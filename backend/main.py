"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .api import model as model_api
from .api import readings as readings_api
from .api import system as system_api
from .api.common import Services
from .config import APP_VERSION, Settings
from .database import Database
from .services.ingestion import IngestionService
from .services.model_service import ModelService
from .services.realtime import Hub

log = logging.getLogger("landslide")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Rejects request bodies larger than max_bytes (declared or streamed)."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    too_big = int(value) > self.max_bytes
                except ValueError:
                    too_big = True
                if too_big:
                    await self._reject(send)
                    return
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"status":"error","error":{"code":413,"message":"request body too large"}}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _error(status: int, message: str, details=None) -> JSONResponse:
    content = {"status": "error", "error": {"code": status, "message": message}}
    if details:
        content["error"]["details"] = details
    return JSONResponse(status_code=status, content=content)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.db_path, settings.backup_dir)
    model = ModelService(settings)
    hub = Hub()
    ingestion = IngestionService(db, model, hub, settings)
    services = Services(settings=settings, db=db, model=model, hub=hub, ingestion=ingestion)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.migrate()
        model.load()
        hub.bind_loop(asyncio.get_running_loop())
        for problem in settings.problems():
            log.warning("configuration: %s", problem)

        async def monitor():
            while True:
                await asyncio.sleep(2.0)
                try:
                    await run_in_threadpool(ingestion.check_offline)
                except Exception:  # noqa: BLE001 - keep monitoring
                    log.exception("connectivity monitor failed")

        task = asyncio.create_task(monitor())
        log.info("backend %s started", APP_VERSION)
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            db.close()
            log.info("backend stopped")

    app = FastAPI(
        title="Landslide Monitoring Backend",
        version=APP_VERSION,
        description="Four-sensor experimental landslide monitoring prototype. Risk classes come from a Random Forest "
                    "model; they are prototype outputs, not validated public-safety warnings.",
        lifespan=lifespan,
    )
    app.state.services = services

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return _error(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()][:20]
        return _error(422, "request validation failed", details)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return _error(500, "internal server error")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if not request.url.path.startswith("/api/") and not request.url.path.startswith("/docs"):
            response.headers.setdefault("Content-Security-Policy", CSP)
            # Dashboard assets: always revalidate (ETag) so an updated module is never served stale.
            response.headers.setdefault("Cache-Control", "no-cache")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "DELETE"],
                           allow_headers=["Content-Type", "X-Admin-Token"])

    app.include_router(readings_api.router)
    app.include_router(system_api.router)
    app.include_router(model_api.router)

    if settings.frontend_dir.exists():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app
