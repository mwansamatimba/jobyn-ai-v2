"""Resume upload and parsing service.

Owns the full pipeline:
  1. Validate the uploaded file (type + size).
  2. Persist the raw upload record.
  3. Extract plain text from the file bytes.
  4. Run the AI CV analyzer to produce a structured profile.
  5. Persist a canonical Resume from the AI output.
  6. Update the UploadedResume record with parsed data and status.

All transaction boundaries are managed here; routes stay free of session
lifecycle concerns.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from backend.ai.cv_analyzer import CVAnalysisError, CVAnalyzerService
from backend.models.enums import ParseStatus, ResumeStatus
from backend.models.resume import Resume, UploadedResume
from backend.repositories.resume import ResumeRepository, UploadedResumeRepository
from backend.services.base import BaseService
from backend.services.resume_parser import (
    ResumeParserError,
    extract_text,
    validate_upload,
)

logger = logging.getLogger(__name__)


class ResumeUploadError(Exception):
    """Raised when the resume upload or parse pipeline fails."""


class ResumeService(BaseService[UploadedResumeRepository]):
    """Orchestrates resume upload, text extraction and AI parsing."""

    def __init__(
        self,
        upload_repository: UploadedResumeRepository,
        resume_repository: ResumeRepository,
        cv_analyzer: CVAnalyzerService | None = None,
    ) -> None:
        super().__init__(upload_repository)
        self.upload_repository = upload_repository
        self.resume_repository = resume_repository
        self._cv_analyzer = cv_analyzer or CVAnalyzerService()

    async def upload_and_parse(
        self,
        user_id: uuid.UUID,
        filename: str,
        content_type: str,
        file_bytes: bytes,
    ) -> UploadedResume:
        """Run the full upload → validate → extract → parse → persist pipeline.

        Args:
            user_id: The authenticated user's UUID.
            filename: Original filename as reported by the client.
            content_type: MIME type of the uploaded file.
            file_bytes: Raw file content.

        Returns:
            The persisted :class:`UploadedResume` record with parse results.

        Raises:
            ResumeUploadError: If validation, extraction, or AI analysis fails.
        """
        size_bytes = len(file_bytes)

        # Step 1 — validate before touching the database.
        try:
            validate_upload(content_type, size_bytes)
        except ResumeParserError as exc:
            raise ResumeUploadError(str(exc)) from exc

        # Step 2 — persist the upload record immediately (status=PROCESSING).
        upload = await self.upload_repository.create(
            user_id=user_id,
            original_filename=filename,
            file_path="",          # no file-system storage in this implementation
            content_type=content_type,
            file_size_bytes=size_bytes,
            parse_status=ParseStatus.PROCESSING,
        )
        await self.commit()

        # Steps 3–5 — extract text, call AI, persist results.
        try:
            text = self._extract(file_bytes, content_type)
            parsed_data = await self._analyze(text)
            resume = await self._persist_resume(user_id, filename, parsed_data)
            await self._finalize_upload(upload, parsed_data, resume)
        except Exception as exc:
            await self._mark_failed(upload)
            raise ResumeUploadError(str(exc)) from exc

        return upload

    async def get_candidate_profile(self, candidate_id: uuid.UUID) -> Resume | None:
        """Return the latest canonical resume for a user, or None.

        Args:
            candidate_id: The user's UUID (acts as candidate identifier).

        Returns:
            The most recent :class:`Resume` record, or ``None`` when the user
            has no parsed resumes yet.
        """
        return await self.resume_repository.get_latest_for_user(candidate_id)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _extract(self, file_bytes: bytes, content_type: str) -> str:
        """Extract plain text, re-raising as ResumeUploadError on failure."""
        try:
            return extract_text(file_bytes, content_type)
        except ResumeParserError as exc:
            raise ResumeUploadError(str(exc)) from exc

    async def _analyze(self, text: str) -> dict[str, Any]:
        """Run the AI CV analyzer, re-raising as ResumeUploadError on failure."""
        try:
            return await self._cv_analyzer.analyze_cv(text)
        except CVAnalysisError as exc:
            raise ResumeUploadError(str(exc)) from exc

    async def _persist_resume(
        self,
        user_id: uuid.UUID,
        filename: str,
        parsed_data: dict[str, Any],
    ) -> Resume:
        """Create a canonical Resume from AI output and commit it."""
        title = parsed_data.get("name") or filename
        resume = await self.resume_repository.create(
            user_id=user_id,
            title=title,
            status=ResumeStatus.COMPLETED,
            content=parsed_data,
            latest_parse_status=ParseStatus.SUCCEEDED,
            last_parsed_at=date.today(),
        )
        return resume

    async def _finalize_upload(
        self,
        upload: UploadedResume,
        parsed_data: dict[str, Any],
        resume: Resume,
    ) -> None:
        """Update upload record with success status and commit everything."""
        await self.upload_repository.update(
            upload,
            parse_status=ParseStatus.SUCCEEDED,
            parsed_data=parsed_data,
            parsed_at=date.today(),
            resume_id=resume.id,
        )
        await self.commit()

    async def _mark_failed(self, upload: UploadedResume) -> None:
        """Stamp the upload record as FAILED and commit."""
        try:
            await self.upload_repository.update(
                upload,
                parse_status=ParseStatus.FAILED,
            )
            await self.commit()
        except Exception:
            logger.exception("Failed to mark upload %s as failed", upload.id)


__all__ = ["ResumeService", "ResumeUploadError"]
