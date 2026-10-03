"""FastAPI application factory and ASGI entrypoint.

Using a factory keeps the app free of import-time side effects beyond the
database engine (see ``backend/database/session.py``), makes testing trivial,
and lets tests inject an explicit :class:`Settings` instance if needed.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.api.router import api_router
from backend.core.config import Settings, get_settings
from backend.core.errors import register_exception_handlers
from backend.utils.logging import configure_logging

_ADMIN_PAGE = Path(__file__).parent / "admin.html"
_CSV_MAX_REQUEST_BYTES = 5 * 1024 * 1024 + 64 * 1024


class CsvUploadBodyLimitMiddleware:
    """Bound CSV request bodies before multipart parsing can spool them."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        paths: set[str],
        maximum_bytes: int,
    ) -> None:
        self.app = app
        self.paths = paths
        self.maximum_bytes = maximum_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] not in self.paths
        ):
            await self.app(scope, receive, send)
            return

        content_length = next(
            (
                value
                for key, value in scope.get("headers", [])
                if key.lower() == b"content-length"
            ),
            None,
        )
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = self.maximum_bytes + 1
            if declared_size > self.maximum_bytes:
                await self._too_large(send)
                return

        received_bytes = 0
        exceeded = False

        async def limited_receive() -> Message:
            nonlocal received_bytes, exceeded
            if exceeded:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.maximum_bytes:
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def limited_send(message: Message) -> None:
            if not exceeded:
                await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded:
            await self._too_large(send)

    @staticmethod
    async def _too_large(send: Send) -> None:
        body = b'{"detail":"CSV upload request exceeds the maximum allowed size."}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    prefix = settings.API_V1_PREFIX.rstrip("/")
    app.add_middleware(
        CsvUploadBodyLimitMiddleware,
        paths={
            f"{prefix}/admin/jobs/csv/preview",
            f"{prefix}/admin/jobs/csv/import",
        },
        maximum_bytes=_CSV_MAX_REQUEST_BYTES,
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/admin", include_in_schema=False)
    async def admin_page() -> FileResponse:
        return FileResponse(_ADMIN_PAGE, media_type="text/html")

    return app


app = create_app()
