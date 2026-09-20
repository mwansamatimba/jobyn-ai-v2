"""UN Careers RSS connector.

Official United Nations Careers RSS feed — no authentication required.

Feed URL: https://careers.un.org/lbw/home.aspx?viewtype=rss&lang=en-US

Deduplication: uses GUID (or ``<link>`` as fallback) as external_id.
Repeated RSS synchronization is idempotent because the dedup key is stable.

Attribution: "Via UN Careers (https://careers.un.org)"
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from backend.ingestion.base import ConnectorError, JobSourceConnector
from backend.ingestion.http_client import get_text
from backend.ingestion.sanitize import html_to_text, validate_application_url
from backend.ingestion.schema import NormalizedJob

logger = logging.getLogger(__name__)

_RSS_URL = "https://careers.un.org/lbw/home.aspx?viewtype=rss&lang=en-US"
_SOURCE_NAME = "un_careers"
_ATTRIBUTION = "Via UN Careers (https://careers.un.org)"


class UNCareersConnector(JobSourceConnector):
    """Fetch jobs from the official UN Careers RSS feed."""

    source_name = _SOURCE_NAME
    source_type = "rss"
    attribution_template = _ATTRIBUTION

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client=client)

    async def fetch_jobs(self) -> list[NormalizedJob]:
        try:
            xml_text = await get_text(_RSS_URL, client=self._client)
        except Exception as exc:
            raise ConnectorError(f"UN Careers RSS fetch failed: {exc}") from exc

        items = _parse_rss(xml_text)
        results: list[NormalizedJob] = []
        seen_guids: set[str] = set()

        for item in items:
            job = await self.normalize_job(item)
            if job is None:
                continue
            # Deduplicate within a single feed pull (same GUID appearing twice)
            if job.external_id in seen_guids:
                continue
            seen_guids.add(job.external_id)
            results.append(job)

        logger.info("UN Careers fetched=%d", len(results))
        return results

    async def normalize_job(self, raw: dict[str, Any]) -> NormalizedJob | None:
        title = _clean(raw.get("title"))
        if not title:
            self._log_skip("missing_title", raw)
            return None

        link = _clean(raw.get("link"))
        guid = _clean(raw.get("guid")) or link
        if not guid:
            self._log_skip("missing_guid_and_link", raw)
            return None

        app_url = validate_application_url(link or guid)
        if not app_url:
            self._log_skip("invalid_application_url", raw)
            return None

        # Organisation from title (UN jobs typically include dept)
        # Title format often: "TITLE, LEVEL, ORG (LOCATION)"
        company = "United Nations"
        org_match = re.search(r"\(([^)]+)\)\s*$", title)
        if org_match:
            possible_org = org_match.group(1).strip()
            # Only treat as org if it looks like an acronym or known UN body
            if re.match(r"^[A-Z/\-& ]{2,40}$", possible_org):
                company = possible_org

        description_raw = raw.get("description", "")
        description = html_to_text(description_raw)

        # Location: try to extract from description or title
        location = _extract_location(title, description)

        posted_at = _parse_rfc2822(raw.get("pubDate"))

        # Deadline: sometimes embedded in description as "Deadline: YYYY-MM-DD"
        deadline = _extract_deadline(description)

        return NormalizedJob(
            title=title,
            company=company,
            application_url=app_url,
            source_name=_SOURCE_NAME,
            external_id=guid,
            source_url=app_url,
            attribution=_ATTRIBUTION,
            location=location,
            description=description,
            date_posted=posted_at,
            deadline=deadline,
            raw=raw,
        )


# ---------------------------------------------------------------------------
# RSS parsing (stdlib xml only — no lxml dependency)
# ---------------------------------------------------------------------------

def _parse_rss(xml_text: str) -> list[dict[str, Any]]:
    """Parse RSS 2.0 XML into a list of item dicts."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError as exc:
        logger.error("UN Careers RSS parse error: %s", exc)
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    items: list[dict[str, Any]] = []
    for item_el in channel.findall("item"):
        items.append({
            "title":       _el_text(item_el, "title"),
            "link":        _el_text(item_el, "link"),
            "guid":        _el_text(item_el, "guid"),
            "description": _el_text(item_el, "description"),
            "pubDate":     _el_text(item_el, "pubDate"),
            "category":    _el_text(item_el, "category"),
        })
    return items


def _el_text(parent: ET.Element, tag: str) -> str:
    el = parent.find(tag)
    return (el.text or "").strip() if el is not None else ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(value: Any) -> str:
    if not value:
        return ""
    return str(value).strip()


def _parse_rfc2822(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(str(raw))
    except Exception:
        return None


def _extract_location(title: str, description: str) -> str:
    """Try to extract a location string from title/description."""
    # Title often ends with (Location, Country)
    m = re.search(r"\(([^)]+)\)\s*$", title)
    if m:
        candidate = m.group(1).strip()
        # Skip pure acronyms (likely org, not location)
        if not re.match(r"^[A-Z]{2,6}$", candidate):
            return candidate
    # Look for "Duty Station: X" in description
    m2 = re.search(r"duty\s+station[:\s]+([^\n.]+)", description, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()
    return ""


def _extract_deadline(description: str) -> datetime | None:
    """Extract application deadline from description text."""
    patterns = [
        r"deadline[:\s]+(\d{1,2}\s+\w+\s+\d{4})",
        r"closing date[:\s]+(\d{1,2}\s+\w+\s+\d{4})",
        r"apply by[:\s]+(\d{4}-\d{2}-\d{2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, description, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
    return None
