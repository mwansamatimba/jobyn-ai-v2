"""One-page Techmap retrieval into the isolated C5 experiment store."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from backend.core.config import get_settings
from backend.ingestion.c5 import C5ImportResult, run_c5_experiment_import
from backend.ingestion.config import C5ExperimentConfig
from backend.ingestion.processing import classify_category, validate_url
from backend.ingestion.retrieval import (
    C5HttpClient,
    C5HttpTransport,
    C5RequestGovernor,
    C5RetrievalError,
    C5TransportError,
    UrllibC5Transport,
)
from backend.ingestion.store import C5ExperimentStore

TECHMAP_ENDPOINT = (
    "https://daily-international-job-postings.p.rapidapi.com/api/v2/jobs/search"
)
TECHMAP_DOMAIN = ("daily-international-job-postings.p.rapidapi.com",)
TECHMAP_SOURCE_NAME = "techmap_daily_international"
TECHMAP_PROVIDER = "Techmap Daily International Job Postings"
TECHMAP_MAX_REQUESTS = 1
TECHMAP_MAX_RECORDS = 10
TECHMAP_RETRIEVAL_METHOD = "rapidapi_techmap"


class TechmapError(ValueError):
    """Sanitized Techmap error with a stable classification code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TechmapConfig:
    api_key: str | None = field(default=None, repr=False)
    country_code: str = "zm"
    page_size: int = 10
    max_pages: int = 1
    is_duplicate: bool = False

    def validate(self) -> TechmapConfig:
        if not self.api_key or not self.api_key.strip():
            raise TechmapError("missing_api_key", "TECHMAP_API_KEY is required")
        if self.country_code.lower() != "zm":
            raise TechmapError("invalid_country", "Techmap C5 retrieval is limited to Zambia")
        if self.page_size != TECHMAP_MAX_RECORDS:
            raise TechmapError("invalid_page_size", "Techmap first-run page size must be 10")
        if self.max_pages != 1:
            raise TechmapError(
                "pagination_disabled", "Techmap first-run mode permits one page"
            )
        if self.is_duplicate:
            raise TechmapError(
                "invalid_duplicate_mode",
                "Techmap first-run mode requires isDuplicate=false",
            )
        return self

    @classmethod
    def from_environment(cls) -> TechmapConfig:
        try:
            page_size = int(os.environ.get("TECHMAP_PAGE_SIZE", "10"))
            max_pages = int(os.environ.get("TECHMAP_MAX_PAGES", "1"))
        except ValueError as error:
            raise TechmapError(
                "invalid_configuration", "Techmap paging configuration is invalid"
            ) from error
        duplicate_value = os.environ.get("TECHMAP_IS_DUPLICATE", "false").lower()
        if duplicate_value not in {"true", "false"}:
            raise TechmapError(
                "invalid_configuration", "TECHMAP_IS_DUPLICATE must be true or false"
            )
        config = cls(
            api_key=os.environ.get("TECHMAP_API_KEY"),
            country_code=os.environ.get("TECHMAP_COUNTRY_CODE", "zm"),
            page_size=page_size,
            max_pages=max_pages,
            is_duplicate=duplicate_value == "true",
        )
        return config.validate()


@dataclass(frozen=True, slots=True)
class TechmapRunResult:
    http_status: int
    total_count: int | None
    returned_count: int
    processed_count: int
    accepted: int
    rejected: int
    result: C5ImportResult

    @property
    def store(self) -> C5ExperimentStore:
        return self.result.store


def _text(value: object) -> str:
    if isinstance(value, str | int | float):
        return str(value).strip()
    if isinstance(value, Mapping):
        for key in ("name", "value", "text", "label"):
            text = _text(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return "; ".join(text for item in value if (text := _text(item)))
    return ""


def _url(record: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, Mapping):
            value = value.get("url") or value.get("href") or value.get("link")
        candidate = validate_url(_text(value))
        if candidate:
            return candidate
    return ""


def _structured_country(record: Mapping[str, object]) -> tuple[str, bool]:
    code = _text(record.get("countryCode") or record.get("country_code")).lower()
    country_name = _text(record.get("country") or record.get("countryName"))
    if code == "zm" or (not code and country_name.casefold() in {"zambia", "republic of zambia"}):
        return country_name or "Zambia", True
    if country_name:
        return country_name, False
    return code, False


def _location(record: Mapping[str, object]) -> tuple[str, str, str]:
    city = _text(record.get("city"))
    workplace = _text(record.get("workPlace") or record.get("workplace"))
    province = _text(record.get("province") or record.get("region"))
    components: list[str] = []
    for component in (workplace, city, province):
        if component and component.casefold() not in {part.casefold() for part in components}:
            components.append(component)
    return ", ".join(components), city, province


def _requirements(record: Mapping[str, object]) -> str:
    sections: list[str] = []
    for field_name in ("skills", "qualifications", "requirements"):
        value = _text(record.get(field_name))
        if value and value.casefold() not in {section.casefold() for section in sections}:
            sections.append(value)
    return "\n".join(sections)


def _description(record: Mapping[str, object]) -> str:
    for key in ("description", "fullPostingData", "postingData", "fullPosting"):
        value = record.get(key)
        if isinstance(value, Mapping):
            for nested in ("description", "body", "content", "text"):
                text = _text(value.get(nested))
                if text:
                    return text
        text = _text(value)
        if text:
            return text
    return ""


def _payload(
    record: Mapping[str, object],
    *,
    experiment_id: str,
    retrieval_run_id: str,
    retrieved_at: str,
    http_status: int,
) -> tuple[dict[str, object], bool]:
    country, is_zambia = _structured_country(record)
    location, city, province = _location(record)
    title = _text(record.get("title"))
    company = _text(record.get("company") or record.get("companyName"))
    description = _description(record)
    application_url = _url(
        record,
        "applicationUrl",
        "application_url",
        "applyUrl",
        "applyLink",
    )
    source_url = _url(
        record,
        "sourceUrl",
        "source_url",
        "originalUrl",
        "jobUrl",
        "url",
        "link",
    )
    portal = _text(record.get("portal"))
    original_source = _text(record.get("source"))
    date_created = _text(record.get("dateCreated") or record.get("date_created"))
    date_active = _text(record.get("dateActive") or record.get("date_active"))
    date_expired = _text(record.get("dateExpired") or record.get("date_expired"))
    skills = _requirements(record)
    attribution = "Source: Techmap Daily International"
    if portal or original_source:
        attribution += " (aggregated"
        if portal:
            attribution += f" via {portal}"
        if original_source:
            attribution += f"; original source {original_source}"
        attribution += ")"
    raw: dict[str, object] = {
        "techmap_country_code": _text(record.get("countryCode") or record.get("country_code")),
        "techmap_portal": portal,
        "techmap_original_source": original_source,
        "techmap_industry": _text(record.get("industry")),
        "techmap_date_active": date_active,
        "techmap_date_expired": date_expired,
        "techmap_date_created": date_created,
        "techmap_is_duplicate": record.get("isDuplicate"),
        "retrieval_provider": TECHMAP_PROVIDER,
        "experiment_id": experiment_id,
        "retrieval_run_id": retrieval_run_id,
        "retrieval_timestamp": retrieved_at,
        "http_status": http_status,
    }
    return (
        {
            "title": title,
            "company": company,
            "location": location,
            "country": country,
            "province": province,
            "city": city,
            "remote_eligibility": _text(record.get("remoteEligibility")),
            "description": description,
            "requirements": skills,
            "source_name": TECHMAP_SOURCE_NAME,
            "source_url": source_url,
            "application_url": application_url,
            "external_id": _text(
                record.get("id") or record.get("jobId") or record.get("externalId")
            ),
            "posted_at": date_created,
            "deadline": "",
            "category": classify_category(title, f"{description} {skills}"),
            "attribution": attribution,
            "techmap_provenance": raw,
        },
        is_zambia,
    )


def _safe_store(config: C5ExperimentConfig, store: C5ExperimentStore) -> None:
    expected_path = config.sqlite_path
    database_row = store.connection.execute("PRAGMA database_list").fetchone()
    actual_path = (
        Path(database_row["file"]).resolve()
        if database_row and database_row["file"]
        else None
    )
    if expected_path is None or actual_path != expected_path:
        raise TechmapError("database_isolation", "Techmap store does not match isolated C5 SQLite")

    production_url = get_settings().DATABASE_URL
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if production_url.startswith(prefix):
            production_path = Path(production_url[len(prefix) :]).resolve()
            if actual_path == production_path:
                raise TechmapError(
                    "database_isolation",
                    "Techmap C5 database must not be the application database",
                )


def _total_count(body: Mapping[str, object]) -> int | None:
    value = body.get("totalCount", body.get("total_count"))
    if isinstance(value, int) and value >= 0:
        return value
    return None


class TechmapProvider:
    def __init__(
        self,
        *,
        config: TechmapConfig,
        transport: C5HttpTransport | None = None,
    ) -> None:
        self.config = config.validate()
        self.transport = transport or UrllibC5Transport(
            {
                "X-RapidAPI-Key": self.config.api_key or "",
                "X-RapidAPI-Host": TECHMAP_DOMAIN[0],
            }
        )

    async def search(
        self,
        c5_config: C5ExperimentConfig,
        *,
        retrieval_run_id: str,
        store: C5ExperimentStore | None = None,
    ) -> TechmapRunResult:
        c5_config.validate()
        db = store or C5ExperimentStore(c5_config.sqlite_path)
        _safe_store(c5_config, db)
        request_url = TECHMAP_ENDPOINT + "?" + urlencode(
            {
                "countryCode": "zm",
                "isDuplicate": "false",
                "page": "1",
                "pageSize": str(TECHMAP_MAX_RECORDS),
            }
        )
        client = C5HttpClient(
            C5RequestGovernor(
                db,
                c5_config.experiment_id or "",
                maximum_requests=TECHMAP_MAX_REQUESTS,
            ),
            transport=self.transport,
            source_name=TECHMAP_SOURCE_NAME,
            retrieval_query="countryCode=zm",
            provider=TECHMAP_PROVIDER,
            retrieval_method=TECHMAP_RETRIEVAL_METHOD,
        )
        try:
            response = await client.get(request_url, TECHMAP_DOMAIN)
        except C5TransportError as error:
            messages = {
                "request_budget_exhausted": "Techmap C5 request budget is exhausted",
                "response_too_large": "Techmap response exceeded the configured size limit",
                "timeout": "Techmap request timed out",
                "dns_connect_failure": "Techmap DNS or connection failure",
                "url_transport_failure": "Techmap URL transport failure",
                "domain_guard": "Techmap request was blocked by a domain safety guard",
                "unexpected_transport": "Unexpected Techmap transport failure",
            }
            raise TechmapError(
                error.code,
                messages.get(error.code, "Techmap request failed safely"),
            ) from None
        except C5RetrievalError:
            raise TechmapError(
                "guard_failure", "Techmap request was blocked by a C5 safety guard"
            ) from None

        if response.status_code in {401, 403}:
            raise TechmapError(
                "authentication_error",
                f"Techmap rejected credentials (HTTP {response.status_code})",
            )
        if response.status_code == 429:
            raise TechmapError("rate_limited", "Techmap rate-limited the request (HTTP 429)")
        if response.status_code == 400:
            raise TechmapError("bad_request", "Techmap rejected the request (HTTP 400)")
        if response.status_code >= 500:
            raise TechmapError("http_error", f"Techmap returned HTTP {response.status_code}")
        if response.status_code >= 400:
            raise TechmapError("http_error", f"Techmap returned HTTP {response.status_code}")

        try:
            body = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TechmapError("malformed_json", "Techmap returned malformed JSON") from None
        if not isinstance(body, Mapping):
            raise TechmapError("unexpected_response", "Techmap response must be a JSON object")
        records = body.get("result")
        if records is None:
            raise TechmapError("missing_result", "Techmap response is missing result")
        if not isinstance(records, list):
            raise TechmapError("unexpected_response", "Techmap result must be an array")

        retrieved_at = datetime.now(UTC).isoformat()
        raw_records = records
        bounded_records = raw_records[: self.config.page_size]
        mapped_records: list[dict[str, object]] = []
        rejected_country = 0
        for item in bounded_records:
            if not isinstance(item, Mapping):
                rejected_country += 1
                continue
            payload, is_zambia = _payload(
                item,
                experiment_id=c5_config.experiment_id or "",
                retrieval_run_id=retrieval_run_id,
                retrieved_at=retrieved_at,
                http_status=response.status_code,
            )
            if is_zambia:
                mapped_records.append(payload)
            else:
                rejected_country += 1

        imported = run_c5_experiment_import(
            c5_config,
            TECHMAP_SOURCE_NAME,
            mapped_records,
            retrieval_requests=0,
            store=db,
            retrieval_run_id=retrieval_run_id,
            retrieval_query="countryCode=zm",
            retrieval_provider=TECHMAP_PROVIDER,
            http_status=response.status_code,
        )
        cap_rejected = max(0, len(raw_records) - len(bounded_records))
        return TechmapRunResult(
            http_status=response.status_code,
            total_count=_total_count(body),
            returned_count=len(raw_records),
            processed_count=len(bounded_records),
            accepted=imported.accepted,
            rejected=imported.rejected + rejected_country + cap_rejected,
            result=imported,
        )


async def run_techmap_once(
    c5_config: C5ExperimentConfig,
    techmap_config: TechmapConfig,
    *,
    retrieval_run_id: str,
    transport: C5HttpTransport | None = None,
    store: C5ExperimentStore | None = None,
) -> TechmapRunResult:
    """Execute one Techmap search request without retrying or paginating."""

    return await TechmapProvider(
        config=techmap_config,
        transport=transport,
    ).search(
        c5_config,
        retrieval_run_id=retrieval_run_id,
        store=store,
    )
