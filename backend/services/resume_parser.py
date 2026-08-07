"""Resume file parsing utilities.

Handles text extraction from uploaded PDF and DOCX files. This module is
framework-agnostic: it only reads bytes and returns plain text. All HTTP,
database and AI concerns are handled in other layers.
"""

from __future__ import annotations

import io
import logging
from typing import Final

logger = logging.getLogger(__name__)

# Supported MIME types and their canonical labels.
SUPPORTED_CONTENT_TYPES: Final[dict[str, str]] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}

# 10 MB hard limit for uploaded files.
MAX_FILE_SIZE_BYTES: Final[int] = 10 * 1024 * 1024


class ResumeParserError(Exception):
    """Raised when a resume file cannot be parsed."""


class UnsupportedFileTypeError(ResumeParserError):
    """Raised when the uploaded file type is not supported."""

    def __init__(self, content_type: str) -> None:
        super().__init__(
            f"Unsupported file type '{content_type}'. "
            "Only PDF and DOCX files are accepted."
        )


class FileTooLargeError(ResumeParserError):
    """Raised when the uploaded file exceeds the size limit."""

    def __init__(self, size_bytes: int) -> None:
        super().__init__(
            f"File size {size_bytes:,} bytes exceeds the "
            f"{MAX_FILE_SIZE_BYTES:,} byte limit."
        )


class EmptyDocumentError(ResumeParserError):
    """Raised when a file is valid but contains no extractable text."""

    def __init__(self) -> None:
        super().__init__(
            "The uploaded file appears to be empty or contains no readable text."
        )


def validate_upload(content_type: str, size_bytes: int) -> None:
    """Validate content type and file size before extraction.

    Args:
        content_type: MIME type reported by the client.
        size_bytes: Total byte length of the uploaded file.

    Raises:
        UnsupportedFileTypeError: If the MIME type is not in
            :data:`SUPPORTED_CONTENT_TYPES`.
        FileTooLargeError: If ``size_bytes`` exceeds :data:`MAX_FILE_SIZE_BYTES`.
    """
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise UnsupportedFileTypeError(content_type)
    if size_bytes > MAX_FILE_SIZE_BYTES:
        raise FileTooLargeError(size_bytes)


def extract_text(file_bytes: bytes, content_type: str) -> str:
    """Extract plain text from a PDF or DOCX byte payload.

    Args:
        file_bytes: Raw bytes of the uploaded file.
        content_type: MIME type used to select the appropriate extractor.

    Returns:
        Extracted plain text with whitespace normalized.

    Raises:
        UnsupportedFileTypeError: If ``content_type`` is not supported.
        EmptyDocumentError: If the file yields no text after extraction.
        ResumeParserError: If the file is corrupt or unreadable.
    """
    format_label = SUPPORTED_CONTENT_TYPES.get(content_type)
    if format_label is None:
        raise UnsupportedFileTypeError(content_type)

    try:
        if format_label == "pdf":
            text = _extract_pdf(file_bytes)
        else:
            text = _extract_docx(file_bytes)
    except (UnsupportedFileTypeError, EmptyDocumentError):
        raise
    except Exception as exc:
        logger.warning("Failed to extract text from %s file: %s", format_label, exc)
        raise ResumeParserError(
            f"Could not read the uploaded {format_label.upper()} file. "
            "It may be corrupted or password-protected."
        ) from exc

    normalized = " ".join(text.split())
    if not normalized:
        raise EmptyDocumentError()

    return normalized


def _extract_pdf(file_bytes: bytes) -> str:
    """Extract text from a PDF byte payload using pypdf."""
    from pypdf import PdfReader  # imported lazily so tests can patch

    reader = PdfReader(io.BytesIO(file_bytes))
    parts: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        parts.append(page_text)
    return "\n".join(parts)


def _extract_docx(file_bytes: bytes) -> str:
    """Extract text from a DOCX byte payload using python-docx."""
    from docx import Document  # imported lazily so tests can patch

    doc = Document(io.BytesIO(file_bytes))
    return "\n".join(para.text for para in doc.paragraphs)


__all__ = [
    "SUPPORTED_CONTENT_TYPES",
    "MAX_FILE_SIZE_BYTES",
    "ResumeParserError",
    "UnsupportedFileTypeError",
    "FileTooLargeError",
    "EmptyDocumentError",
    "validate_upload",
    "extract_text",
]
