"""Small Bright Data Jobs API client used by the LinkedIn/Indeed connectors.

The module intentionally contains only transport/orchestration helpers; source
field mapping remains in the individual connectors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.brightdata.com"
DEFAULT_TIMEOUT = 60.0
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_POLL_TIMEOUT = 180.0


class BrightDataError(RuntimeError):
    """Controlled Bright Data API failure."""


async def run_keyword_dataset(
    *,
    api_key: str,
    dataset_id: str,
    inputs: list[dict[str, Any]],
    limit_per_input: int,
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.AsyncClient | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT,
) -> list[dict[str, Any]]:
    """Trigger a Bright Data discovery dataset and fetch its snapshot."""
    if not api_key:
        raise BrightDataError("BRIGHTDATA_API_KEY is not configured")
    if not dataset_id:
        raise BrightDataError("Bright Data dataset ID is not configured")
    if not inputs:
        return []

    params = {
        "dataset_id": dataset_id,
        "include_errors": "true",
        "type": "discover_new",
        "discover_by": "keyword",
        "limit_per_input": str(max(1, limit_per_input)),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    own_client = client is None
    http = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        response = await http.post(
            f"{base_url.rstrip('/')}/datasets/v3/trigger",
            params=params,
            headers=headers,
            json=inputs,
        )
        response.raise_for_status()
        trigger = response.json()
        snapshot_id = trigger.get("snapshot_id")
        if not snapshot_id:
            raise BrightDataError("Bright Data trigger response did not contain snapshot_id")

        deadline = asyncio.get_running_loop().time() + poll_timeout
        while True:
            progress = await http.get(
                f"{base_url.rstrip('/')}/datasets/v3/progress/{snapshot_id}",
                headers=headers,
            )
            progress.raise_for_status()
            status = str(progress.json().get("status", "")).lower()

            if status == "ready":
                break
            if status in {"failed", "error", "cancelled"}:
                raise BrightDataError(
                    f"Bright Data snapshot {snapshot_id} failed with status={status}"
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise BrightDataError(
                    f"Bright Data snapshot {snapshot_id} timed out after {poll_timeout}s"
                )
            await asyncio.sleep(poll_interval)

        snapshot = await http.get(
            f"{base_url.rstrip('/')}/datasets/v3/snapshot/{snapshot_id}",
            params={"format": "json"},
            headers=headers,
        )
        snapshot.raise_for_status()
        data = snapshot.json()

        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            records = data.get("data") or data.get("records") or data.get("items")
            if isinstance(records, list):
                return [item for item in records if isinstance(item, dict)]
        raise BrightDataError("Bright Data snapshot returned an unsupported response shape")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            raise BrightDataError("Bright Data authentication failed (HTTP 401)") from exc
        if status == 429:
            raise BrightDataError("Bright Data rate limit reached (HTTP 429)") from exc
        raise BrightDataError(f"Bright Data HTTP error (HTTP {status})") from exc
    except httpx.TimeoutException as exc:
        raise BrightDataError("Bright Data request timed out") from exc
    except httpx.HTTPError as exc:
        raise BrightDataError(f"Bright Data network error: {exc.__class__.__name__}") from exc
    finally:
        if own_client:
            await http.aclose()
