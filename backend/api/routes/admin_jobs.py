"""Administrator-only job CSV import endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_session, require_admin
from backend.models.user import User
from backend.schemas.admin_jobs import JobImportHistoryItem, JobImportPreview, JobImportResult
from backend.services.admin_csv_import import CSVImportError, CSVImportService, csv_template

router = APIRouter(prefix="/admin/jobs/import", tags=["Admin Jobs"])
_service = CSVImportService()


async def _parse_upload(upload: UploadFile):
    try:
        data = await _service.read_upload(upload)
        return _service.parse(data, upload.filename or "jobs.csv")
    except CSVImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/preview",
    response_model=JobImportPreview,
    summary="Validate and preview an administrator job CSV",
)
async def preview_job_csv(
    file: Annotated[UploadFile, File(...)],
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> JobImportPreview:
    parsed = await _parse_upload(file)
    return await _service.preview(session, parsed)


@router.post(
    "",
    response_model=JobImportResult,
    summary="Import valid rows from an administrator job CSV",
)
async def import_job_csv(
    file: Annotated[UploadFile, File(...)],
    current_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> JobImportResult:
    parsed = await _parse_upload(file)
    try:
        return await _service.import_rows(session, parsed, current_user)
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The import failed and the database transaction was rolled back.",
        ) from exc


@router.get(
    "/template",
    response_class=Response,
    summary="Download the administrator CSV template",
)
async def download_job_csv_template(
    _: User = Depends(require_admin),
) -> Response:
    return Response(
        content=csv_template(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="jobyn-job-import-template.csv"'},
    )


@router.get(
    "/history",
    response_model=list[JobImportHistoryItem],
    summary="List recent administrator CSV imports",
)
async def list_job_csv_history(
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[JobImportHistoryItem]:
    return await _service.history(session)