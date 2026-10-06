"""Guarded, dependency-injected retrieval primitives for C5."""

# The transport protocol intentionally exposes a bounded timeout argument.
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlencode, urljoin, urlparse

from backend.ingestion.c5 import C5ImportResult, run_c5_experiment_import
from backend.ingestion.config import C5ExperimentConfig
from backend.ingestion.processing import classify_category, validate_url
from backend.ingestion.sources import (
    PUBLIC_INSTITUTION_DOMAINS,
    SOURCE_CAPS,
)
from backend.ingestion.store import C5ExperimentStore

MAX_RESPONSE_BYTES = 2_000_000
SCRAPINGDOG_GOOGLE_JOBS_ENDPOINT = "https://api.scrapingdog.com/google_jobs"
SCRAPINGDOG_GOOGLE_JOBS_DOMAIN = ("api.scrapingdog.com",)
SCRAPINGDOG_SUCCESSFUL_REQUEST_CREDIT_COST = 5


class C5RetrievalError(ValueError):
    """Raised when a retrieval hard stop is reached."""


class C5TransportError(C5RetrievalError):
    """Sanitized HTTP-boundary error with a stable classification code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class C5ScrapingdogError(C5RetrievalError):
    """A sanitized Scrapingdog failure that never includes the API key or body."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class C5RetrievalTarget:
    source_name: str
    experiment_id: str
    retrieval_run_id: str
    listing_url: str
    allowed_domains: tuple[str, ...]
    max_records: int
    institution_id: str | None = None
    institution_name: str | None = None

    def __post_init__(self) -> None:
        if self.source_name not in SOURCE_CAPS:
            raise C5RetrievalError("source is not allowlisted for C5 retrieval")
        if self.source_name == "zambian_public_institution":
            if not self.institution_id:
                raise C5RetrievalError("public institution retrieval requires institution_id")
            approved = PUBLIC_INSTITUTION_DOMAINS.get(self.institution_id.lower())
            if not approved or tuple(self.allowed_domains) != approved:
                raise C5RetrievalError("institution domain is not configured")
        elif self.institution_id:
            raise C5RetrievalError("institution_id is only valid for public institutions")
        if not self.allowed_domains or any(not _valid_domain(d) for d in self.allowed_domains):
            raise C5RetrievalError("explicit approved domain is required")
        if self.source_name != "zambian_public_institution":
            expected = {
                "go_zambia_jobs": ("gozambiajobs.com",),
                "jobzambia": ("jobzambia.com",),
                "zambiajob": ("zambiajob.com",),
            }[self.source_name]
            if tuple(d.lower().rstrip(".") for d in self.allowed_domains) != expected:
                raise C5RetrievalError("source domain is not approved")
        if not 1 <= self.max_records <= SOURCE_CAPS[self.source_name]:
            raise C5RetrievalError("max_records exceeds source cap")
        try:
            validate_url(self.listing_url)
        except ValueError as error:
            raise C5RetrievalError("listing URL is invalid") from error
        if not domain_allowed(self.listing_url, self.allowed_domains):
            raise C5RetrievalError("listing URL is outside approved domain")


@dataclass(frozen=True, slots=True)
class C5RawVacancy:
    source_name: str
    source_url: str
    payload: Mapping[str, object]
    application_url: str | None = None


@dataclass(frozen=True, slots=True)
class C5HttpResponse:
    url: str
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class C5HttpTransport(Protocol):
    async def request(
        self, url: str, *, timeout: float, max_bytes: int
    ) -> C5HttpResponse:
        """Perform one request without redirects, cookies, or authentication."""


class UrllibC5Transport:
    def __init__(self, headers: Mapping[str, str] | None = None) -> None:
        self.headers = dict(headers or {})

    async def request(
        self, url: str, *, timeout: float, max_bytes: int
    ) -> C5HttpResponse:
        return await asyncio.to_thread(
            self._request, url, timeout, max_bytes, self.headers
        )

    @staticmethod
    def _request(
        url: str,
        timeout: float,
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
    ) -> C5HttpResponse:
        request_headers = {"User-Agent": "Jobyn-C5/1.0"}
        request_headers.update(headers or {})
        request = urllib.request.Request(url, headers=request_headers)
        opener = urllib.request.build_opener(
            _NoRedirectHandler,
            urllib.request.ProxyHandler({}),
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise C5TransportError(
                        "response_too_large", "HTTP response exceeded configured size limit"
                    )
                return C5HttpResponse(
                    response.geturl(),
                    response.status,
                    {k.lower(): v for k, v in response.headers.items()},
                    body,
                )
        except urllib.error.HTTPError as error:
            return C5HttpResponse(
                url,
                error.code,
                {key.lower(): value for key, value in error.headers.items()},
                error.read(max_bytes + 1),
            )
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                code, message = "timeout", "HTTP request timed out"
            elif isinstance(reason, (socket.gaierror, ConnectionError, OSError)):
                code, message = "dns_connect_failure", "HTTP DNS or connection failure"
            else:
                code, message = "url_transport_failure", "HTTP URL transport failure"
            raise C5TransportError(code, message) from None
        except TimeoutError:
            raise C5TransportError("timeout", "HTTP request timed out") from None
        except (socket.gaierror, ConnectionError):
            raise C5TransportError(
                "dns_connect_failure", "HTTP DNS or connection failure"
            ) from None
        except OSError:
            raise C5TransportError(
                "unexpected_transport", "Unexpected HTTP transport failure"
            ) from None
        except Exception:
            raise C5TransportError(
                "unexpected_transport", "Unexpected HTTP transport failure"
            ) from None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _valid_domain(domain: str) -> bool:
    value = domain.strip().lower().rstrip(".")
    return bool(value) and "*" not in value and "/" not in value and " " not in value


def domain_allowed(url: str, allowed_domains: Sequence[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme in {"http", "https"}
        and not parsed.username
        and not parsed.password
        and any(host == d.strip().lower().rstrip(".") for d in allowed_domains)
    )


class C5RequestGovernor:
    def __init__(self, store: C5ExperimentStore, experiment_id: str, *, maximum_requests: int = 40):
        if not 1 <= maximum_requests <= 40:
            raise ValueError("maximum_requests must be between 1 and 40")
        self.store = store
        self.experiment_id = experiment_id
        self.maximum_requests = maximum_requests
        self._lock = asyncio.Lock()

    async def reserve(
        self,
        source_name: str,
        *,
        retrieval_query: str | None = None,
        provider: str | None = None,
        retrieval_method: str = "controlled_http",
    ) -> int:
        async with self._lock:
            if not self.store.reserve_request(
                self.experiment_id,
                source_name,
                retrieval_method,
                self.maximum_requests,
                retrieval_query=retrieval_query,
                provider=provider,
            ):
                raise C5TransportError(
                    "request_budget_exhausted",
                    "C5 retrieval request budget is exhausted",
                )
            return self.store.request_count(self.experiment_id)


class C5HttpClient:
    def __init__(
        self,
        governor: C5RequestGovernor,
        *,
        transport: C5HttpTransport,
        source_name: str,
        timeout: float = 10.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        retrieval_query: str | None = None,
        provider: str | None = None,
        retrieval_method: str = "controlled_http",
    ):
        if timeout <= 0 or not 0 < max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError("unsafe HTTP client limits")
        self.governor = governor
        self.transport = transport
        self.source_name = source_name
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.retrieval_query = retrieval_query
        self.provider = provider
        self.retrieval_method = retrieval_method

    async def get(self, url: str, allowed_domains: Sequence[str]) -> C5HttpResponse:
        try:
            validate_url(url)
        except ValueError:
            raise C5TransportError("domain_guard", "Request URL is invalid") from None
        if not domain_allowed(url, allowed_domains):
            raise C5TransportError(
                "domain_guard", "Request URL is outside the approved domain"
            )
        await self.governor.reserve(
            self.source_name,
            retrieval_query=self.retrieval_query,
            provider=self.provider,
            retrieval_method=self.retrieval_method,
        )
        try:
            response = await self.transport.request(
                url, timeout=self.timeout, max_bytes=self.max_response_bytes
            )
        except C5TransportError:
            raise
        except C5RetrievalError:
            raise
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise C5TransportError("timeout", "HTTP request timed out") from None
            if isinstance(reason, (socket.gaierror, ConnectionError, OSError)):
                raise C5TransportError(
                    "dns_connect_failure", "HTTP DNS or connection failure"
                ) from None
            raise C5TransportError(
                "url_transport_failure", "HTTP URL transport failure"
            ) from None
        except TimeoutError:
            raise C5TransportError("timeout", "HTTP request timed out") from None
        except (socket.gaierror, ConnectionError):
            raise C5TransportError(
                "dns_connect_failure", "HTTP DNS or connection failure"
            ) from None
        except OSError:
            raise C5TransportError(
                "unexpected_transport", "Unexpected HTTP transport failure"
            ) from None
        except Exception:
            raise C5TransportError(
                "unexpected_transport", "Unexpected HTTP transport failure"
            ) from None
        if response.url != url:
            raise C5TransportError(
                "domain_guard", "HTTP redirect was not followed"
            )
        if len(response.body) > self.max_response_bytes:
            raise C5TransportError(
                "response_too_large", "HTTP response exceeded configured size limit"
            )
        if 300 <= response.status_code < 400:
            raise C5TransportError(
                "domain_guard", "HTTP redirect was not followed"
            )
        return response


class _FieldParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields: dict[str, list[str]] = {}
        self.detail_urls: list[str] = []
        self._active: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        href = values.get("href")
        if href and (
            values.get("data-vacancy-url") is not None
            or "detail" in values.get("class", "")
        ):
            self.detail_urls.append(href)
        field = values.get("data-field") or values.get("class")
        if field:
            self._active = field
            self.fields.setdefault(field, [])

    def handle_data(self, data: str) -> None:
        if self._active:
            self.fields[self._active].append(data)

    def handle_endtag(self, tag: str) -> None:
        self._active = None

    def value(self, *names: str) -> str:
        for name in names:
            if self.fields.get(name):
                return " ".join(self.fields[name]).strip()
        return ""


class _HtmlVacancyParser:
    def __init__(self, source_name: str):
        self.source_name = source_name

    def parse(self, response: C5HttpResponse, target: C5RetrievalTarget) -> list[C5RawVacancy]:
        parser = _FieldParser()
        parser.feed(response.body.decode("utf-8", errors="replace"))
        title = parser.value("title", "job-title")
        company = parser.value("company", "employer")
        location = parser.value("location")
        source_url = parser.value("source_url") or response.url
        application_url = parser.value("application_url", "apply")
        if not title or not company:
            return []
        return [
            C5RawVacancy(
                self.source_name,
                source_url,
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "country": "Zambia" if "zambia" in location.lower() else "",
                    "description": parser.value("description"),
                    "requirements": parser.value("requirements"),
                    "source_url": source_url,
                    "application_url": application_url,
                    "external_id": parser.value("external_id", "job-id"),
                    "attribution": f"Source: {self.source_name}",
                },
                application_url or None,
            )
        ]

    def detail_urls(self, response: C5HttpResponse, target: C5RetrievalTarget) -> list[str]:
        parser = _FieldParser()
        parser.feed(response.body.decode("utf-8", errors="replace"))
        return [
            urljoin(response.url, href)
            for href in parser.detail_urls
            if domain_allowed(urljoin(response.url, href), target.allowed_domains)
        ]


class GoZambiaJobsParser(_HtmlVacancyParser):
    def __init__(self):
        super().__init__("go_zambia_jobs")


class JobZambiaParser(_HtmlVacancyParser):
    def __init__(self):
        super().__init__("jobzambia")


class PublicInstitutionParser(_HtmlVacancyParser):
    def __init__(self):
        super().__init__("zambian_public_institution")


class ZambiaJobParser(_HtmlVacancyParser):
    def __init__(self):
        super().__init__("zambiajob")


def _parser_for(source_name: str) -> _HtmlVacancyParser:
    parsers = {
        "go_zambia_jobs": GoZambiaJobsParser,
        "jobzambia": JobZambiaParser,
        "zambian_public_institution": PublicInstitutionParser,
        "zambiajob": ZambiaJobParser,
    }
    try:
        return parsers[source_name]()
    except KeyError as error:
        raise C5RetrievalError("source has no C5 parser") from error


async def run_c5_retrieval(
    config: C5ExperimentConfig,
    target: C5RetrievalTarget,
    *,
    transport: C5HttpTransport,
    store: C5ExperimentStore | None = None,
) -> C5ImportResult:
    config.validate()
    if target.experiment_id != config.experiment_id:
        raise C5RetrievalError("target experiment does not match configuration")
    db = store or C5ExperimentStore(config.sqlite_path)
    governor = C5RequestGovernor(db, config.experiment_id)
    client = C5HttpClient(governor, transport=transport, source_name=target.source_name)
    if target.source_name == "zambiajob" and db.count_primary_jobs() >= 25:
        raise C5RetrievalError("zambiajob is fallback-only after primary sources reach 25 records")
    response = await client.get(target.listing_url, target.allowed_domains)
    if response.status_code >= 400:
        raise C5RetrievalError("listing request failed")
    parser = _parser_for(target.source_name)
    raw = parser.parse(response, target)
    if any(not domain_allowed(vacancy.source_url, target.allowed_domains) for vacancy in raw):
        raise C5RetrievalError("parsed source URL is outside approved domain")
    for detail_url in parser.detail_urls(response, target):
        if len(raw) >= target.max_records:
            break
        detail = await client.get(detail_url, target.allowed_domains)
        if detail.status_code < 400:
            detail_raw = parser.parse(detail, target)
            if any(
                not domain_allowed(vacancy.source_url, target.allowed_domains)
                for vacancy in detail_raw
            ):
                raise C5RetrievalError("parsed source URL is outside approved domain")
            raw.extend(detail_raw)
    if not raw:
        raise C5RetrievalError("required vacancy fields could not be obtained")
    return run_c5_experiment_import(
        config,
        target.source_name,
        [vacancy.payload for vacancy in raw[: target.max_records]],
        institution_id=target.institution_id,
        retrieval_requests=0,
        store=db,
        retrieval_run_id=target.retrieval_run_id,
    )


def _scrapingdog_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return " ".join(
            text for item in value if (text := _scrapingdog_text(item))
        )
    return ""


def _scrapingdog_requirements(result: Mapping[str, object]) -> str:
    highlights = result.get("job_highlights")
    if not isinstance(highlights, list):
        return _scrapingdog_text(result.get("requirements"))
    sections: list[str] = []
    for section in highlights:
        if not isinstance(section, Mapping):
            continue
        title = _scrapingdog_text(section.get("title"))
        items = _scrapingdog_text(section.get("items"))
        if title and items:
            sections.append(f"{title}: {items}")
        elif items:
            sections.append(items)
    return "\n".join(sections) or _scrapingdog_text(result.get("requirements"))


def _scrapingdog_application_url(result: Mapping[str, object]) -> str:
    links = result.get("apply_links")
    if not isinstance(links, list):
        return ""
    for item in links:
        if not isinstance(item, Mapping):
            continue
        link = validate_url(_scrapingdog_text(item.get("link") or item.get("url")))
        if link:
            return link
    return ""


def _scrapingdog_payload(
    result: Mapping[str, object],
    *,
    query: str,
    retrieved_at: str,
    experiment_id: str,
    retrieval_run_id: str,
    http_status: int,
) -> dict[str, object]:
    title = _scrapingdog_text(result.get("title"))
    company = _scrapingdog_text(result.get("company_name"))
    location = _scrapingdog_text(result.get("location"))
    description = _scrapingdog_text(result.get("description"))
    source_url = validate_url(
        _scrapingdog_text(result.get("share_link") or result.get("source_url"))
    ) or ""
    application_url = _scrapingdog_application_url(result)
    via = _scrapingdog_text(result.get("via"))
    posted_at = _scrapingdog_text(
        result.get("posted_at") or result.get("date_posted")
    )
    deadline = _scrapingdog_text(result.get("deadline") or result.get("application_deadline"))
    return {
        "title": title,
        "company": company,
        "company_name": company,
        "location": location,
        "country": "Zambia" if "zambia" in location.lower() else "",
        "description": description,
        "requirements": _scrapingdog_requirements(result),
        "source_url": source_url,
        "application_url": application_url,
        "external_id": _scrapingdog_text(result.get("job_id") or result.get("id")),
        "posted_at": posted_at,
        "deadline": deadline,
        "category": classify_category(title, description),
        "attribution": (
            "Source: Google Jobs via Scrapingdog - view original vacancy"
            + (f" (via {via})" if via else "")
        ),
        "provider": "scrapingdog_google_jobs",
        "retrieval_provider": "Scrapingdog Google Jobs API",
        "retrieval_query": query,
        "retrieval_timestamp": retrieved_at,
        "experiment_id": experiment_id,
        "retrieval_run_id": retrieval_run_id,
        "http_status": http_status,
        "google_jobs_via": via,
    }


class C5ScrapingdogGoogleJobsProvider:
    """Optional Google Jobs API provider; application links are metadata only."""

    def __init__(
        self,
        *,
        transport: C5HttpTransport,
        api_key: str | None,
    ) -> None:
        self.transport = transport
        self.api_key = api_key

    async def search(
        self,
        config: C5ExperimentConfig,
        query: str,
        *,
        retrieval_run_id: str,
        store: C5ExperimentStore | None = None,
        country: str = "zm",
    ) -> C5ImportResult:
        config.validate()
        if not self.api_key or not self.api_key.strip():
            raise C5ScrapingdogError(
                "missing_api_key", "SCRAPINGDOG_API_KEY is required for this provider"
            )
        normalized_query = " ".join(query.split())
        if not normalized_query or len(normalized_query) > 250:
            raise C5ScrapingdogError(
                "invalid_query", "Google Jobs query must contain 1 to 250 characters"
            )
        if country.lower() != "zm":
            raise C5ScrapingdogError("invalid_country", "only Zambia country code zm is allowed")

        db = store or C5ExperimentStore(config.sqlite_path)
        request_url = (
            f"{SCRAPINGDOG_GOOGLE_JOBS_ENDPOINT}?"
            + urlencode({"api_key": self.api_key, "query": normalized_query, "country": "zm"})
        )
        client = C5HttpClient(
            C5RequestGovernor(db, config.experiment_id),
            transport=self.transport,
            source_name="scrapingdog_google_jobs",
            retrieval_query=normalized_query,
            provider="scrapingdog_google_jobs",
        )
        try:
            response = await client.get(request_url, SCRAPINGDOG_GOOGLE_JOBS_DOMAIN)
        except C5TransportError as error:
            messages = {
                "request_budget_exhausted": "C5 retrieval request budget is exhausted",
                "response_too_large": "Scrapingdog response exceeded the configured size limit",
                "timeout": "Scrapingdog request timed out",
                "dns_connect_failure": "Scrapingdog DNS or connection failure",
                "url_transport_failure": "Scrapingdog URL transport failure",
                "domain_guard": "Scrapingdog request was rejected by a domain safety guard",
                "unexpected_transport": "Unexpected Scrapingdog transport failure",
            }
            raise C5ScrapingdogError(
                error.code,
                messages.get(error.code, "Scrapingdog request failed safely"),
            ) from None
        except C5RetrievalError:
            raise C5ScrapingdogError(
                "guard_failure", "Scrapingdog request was stopped by a C5 safety guard"
            ) from None
        if response.status_code in {401, 403}:
            raise C5ScrapingdogError(
                "authentication_error",
                f"Scrapingdog rejected the API credentials (HTTP {response.status_code})",
            )
        if response.status_code == 429:
            raise C5ScrapingdogError(
                "rate_limited", "Scrapingdog rate-limited the request (HTTP 429)"
            )
        if response.status_code >= 400:
            raise C5ScrapingdogError(
                "http_error", f"Scrapingdog returned HTTP {response.status_code}"
            )
        try:
            body = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise C5ScrapingdogError(
                "malformed_json", "Scrapingdog returned malformed JSON"
            ) from error
        if not isinstance(body, Mapping):
            raise C5ScrapingdogError(
                "malformed_response", "Scrapingdog response must be a JSON object"
            )
        if body.get("error"):
            raise C5ScrapingdogError(
                "api_error", "Scrapingdog reported an API error"
            )
        results = body.get("jobs_results")
        if not isinstance(results, list):
            raise C5ScrapingdogError(
                "malformed_response", "Scrapingdog response is missing jobs_results"
            )

        retrieved_at = datetime.now(UTC).isoformat()
        payloads = [
            _scrapingdog_payload(
                item if isinstance(item, Mapping) else {},
                query=normalized_query,
                retrieved_at=retrieved_at,
                experiment_id=config.experiment_id,
                retrieval_run_id=retrieval_run_id,
                http_status=response.status_code,
            )
            for item in results[:50]
        ]
        return run_c5_experiment_import(
            config,
            "scrapingdog_google_jobs",
            payloads,
            retrieval_requests=0,
            store=db,
            retrieval_run_id=retrieval_run_id,
            retrieval_query=normalized_query,
            retrieval_provider="scrapingdog_google_jobs",
            http_status=response.status_code,
        )


async def run_scrapingdog_google_jobs(
    config: C5ExperimentConfig,
    query: str,
    *,
    retrieval_run_id: str,
    transport: C5HttpTransport,
    store: C5ExperimentStore | None = None,
    api_key: str | None = None,
) -> C5ImportResult:
    """Run one non-paginated query through Scrapingdog and the isolated C5 store."""

    return await C5ScrapingdogGoogleJobsProvider(
        transport=transport,
        api_key=api_key if api_key is not None else config.scrapingdog_api_key,
    ).search(
        config,
        query,
        retrieval_run_id=retrieval_run_id,
        store=store,
    )
