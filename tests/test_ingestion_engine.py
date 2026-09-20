"""Job Ingestion Engine test suite.

All external HTTP calls are mocked — no live network requests.

Coverage:
  A. Connector normalisation (Greenhouse, Lever, Ashby, ReliefWeb, UN Careers)
  B. Zambia / remote location classification
  C. Job category classification
  D. HTML sanitisation
  E. URL validation / SSRF protection
  F. Deduplication helpers
  G. Expiry / lifecycle state machine (unit tests)
  H. Sync orchestrator — source isolation, idempotence, failed source
  I. Jobs API — authentication, route ordering, pagination, filtering
  J. Active-only listing (closed/expired excluded)
  K. JobRead regression (posted_at → date_posted)
"""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal HTTP response builders
# ---------------------------------------------------------------------------

def _json_response(payload: Any, status: int = 200):
    import httpx
    return httpx.Response(
        status_code=status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://example.com"),
    )


def _text_response(text: str, status: int = 200):
    import httpx
    return httpx.Response(
        status_code=status,
        content=text.encode("utf-8"),
        headers={"content-type": "text/xml; charset=utf-8"},
        request=httpx.Request("GET", "https://example.com"),
    )


def _rss_xml(items: list[dict]) -> str:
    channel = ET.Element("channel")
    for d in items:
        item_el = ET.SubElement(channel, "item")
        for k, v in d.items():
            el = ET.SubElement(item_el, k)
            el.text = v
    root = ET.Element("rss", version="2.0")
    root.append(channel)
    return '<?xml version="1.0"?>' + ET.tostring(root, encoding="unicode")


# ===========================================================================
# A. Connector normalisation
# ===========================================================================


class TestGreenhouseNormalisation:
    @pytest.mark.asyncio
    async def test_full_valid_job(self):
        from backend.ingestion.sources.greenhouse import GreenhouseConnector
        c = GreenhouseConnector(boards=["stripe"])
        raw = {
            "_board_slug": "stripe",
            "id": "123456",
            "title": "Senior Backend Engineer",
            "company": {"name": "Stripe"},
            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/123456",
            "location": {"name": "Remote"},
            "departments": [{"name": "Engineering"}],
            "content": "<p>Build great APIs.</p>",
            "updated_at": "2026-06-01T10:00:00Z",
        }
        job = await c.normalize_job(raw)
        assert job is not None
        assert job.title == "Senior Backend Engineer"
        assert job.company == "Stripe"
        assert job.external_id == "stripe:123456"
        assert job.source_name == "greenhouse"
        assert job.application_url == "https://boards.greenhouse.io/stripe/jobs/123456"
        assert job.department == "Engineering"
        assert job.date_posted is not None

    @pytest.mark.asyncio
    async def test_missing_title_returns_none(self):
        from backend.ingestion.sources.greenhouse import GreenhouseConnector
        c = GreenhouseConnector(boards=["co"])
        raw = {"_board_slug": "co", "id": "1", "title": "", "absolute_url": "https://x.com/j/1"}
        assert await c.normalize_job(raw) is None

    @pytest.mark.asyncio
    async def test_missing_id_returns_none(self):
        from backend.ingestion.sources.greenhouse import GreenhouseConnector
        c = GreenhouseConnector(boards=["co"])
        raw = {"_board_slug": "co", "id": "", "title": "Dev", "absolute_url": "https://x.com/j/1"}
        assert await c.normalize_job(raw) is None

    @pytest.mark.asyncio
    async def test_unsafe_url_rejected(self):
        from backend.ingestion.sources.greenhouse import GreenhouseConnector
        c = GreenhouseConnector(boards=["co"])
        raw = {"_board_slug": "co", "id": "1", "title": "Dev",
               "absolute_url": "javascript:alert(1)"}
        assert await c.normalize_job(raw) is None

    @pytest.mark.asyncio
    async def test_no_boards_returns_empty(self):
        from backend.ingestion.sources.greenhouse import GreenhouseConnector
        assert await GreenhouseConnector(boards=[]).fetch_jobs() == []

    @pytest.mark.asyncio
    async def test_fetch_passes_content_param(self):
        from backend.ingestion.sources.greenhouse import GreenhouseConnector
        captured: dict = {}

        async def mock_get_json(url, *, params=None, client=None):
            captured.update(params or {})
            return {"jobs": []}

        c = GreenhouseConnector(boards=["co"])
        with patch("backend.ingestion.sources.greenhouse.get_json", new=mock_get_json):
            await c.fetch_jobs()
        assert captured.get("content") == "true"


class TestLeverNormalisation:
    @pytest.mark.asyncio
    async def test_full_valid_posting(self):
        from backend.ingestion.sources.lever import LeverConnector
        c = LeverConnector(companies=["airbnb"])
        raw = {
            "_company": "airbnb",
            "id": "posting-abc",
            "text": "Frontend Engineer",
            "hostedUrl": "https://jobs.lever.co/airbnb/posting-abc",
            "categories": {"location": "Remote", "team": "Design"},
            "createdAt": 1700000000000,
        }
        job = await c.normalize_job(raw)
        assert job is not None
        assert job.title == "Frontend Engineer"
        assert job.external_id == "airbnb:posting-abc"
        assert job.source_name == "lever"
        assert "lever.co" in job.application_url
        assert job.department == "Design"
        assert job.date_posted is not None

    @pytest.mark.asyncio
    async def test_missing_title_returns_none(self):
        from backend.ingestion.sources.lever import LeverConnector
        c = LeverConnector(companies=["co"])
        raw = {"_company": "co", "id": "1", "text": "", "hostedUrl": "https://x.com/j/1"}
        assert await c.normalize_job(raw) is None

    @pytest.mark.asyncio
    async def test_optional_team_absent(self):
        from backend.ingestion.sources.lever import LeverConnector
        c = LeverConnector(companies=["co"])
        raw = {"_company": "co", "id": "1", "text": "Dev",
               "hostedUrl": "https://jobs.lever.co/co/1"}
        job = await c.normalize_job(raw)
        assert job is not None
        assert job.department == ""

    @pytest.mark.asyncio
    async def test_no_companies_returns_empty(self):
        from backend.ingestion.sources.lever import LeverConnector
        assert await LeverConnector(companies=[]).fetch_jobs() == []


class TestAshbyNormalisation:
    @pytest.mark.asyncio
    async def test_full_valid_job(self):
        from backend.ingestion.sources.ashby import AshbyConnector
        c = AshbyConnector(orgs=["linear"])
        raw = {
            "_org": "linear",
            "id": "job-xyz",
            "title": "Data Engineer",
            "organizationName": "Linear",
            "jobUrl": "https://jobs.ashbyhq.com/linear/job-xyz",
            "jobLocations": [{"locationStr": "Remote"}],
            "workplaceType": "remote",
            "employmentType": "fulltime",
            "team": "Data",
            "publishedDate": "2026-07-01T00:00:00Z",
        }
        job = await c.normalize_job(raw)
        assert job is not None
        assert job.title == "Data Engineer"
        assert job.external_id == "linear:job-xyz"
        assert job.source_name == "ashby"
        assert job.employment_type == "full_time"
        assert job.department == "Data"

    @pytest.mark.asyncio
    async def test_compensation_optional(self):
        from backend.ingestion.sources.ashby import AshbyConnector
        c = AshbyConnector(orgs=["co"])
        raw = {
            "_org": "co", "id": "1", "title": "Eng", "organizationName": "Co",
            "jobUrl": "https://jobs.ashbyhq.com/co/1",
            "compensation": {"minValue": 5000, "maxValue": 8000, "currency": "USD"},
        }
        job = await c.normalize_job(raw)
        assert job is not None
        assert job.salary_min == 5000.0
        assert job.salary_max == 8000.0
        assert job.currency == "USD"

    @pytest.mark.asyncio
    async def test_no_orgs_returns_empty(self):
        from backend.ingestion.sources.ashby import AshbyConnector
        assert await AshbyConnector(orgs=[]).fetch_jobs() == []


class TestReliefWebNormalisation:
    @pytest.mark.asyncio
    async def test_full_valid_job(self):
        from backend.ingestion.sources.reliefweb import ReliefWebConnector
        c = ReliefWebConnector()
        raw = {
            "id": "rw-999",
            "fields": {
                "id": "rw-999",
                "title": "Programme Officer",
                "source": [{"name": "UNICEF"}],
                "country": [{"name": "Zambia"}],
                "city": "Lusaka",
                "url": "https://reliefweb.int/job/999",
                "how_to_apply": "<p>Apply at https://apply.unicef.org/999</p>",
                "body": "<p>Support programme delivery.</p>",
                "date": {"created": "2026-06-01T12:00:00"},
                "job_closing_date": "2026-08-31",
            },
        }
        job = await c.normalize_job(raw)
        assert job is not None
        assert job.title == "Programme Officer"
        assert job.company == "UNICEF"
        assert job.country == "Zambia"
        assert job.source_name == "reliefweb"
        assert job.external_id == "rw-999"
        assert job.deadline is not None

    @pytest.mark.asyncio
    async def test_missing_id_returns_none(self):
        from backend.ingestion.sources.reliefweb import ReliefWebConnector
        c = ReliefWebConnector()
        raw = {"fields": {"title": "A job", "url": "https://x.com/j/1"}}
        assert await c.normalize_job(raw) is None


class TestUNCareersNormalisation:
    def _rss(self, items):
        return _rss_xml(items)

    @pytest.mark.asyncio
    async def test_full_valid_item(self):
        from backend.ingestion.sources.un_careers import UNCareersConnector
        c = UNCareersConnector()
        raw = {
            "title": "Economic Affairs Officer, P4, New York",
            "link": "https://careers.un.org/job/12345",
            "guid": "un-careers-12345",
            "description": "Duty Station: New York",
            "pubDate": "Mon, 01 Jun 2026 00:00:00 +0000",
        }
        job = await c.normalize_job(raw)
        assert job is not None
        assert "Economic Affairs Officer" in job.title
        assert job.external_id == "un-careers-12345"
        assert job.source_name == "un_careers"
        assert job.application_url == "https://careers.un.org/job/12345"
        assert job.date_posted is not None

    @pytest.mark.asyncio
    async def test_duplicate_guid_deduplicated_within_feed(self):
        from backend.ingestion.sources.un_careers import UNCareersConnector
        c = UNCareersConnector()
        rss = self._rss([
            {"title": "Job A", "link": "https://careers.un.org/job/1",
             "guid": "guid-1", "description": "", "pubDate": "Mon, 01 Jun 2026 00:00:00 +0000"},
            {"title": "Job A duplicate", "link": "https://careers.un.org/job/1",
             "guid": "guid-1", "description": "", "pubDate": "Mon, 01 Jun 2026 00:00:00 +0000"},
        ])
        with patch("backend.ingestion.sources.un_careers.get_text",
                   AsyncMock(return_value=rss)):
            jobs = await c.fetch_jobs()
        assert len(jobs) == 1

    @pytest.mark.asyncio
    async def test_missing_link_and_guid_returns_none(self):
        from backend.ingestion.sources.un_careers import UNCareersConnector
        c = UNCareersConnector()
        raw = {"title": "A job", "link": "", "guid": "", "description": "", "pubDate": ""}
        assert await c.normalize_job(raw) is None


# ===========================================================================
# B. Zambia / remote location classification
# ===========================================================================


class TestZambiaClassifier:
    def _c(self, loc, hint=""):
        from backend.ingestion.classifiers import classify_location
        return classify_location(loc, country_hint=hint)

    def test_lusaka_zambia(self):
        r = self._c("Lusaka, Zambia")
        assert r.country == "Zambia"
        assert r.city == "Lusaka"
        assert r.province == "Lusaka Province"
        assert r.remote_eligibility == "not_remote"

    def test_ndola_copperbelt(self):
        r = self._c("Ndola, Copperbelt")
        assert r.country == "Zambia"
        assert r.province == "Copperbelt Province"
        assert r.remote_eligibility == "not_remote"

    def test_country_hint_zambia(self):
        r = self._c("Chipata", hint="Zambia")
        assert r.country == "Zambia"
        assert r.remote_eligibility == "not_remote"

    def test_remote_zambia_eligible(self):
        r = self._c("Remote - Zambia")
        assert r.remote_eligibility == "zambia_eligible"
        assert r.country == "Zambia"

    def test_remote_africa_not_zambia(self):
        r = self._c("Remote - Africa")
        assert r.remote_eligibility == "africa_eligible"
        assert r.country != "Zambia"  # Must NOT be Zambia

    def test_remote_global_worldwide(self):
        r = self._c("Remote - Worldwide")
        assert r.remote_eligibility == "global"

    def test_plain_remote_global(self):
        r = self._c("Remote")
        assert r.remote_eligibility == "global"

    def test_us_only_not_zambia_eligible(self):
        r = self._c("Remote - US Only")
        assert r.remote_eligibility == "not_remote"

    def test_eu_only_not_zambia_eligible(self):
        r = self._c("Remote - EU Only")
        assert r.remote_eligibility == "not_remote"

    def test_empty_location_unclear(self):
        r = self._c("")
        assert r.remote_eligibility == "restrictions_unclear"

    def test_africa_remote_is_never_zambia(self):
        """Critical: Africa-eligible must NOT be upgraded to zambia_eligible."""
        r = self._c("Remote - East Africa")
        assert r.remote_eligibility == "africa_eligible"
        assert r.remote_eligibility != "zambia_eligible"


# ===========================================================================
# C. Job category classification
# ===========================================================================


class TestCategoryClassifier:
    def _cat(self, title, desc=""):
        from backend.ingestion.classifiers import classify_category
        return classify_category(title, desc)

    def test_ict_from_title(self):
        assert self._cat("Senior Software Developer") == "ict_technology"

    def test_ngo_from_title(self):
        assert self._cat("Programme Officer, UNICEF") == "ngo_development"

    def test_healthcare_from_title(self):
        assert self._cat("Clinical Nurse Specialist") == "healthcare"

    def test_accounting_from_title(self):
        assert self._cat("Senior Accountant") == "accounting_finance"

    def test_education_from_title(self):
        assert self._cat("Primary School Teacher") == "education"

    def test_internship_from_title(self):
        assert self._cat("Graduate Trainee Engineer") == "internships_graduate"

    def test_fallback_to_description(self):
        cat = self._cat("Staff Member", "Manage financial audits and reporting.")
        assert cat == "accounting_finance"

    def test_other_when_no_match(self):
        # Use a title with no matching category keyword
        assert self._cat("Ethnobotanical Forager") == "other"


# ===========================================================================
# D. HTML sanitisation
# ===========================================================================


class TestHtmlSanitizer:
    def _s(self, html):
        from backend.ingestion.sanitize import sanitize_html
        return sanitize_html(html)

    def test_strips_script(self):
        out = self._s("<p>Good</p><script>alert(1)</script>")
        assert "<script" not in out
        assert "Good" in out

    def test_strips_onclick(self):
        out = self._s('<p onclick="evil()">Text</p>')
        assert "onclick" not in out
        assert "Text" in out

    def test_preserves_bold(self):
        out = self._s("<strong>Important</strong>")
        assert "<strong>" in out

    def test_strips_iframe(self):
        out = self._s('<iframe src="https://evil.com"></iframe>Safe')
        assert "<iframe" not in out
        assert "Safe" in out

    def test_empty_input(self):
        from backend.ingestion.sanitize import sanitize_html
        assert sanitize_html("") == ""
        assert sanitize_html(None) == ""  # type: ignore[arg-type]


# ===========================================================================
# E. URL validation / SSRF protection
# ===========================================================================


class TestUrlValidation:
    def _v(self, url):
        from backend.ingestion.sanitize import validate_application_url
        return validate_application_url(url)

    def test_https_accepted(self):
        assert self._v("https://example.com/jobs/1") is not None

    def test_http_accepted(self):
        assert self._v("http://example.com/jobs/1") is not None

    def test_javascript_rejected(self):
        assert self._v("javascript:alert(1)") is None

    def test_data_rejected(self):
        assert self._v("data:text/html,<h1>x</h1>") is None

    def test_file_rejected(self):
        assert self._v("file:///etc/passwd") is None

    def test_empty_rejected(self):
        assert self._v("") is None
        assert self._v(None) is None  # type: ignore[arg-type]

    def test_http_client_rejects_unsafe(self):
        from backend.ingestion.http_client import _validate_url
        with pytest.raises(ValueError):
            _validate_url("javascript:alert(1)")
        with pytest.raises(ValueError):
            _validate_url("data:text/html,x")


# ===========================================================================
# F. Deduplication helpers
# ===========================================================================


class TestDeduplicationHelpers:
    def test_same_source_external_id_gives_same_key(self):
        from backend.ingestion.dedup import make_canonical_key
        k1 = make_canonical_key("greenhouse", "stripe:123", "https://x.com/a")
        k2 = make_canonical_key("greenhouse", "stripe:123", "https://different.com/b")
        assert k1 == k2  # source+external_id is the priority

    def test_different_external_ids_differ(self):
        from backend.ingestion.dedup import make_canonical_key
        k1 = make_canonical_key("greenhouse", "stripe:123", "https://x.com")
        k2 = make_canonical_key("greenhouse", "stripe:999", "https://x.com")
        assert k1 != k2

    def test_url_fallback_when_no_external_id(self):
        from backend.ingestion.dedup import make_canonical_key
        k1 = make_canonical_key("", "", "https://example.com/jobs/1")
        k2 = make_canonical_key("", "", "https://example.com/jobs/1")
        assert k1 == k2

    def test_url_strips_tracking_params(self):
        from backend.ingestion.dedup import make_url_key
        k1 = make_url_key("https://example.com/job?id=1&utm_source=gh&utm_medium=x")
        k2 = make_url_key("https://example.com/job?id=1")
        assert k1 == k2

    def test_url_sorts_query_params(self):
        from backend.ingestion.dedup import make_url_key
        k1 = make_url_key("https://x.com/j?b=2&a=1")
        k2 = make_url_key("https://x.com/j?a=1&b=2")
        assert k1 == k2

    def test_canonical_key_is_32_hex(self):
        from backend.ingestion.dedup import make_canonical_key
        k = make_canonical_key("reliefweb", "rw-999", "https://reliefweb.int/j/999")
        assert len(k) == 32
        assert all(c in "0123456789abcdef" for c in k)


# ===========================================================================
# G. Expiry / lifecycle (unit tests on expiry.py)
# ===========================================================================


class TestExpiryUnit:
    def _s(self, **kw):
        from backend.ingestion.expiry import compute_next_status
        defaults = dict(
            current_status="active", seen_in_latest_sync=True,
            sync_was_complete=True, deadline=None,
            source_says_closed=False, missed_syncs=0,
        )
        defaults.update(kw)
        return compute_next_status(**defaults)

    def test_seen_active_stays_active(self):
        status, missed = self._s()
        assert status == "active"
        assert missed == 0

    def test_missed_once_possibly_closed(self):
        status, missed = self._s(seen_in_latest_sync=False, missed_syncs=0)
        assert status == "possibly_closed"
        assert missed == 1

    def test_missed_twice_closed(self):
        status, missed = self._s(
            current_status="possibly_closed", seen_in_latest_sync=False, missed_syncs=1
        )
        assert status == "closed"
        assert missed == 2

    def test_failed_sync_preserves_status(self):
        status, missed = self._s(seen_in_latest_sync=False, sync_was_complete=False)
        assert status == "active"  # unchanged
        assert missed == 0

    def test_partial_sync_preserves_possibly_closed(self):
        status, missed = self._s(
            current_status="possibly_closed", seen_in_latest_sync=False,
            sync_was_complete=False, missed_syncs=1,
        )
        assert status == "possibly_closed"

    def test_past_deadline_expired(self):
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        status, _ = self._s(deadline=past)
        assert status == "expired"

    def test_future_deadline_active(self):
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        status, _ = self._s(deadline=future)
        assert status == "active"

    def test_source_says_closed(self):
        status, _ = self._s(source_says_closed=True)
        assert status == "closed"

    def test_removed_is_sticky(self):
        status, _ = self._s(current_status="removed", seen_in_latest_sync=True)
        assert status == "removed"

    def test_should_deactivate(self):
        from backend.ingestion.expiry import should_deactivate
        assert should_deactivate("closed") is True
        assert should_deactivate("expired") is True
        assert should_deactivate("removed") is True
        assert should_deactivate("active") is False
        assert should_deactivate("possibly_closed") is False


# ===========================================================================
# H. Sync orchestrator — source isolation and idempotence
# (uses db_session fixture from conftest.py)
# ===========================================================================


def _make_test_connector(name: str, jobs_to_return=None, should_fail=False):
    """Build a minimal connector for use in sync tests."""
    from backend.ingestion.base import JobSourceConnector
    from backend.ingestion.schema import NormalizedJob

    jobs = jobs_to_return or []

    class _Connector(JobSourceConnector):
        source_name = name
        source_type = "api"
        attribution_template = f"Via {name}"

        async def fetch_jobs(self):
            if should_fail:
                raise RuntimeError(f"Simulated failure for {name}")
            return list(jobs)

        async def normalize_job(self, raw):
            return None

    return _Connector()


@pytest.mark.asyncio
async def test_failed_source_does_not_abort_others(db_session):
    """Source B failure must not prevent A or C from running."""
    from backend.ingestion.sync import run_sync
    from backend.ingestion.schema import NormalizedJob

    job_a = NormalizedJob(
        title="Job A", company="CoA",
        application_url="https://co-a.example.com/jobs/1",
        source_name="greenhouse",  # official — passes permission check
        external_id="a-1",
    )
    job_c = NormalizedJob(
        title="Job C", company="CoC",
        application_url="https://co-c.example.com/jobs/3",
        source_name="reliefweb",   # official
        external_id="c-3",
    )

    conn_a = _make_test_connector("greenhouse", [job_a])
    conn_b = _make_test_connector("lever", [], should_fail=True)
    conn_c = _make_test_connector("reliefweb", [job_c])

    with patch("backend.ingestion.sync._build_connectors",
               return_value=[conn_a, conn_b, conn_c]):
        result = await run_sync(db_session)

    assert len(result.sources) == 3
    src_a = next(s for s in result.sources if s.source_name == "greenhouse")
    src_b = next(s for s in result.sources if s.source_name == "lever")
    src_c = next(s for s in result.sources if s.source_name == "reliefweb")

    assert src_b.status == "error"
    assert src_a.status == "ok"
    assert src_a.created == 1
    assert src_c.status == "ok"
    assert src_c.created == 1


@pytest.mark.asyncio
async def test_failed_sync_does_not_close_jobs(db_session):
    """A failed source fetch must NOT trigger disappearance-based closure."""
    from backend.ingestion.sync import run_sync
    from backend.ingestion.schema import NormalizedJob
    from sqlalchemy import select
    from backend.models.job import Job

    # First run: create 1 job
    job_norm = NormalizedJob(
        title="Stable Job", company="StableCo",
        application_url="https://stableco.example.com/jobs/99",
        source_name="greenhouse",
        external_id="s-99",
    )
    conn_ok = _make_test_connector("greenhouse", [job_norm])
    with patch("backend.ingestion.sync._build_connectors", return_value=[conn_ok]):
        r1 = await run_sync(db_session)
    assert r1.sources[0].created == 1

    # Second run: connector fails — job must remain active
    conn_fail = _make_test_connector("greenhouse", [], should_fail=True)
    with patch("backend.ingestion.sync._build_connectors", return_value=[conn_fail]):
        r2 = await run_sync(db_session)
    assert r2.sources[0].status == "error"

    # Job is still active — failed sync must not mark possibly_closed
    result = await db_session.execute(
        select(Job).where(Job.source_name == "greenhouse")
    )
    jobs = result.scalars().all()
    assert len(jobs) == 1
    assert jobs[0].is_active is True
    job_status = str(jobs[0].ingestion_status or "active")
    assert job_status not in ("possibly_closed", "closed")


@pytest.mark.asyncio
async def test_idempotent_sync(db_session):
    """Running the same payload twice creates 1 job, not 2."""
    from backend.ingestion.sync import run_sync
    from backend.ingestion.schema import NormalizedJob
    from sqlalchemy import select, func
    from backend.models.job import Job

    job_norm = NormalizedJob(
        title="Idempotent Job", company="IdempoCo",
        application_url="https://idempoco.example.com/jobs/7",
        source_name="greenhouse",
        external_id="idemp-7",
    )

    def _conn():
        return _make_test_connector("greenhouse", [job_norm])

    with patch("backend.ingestion.sync._build_connectors", return_value=[_conn()]):
        r1 = await run_sync(db_session)
    with patch("backend.ingestion.sync._build_connectors", return_value=[_conn()]):
        r2 = await run_sync(db_session)

    # First run: 1 created
    assert r1.sources[0].created == 1
    # Second run: 0 created, 1 updated (same job seen again)
    assert r2.sources[0].created == 0
    assert r2.sources[0].updated == 1

    # Exactly one job in DB
    count = await db_session.execute(
        select(func.count()).where(Job.source_name == "greenhouse")
    )
    assert count.scalar() == 1


@pytest.mark.asyncio
async def test_ingestion_disabled_returns_empty(db_session):
    """JOB_INGESTION_ENABLED=false → sync returns immediately with no sources."""
    from backend.ingestion.sync import run_sync
    with patch("backend.ingestion.sync.get_settings") as m:
        m.return_value = MagicMock(JOB_INGESTION_ENABLED=False)
        result = await run_sync(db_session)
    assert result.sources == []
    assert result.completed_at is not None


# ===========================================================================
# I. Jobs API — authentication, route ordering, pagination, filtering
# ===========================================================================

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
JOBS_URL = "/api/v1/jobs"


def _token(client) -> str:
    email = f"eng_{uuid.uuid4().hex[:8]}@test.com"
    pw = "Pass1234!"
    r = client.post(REGISTER_URL, json={"email": email, "password": pw, "full_name": "T"})
    assert r.status_code == 201
    r2 = client.post(LOGIN_URL, json={"email": email, "password": pw})
    assert r2.status_code == 200
    return r2.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestJobsAPIAuthentication:
    def test_list_unauthenticated(self, client):
        assert client.get(JOBS_URL).status_code == 401

    def test_search_unauthenticated(self, client):
        assert client.get(JOBS_URL + "/search").status_code == 401

    def test_filters_unauthenticated(self, client):
        assert client.get(JOBS_URL + "/filters").status_code == 401

    def test_sync_unauthenticated(self, client):
        assert client.post(JOBS_URL + "/sync").status_code == 401

    def test_sources_unauthenticated(self, client):
        assert client.get(JOBS_URL + "/sources").status_code == 401

    def test_sync_status_unauthenticated(self, client):
        assert client.get(JOBS_URL + "/sync/status").status_code == 401

    def test_recent_unauthenticated(self, client):
        assert client.get(JOBS_URL + "/recent").status_code == 401

    def test_list_authenticated(self, client):
        tok = _token(client)
        r = client.get(JOBS_URL, headers=_auth(tok))
        assert r.status_code == 200
        d = r.json()
        assert "items" in d and "total" in d

    def test_search_authenticated(self, client):
        tok = _token(client)
        r = client.get(JOBS_URL + "/search?q=python", headers=_auth(tok))
        assert r.status_code == 200

    def test_filters_authenticated(self, client):
        tok = _token(client)
        r = client.get(JOBS_URL + "/filters", headers=_auth(tok))
        assert r.status_code == 200

    def test_sources_authenticated(self, client):
        tok = _token(client)
        r = client.get(JOBS_URL + "/sources", headers=_auth(tok))
        assert r.status_code == 200

    def test_sync_status_authenticated(self, client):
        tok = _token(client)
        r = client.get(JOBS_URL + "/sync/status", headers=_auth(tok))
        assert r.status_code == 200


class TestRouteOrdering:
    """These routes must NOT be captured by /{job_id} as UUID parse targets."""

    def _tok(self, client):
        return _token(client)

    def test_search_not_captured_as_job_id(self, client):
        r = client.get(JOBS_URL + "/search", headers=_auth(self._tok(client)))
        # 422 = FastAPI tried to parse "search" as UUID → route ordering broken
        assert r.status_code == 200, f"Got {r.status_code}: route ordering may be broken"

    def test_recent_not_captured_as_job_id(self, client):
        r = client.get(JOBS_URL + "/recent", headers=_auth(self._tok(client)))
        assert r.status_code == 200

    def test_filters_not_captured_as_job_id(self, client):
        r = client.get(JOBS_URL + "/filters", headers=_auth(self._tok(client)))
        assert r.status_code == 200

    def test_sources_not_captured_as_job_id(self, client):
        r = client.get(JOBS_URL + "/sources", headers=_auth(self._tok(client)))
        assert r.status_code == 200

    def test_sync_status_not_captured_as_job_id(self, client):
        r = client.get(JOBS_URL + "/sync/status", headers=_auth(self._tok(client)))
        assert r.status_code == 200

    def test_real_uuid_not_found_returns_404(self, client):
        tok = self._tok(client)
        r = client.get(JOBS_URL + f"/{uuid.uuid4()}", headers=_auth(tok))
        assert r.status_code == 404

    def test_non_uuid_returns_422_not_200(self, client):
        tok = self._tok(client)
        # A random non-UUID, non-named path should fail UUID validation
        r = client.get(JOBS_URL + "/definitely-not-a-uuid-12345", headers=_auth(tok))
        assert r.status_code == 422


class TestJobsAPIPagination:
    def test_default_limit_is_20(self, client):
        tok = _token(client)
        r = client.get(JOBS_URL, headers=_auth(tok))
        assert r.status_code == 200
        assert r.json()["limit"] == 20

    def test_limit_100_accepted(self, client):
        tok = _token(client)
        r = client.get(JOBS_URL + "?limit=100", headers=_auth(tok))
        assert r.status_code == 200

    def test_limit_101_rejected(self, client):
        tok = _token(client)
        r = client.get(JOBS_URL + "?limit=101", headers=_auth(tok))
        assert r.status_code == 422

    def test_search_limit_100_accepted(self, client):
        tok = _token(client)
        r = client.get(JOBS_URL + "/search?limit=100", headers=_auth(tok))
        assert r.status_code == 200

    def test_search_limit_101_rejected(self, client):
        tok = _token(client)
        r = client.get(JOBS_URL + "/search?limit=101", headers=_auth(tok))
        assert r.status_code == 422


# ===========================================================================
# J. Active-only listing — closed/expired jobs excluded
# ===========================================================================


@pytest.mark.asyncio
async def test_active_only_listing(db_session, client):
    """Closed and expired jobs must NOT appear in normal job listings."""
    from backend.ingestion.sync import run_sync
    from backend.ingestion.schema import NormalizedJob
    from sqlalchemy import select
    from backend.models.job import Job
    from backend.models.enums import IngestionStatus
    import datetime

    jobs_to_ingest = [
        NormalizedJob(
            title="Active Job",
            company="ActiveCo",
            application_url="https://activeco.example.com/j/1",
            source_name="greenhouse",
            external_id="act-1",
        ),
        NormalizedJob(
            title="Will Be Closed",
            company="ClosedCo",
            application_url="https://closedco.example.com/j/2",
            source_name="greenhouse",
            external_id="cls-2",
        ),
    ]

    conn = _make_test_connector("greenhouse", jobs_to_ingest)
    with patch("backend.ingestion.sync._build_connectors", return_value=[conn]):
        await run_sync(db_session)

    # Manually mark the second job as closed
    result = await db_session.execute(
        select(Job).where(Job.source_name == "greenhouse")
    )
    all_jobs = result.scalars().all()
    assert len(all_jobs) == 2

    for j in all_jobs:
        if "Closed" in j.title:
            j.is_active = False
            j.ingestion_status = IngestionStatus.CLOSED
    await db_session.commit()

    # Now query the API
    tok = _token(client)
    r = client.get(JOBS_URL, headers=_auth(tok))
    assert r.status_code == 200
    items = r.json()["items"]
    titles = [item["title"] for item in items]
    assert "Active Job" in titles
    assert "Will Be Closed" not in titles


# ===========================================================================
# K. JobRead regression — posted_at → date_posted
# ===========================================================================


def test_jobread_posted_at_alias():
    """ORM field posted_at must serialize as date_posted in API responses."""
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789abcdef")

    from backend.schemas.job import JobRead
    from datetime import datetime, timezone

    ts = datetime(2026, 6, 15, tzinfo=timezone.utc)
    mock_job = SimpleNamespace(
        id=uuid.uuid4(), title="Analyst", description=None, company_name="Co",
        company_logo_url=None, location="Lusaka", location_type="onsite",
        employment_type="full_time", experience_level="mid",
        salary_min=None, salary_max=None, salary_currency="USD",
        is_active=True, source="external", external_url="https://x.com/1",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        posted_at=ts,
        country="Zambia", province="Lusaka Province", city="Lusaka",
        remote_eligibility="not_remote", category="ict_technology",
        source_name="greenhouse", attribution="Via GH",
        deadline=None, last_verified=None, ingestion_status="active",
    )

    read = JobRead.model_validate(mock_job)

    # Field reads from ORM attribute posted_at
    assert read.posted_at == ts

    # Serialized as date_posted
    d = read.model_dump(by_alias=True)
    assert "date_posted" in d
    assert d["date_posted"] == ts
    assert "posted_at" not in d

    # OpenAPI schema exposes date_posted
    schema = JobRead.model_json_schema()
    props = list(schema.get("properties", {}).keys())
    assert "date_posted" in props
    assert "posted_at" not in props
