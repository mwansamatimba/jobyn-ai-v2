"""HTML sanitization and URL validation for ingested job content.

Why sanitize?
-------------
Approved sources may provide job descriptions as HTML.  Raw external HTML
must never be stored or rendered without sanitization because:
  - It may contain XSS payloads (script tags, event handlers).
  - It may embed tracking pixels or off-site resource loads.
  - It may contain misleading or malicious href/src attributes.

This module uses a strict allow-list approach: only the tags and attributes
in the allow-list survive.  Everything else is stripped (not escaped — the
tag is removed but its text content is preserved where safe).

The sanitizer is intentionally simple (no external dependencies beyond the
stdlib html module).  It produces safe, readable plain/HTML text suitable
for storage and display.

Application URL validation
--------------------------
Every application URL from a source must pass :func:`validate_application_url`
before being stored.  This ensures:
  - Only http/https schemes are accepted.
  - javascript:/data:/file: URLs are rejected.
  - The URL is not empty or whitespace.
"""

from __future__ import annotations

import html
import re
import urllib.parse

# ---------------------------------------------------------------------------
# HTML sanitizer
# ---------------------------------------------------------------------------

# Tags whose text content is preserved (tag stripped, text kept).
_STRIP_CONTENT_TAGS = frozenset({
    "script", "style", "iframe", "object", "embed", "form",
    "input", "button", "select", "textarea", "noscript",
})

# Tags allowed in sanitized output (safe formatting only).
_ALLOWED_TAGS = frozenset({
    "p", "br", "strong", "b", "em", "i", "u", "s",
    "ul", "ol", "li", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td",
    "a",   # allowed but href is validated
    "span", "div",  # allowed but no dangerous attributes
})

# Attributes allowed per-tag.
_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan", "scope"}),
}

# Regex to match HTML tags (opening, closing, self-closing).
_TAG_RE = re.compile(r"<(/?)(\w+)([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""(\w[\w-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+)))?""")

# Maximum length for a sanitized description stored in the DB.
_MAX_DESCRIPTION_CHARS = 50_000


def sanitize_html(raw: str | None) -> str:
    """Sanitize HTML from an approved source.

    Strips unsafe tags, event handlers, and dangerous attributes.
    Preserves safe formatting (bold, italic, lists, headings, links).
    Returns a safe HTML string (not plain text — the frontend renders it).

    Args:
        raw: Raw HTML string from the source.

    Returns:
        Sanitized HTML, or an empty string if ``raw`` is None/empty.
    """
    if not raw:
        return ""

    result: list[str] = []
    last_end = 0

    for match in _TAG_RE.finditer(raw):
        # Preserve text between tags.
        text_before = raw[last_end:match.start()]
        result.append(text_before)
        last_end = match.end()

        closing = match.group(1) == "/"
        tag = match.group(2).lower()
        attrs_raw = match.group(3)

        # Always drop dangerous tags entirely (including their opening/closing).
        if tag in _STRIP_CONTENT_TAGS:
            continue

        if tag not in _ALLOWED_TAGS:
            # Unknown tag — drop tag, preserve surrounding text (already handled).
            continue

        if closing:
            result.append(f"</{tag}>")
        else:
            safe_attrs = _build_safe_attrs(tag, attrs_raw)
            attr_str = "".join(f' {k}="{v}"' for k, v in safe_attrs.items())
            # Self-closing for <br>
            if tag == "br":
                result.append("<br>")
            else:
                result.append(f"<{tag}{attr_str}>")

    # Append any trailing text.
    result.append(raw[last_end:])

    sanitized = "".join(result)

    # Truncate before returning.
    if len(sanitized) > _MAX_DESCRIPTION_CHARS:
        sanitized = sanitized[:_MAX_DESCRIPTION_CHARS] + "…"

    return sanitized.strip()


def html_to_text(raw: str | None) -> str:
    """Convert HTML to plain text by stripping all tags.

    Useful when a plain-text field (e.g. requirements) receives HTML input.
    """
    if not raw:
        return ""
    # Remove all tags.
    text = re.sub(r"<[^>]+>", " ", raw)
    # Decode HTML entities.
    text = html.unescape(text)
    # Collapse whitespace.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_safe_attrs(tag: str, attrs_raw: str) -> dict[str, str]:
    """Extract and validate attributes for an allowed tag."""
    allowed = _ALLOWED_ATTRS.get(tag, frozenset())
    safe: dict[str, str] = {}

    for m in _ATTR_RE.finditer(attrs_raw):
        name = m.group(1).lower()
        value = m.group(2) or m.group(3) or m.group(4) or ""

        # Strip event handlers unconditionally.
        if name.startswith("on"):
            continue

        if name not in allowed:
            continue

        # For href, validate the URL scheme.
        if name == "href":
            value = _safe_href(value)
            if not value:
                continue

        safe[name] = html.escape(value, quote=True)

    return safe


def _safe_href(value: str) -> str:
    """Return a safe href value, or empty string if unsafe."""
    stripped = value.strip()
    lower = stripped.lower()
    for bad in ("javascript:", "data:", "vbscript:", "file:"):
        if lower.startswith(bad):
            return ""
    return stripped


# ---------------------------------------------------------------------------
# Application URL validation
# ---------------------------------------------------------------------------


def validate_application_url(url: str | None) -> str | None:
    """Validate and normalise an application URL from a source.

    Returns the cleaned URL string if valid, or None if the URL is missing,
    empty, or uses an unsafe scheme.

    Only ``http`` and ``https`` are accepted.  ``javascript:``, ``data:``,
    and ``file:`` are always rejected.

    Args:
        url: Raw URL string from the connector.

    Returns:
        Cleaned URL string, or None.
    """
    if not url:
        return None

    cleaned = url.strip()
    if not cleaned:
        return None

    try:
        parsed = urllib.parse.urlparse(cleaned)
    except Exception:
        return None

    if parsed.scheme.lower() not in {"http", "https"}:
        return None

    # Reject obviously malformed URLs (no netloc).
    if not parsed.netloc:
        return None

    return cleaned
