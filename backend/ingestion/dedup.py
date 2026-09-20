"""Deterministic deduplication for the Job Ingestion Engine.

Deduplication priority (checked in order):

1. ``source_name + external_id``   — most reliable; exact identity match
2. Normalised ``application_url``  — same URL = same job
3. ``canonical_key``               — SHA-256 of normalised (company, title, location)

Similarity-based fuzzy matching is NOT implemented to avoid silent merges
of different jobs with similar titles at the same company.  Uncertain records
are retained separately.

Idempotency guarantee: running the same sync twice produces the same database
state.  A job already present is updated (last_seen, last_verified) rather than
duplicated.
"""

from __future__ import annotations

import hashlib
import logging
import re
import urllib.parse

from backend.ingestion.schema import NormalizedJob

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical key
# ---------------------------------------------------------------------------


def make_canonical_key(
    source_name: str,
    external_id: str,
    application_url: str,
    company: str = "",
    title: str = "",
    location: str = "",
) -> str:
    """Compute a stable SHA-256-based canonical key for a job.

    Priority:
    1. source_name + external_id  (most stable)
    2. normalised application_url (stable if URL is canonical)
    3. company + title + location  (similarity fallback, less reliable)

    The key is a hex string of the first 32 chars of the SHA-256 digest.
    """
    if source_name and external_id:
        raw = f"src:{_norm(source_name)}:id:{_norm(external_id)}"
    elif application_url:
        raw = f"url:{_norm_url(application_url)}"
    else:
        raw = f"cmp:{_norm(company)}:ttl:{_norm(title)}:loc:{_norm(location)}"

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def make_url_key(application_url: str) -> str:
    """Return a normalised URL key for deduplication."""
    return _norm_url(application_url)


# ---------------------------------------------------------------------------
# Text normalisation helpers (not exported)
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation."""
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_url(url: str) -> str:
    """Normalise a URL for deduplication.

    - Lowercase scheme and host
    - Sort query parameters
    - Strip trailing slash
    - Strip common tracking parameters (utm_*, fbclid, etc.)
    """
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/") or "/"
        # Parse and sort query params; strip tracking params
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        _TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_content",
                     "utm_term", "fbclid", "gclid", "msclkid", "_hsenc",
                     "_hsmi", "mc_eid"}
        filtered = sorted(
            (k, v[0]) for k, v in params.items() if k not in _TRACKING
        )
        qs = urllib.parse.urlencode(filtered)
        return urllib.parse.urlunparse((scheme, netloc, path, "", qs, ""))
    except Exception:
        return url.lower().strip()
