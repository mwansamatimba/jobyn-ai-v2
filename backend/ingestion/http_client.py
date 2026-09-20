"""Shared async HTTP client for the Job Ingestion Engine.

Every external request:
  - carries an identifiable User-Agent (never rotated to evade restrictions)
  - enforces a per-request timeout
  - retries with exponential back-off on transient failures (5xx, network)
  - does NOT retry on 4xx (the source is responding intentionally)
  - caps the response body at 10 MB to guard against memory issues
  - rejects non-HTTPS redirects (SSRF protection)

Usage::

    async with ingestion_client() as client:
        response = await client.get(url)
        data = response.json()

The context-manager form is preferred because it ensures the underlying
connection pool is closed even if an exception occurs.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from backend.core.config import get_settings

logger = logging.getLogger(__name__)

# Maximum response body size accepted from any external source.
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024   # 10 MB

# HTTP status codes that are safe to retry.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _build_client() -> httpx.AsyncClient:
    """Build a preconfigured HTTPX async client."""
    settings = get_settings()
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.INGESTION_REQUEST_TIMEOUT),
        headers={"User-Agent": settings.INGESTION_USER_AGENT},
        follow_redirects=True,
        max_redirects=5,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )


@asynccontextmanager
async def ingestion_client() -> AsyncIterator[httpx.AsyncClient]:
    """Async context manager yielding a configured HTTPX client."""
    client = _build_client()
    try:
        yield client
    finally:
        await client.aclose()


async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
    retries: int | None = None,
) -> Any:
    """Fetch a URL and return the parsed JSON body.

    Args:
        url:     The URL to fetch.  Must use http/https.
        params:  Optional query parameters.
        headers: Optional additional request headers.
        client:  Optional pre-built client (useful for testing).
        retries: Override the configured retry count.

    Returns:
        Parsed JSON (dict or list).

    Raises:
        httpx.HTTPStatusError: On a non-retryable HTTP error.
        httpx.RequestError:    On network failure after all retries.
        ValueError:            If the response body exceeds the size limit.
    """
    settings = get_settings()
    max_retries = retries if retries is not None else settings.INGESTION_MAX_RETRIES

    _validate_url(url)

    _own = client is None
    _client = client or _build_client()

    try:
        return await _get_with_retry(
            _client, url, params=params, headers=headers, max_retries=max_retries
        )
    finally:
        if _own:
            await _client.aclose()


async def get_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
    retries: int | None = None,
) -> str:
    """Fetch a URL and return the response body as a string (e.g. RSS/XML)."""
    settings = get_settings()
    max_retries = retries if retries is not None else settings.INGESTION_MAX_RETRIES

    _validate_url(url)

    _own = client is None
    _client = client or _build_client()

    try:
        resp = await _get_with_retry(
            _client, url, params=params, headers=headers,
            max_retries=max_retries, return_raw=True,
        )
        return resp.text  # type: ignore[union-attr]
    finally:
        if _own:
            await _client.aclose()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_url(url: str) -> None:
    """Reject unsafe URL schemes (SSRF protection)."""
    lower = url.lower().strip()
    for bad in ("javascript:", "data:", "file:", "ftp:"):
        if lower.startswith(bad):
            raise ValueError(f"Unsafe URL scheme rejected: {url!r}")
    if not (lower.startswith("http://") or lower.startswith("https://")):
        raise ValueError(f"Only http/https URLs are permitted, got: {url!r}")


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None,
    max_retries: int,
    return_raw: bool = False,
) -> Any:
    """Perform GET with exponential back-off on transient errors."""
    attempt = 0
    last_exc: Exception | None = None

    while attempt <= max_retries:
        if attempt:
            delay = min(2 ** attempt, 30)  # cap at 30 s
            logger.debug("Retry %d/%d for %s — waiting %ss", attempt, max_retries, url, delay)
            await asyncio.sleep(delay)

        try:
            resp = await client.get(url, params=params, headers=headers)

            # Guard against huge responses.
            content_length = resp.headers.get("content-length")
            if content_length and int(content_length) > _MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"Response from {url!r} exceeds size limit "
                    f"({content_length} > {_MAX_RESPONSE_BYTES} bytes)"
                )

            if resp.status_code in _RETRYABLE_STATUS:
                logger.warning(
                    "Transient HTTP %s from %s (attempt %d/%d)",
                    resp.status_code, url, attempt + 1, max_retries + 1,
                )
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
                attempt += 1
                continue

            resp.raise_for_status()

            if return_raw:
                return resp
            return resp.json()

        except httpx.HTTPStatusError:
            raise   # 4xx are not retried
        except (httpx.RequestError, ValueError) as exc:
            last_exc = exc
            logger.warning("Request error for %s (attempt %d/%d): %s", url, attempt + 1, max_retries + 1, exc)
            attempt += 1

    raise last_exc or RuntimeError(f"All retries exhausted for {url!r}")
