"""FastAPI application factory for the AuraBook daemon."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from aurabook.core.runtime import ApplicationRuntime
from aurabook.utils.config import Settings
from aurabook.utils.errors import (
    AuraBookError,
    AuthenticationError,
    RequestTooLargeError,
)
from aurabook.utils.logging import request_id_var, request_start_var, setup_logging

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Adds a request ID and timing metadata to each request."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request_id_var.set(request_id)
        request_start_var.set(time.time())

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info("%s %s %s", request.method, request.url.path, response.status_code)
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """Optional bearer token protection for the local daemon."""

    async def dispatch(self, request: Request, call_next):
        settings: Settings = request.app.state.settings
        if not settings.api_key or request.url.path in ("/", "/health"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {settings.api_key}":
            error = AuthenticationError("Invalid or missing API key")
            return JSONResponse(status_code=error.status_code, content=error.to_dict())

        return await call_next(request)


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Rejects requests that exceed the configured body size."""

    async def dispatch(self, request: Request, call_next):
        settings: Settings = request.app.state.settings
        content_length = request.headers.get("Content-Length")
        if content_length and int(content_length) > settings.max_body_size:
            error = RequestTooLargeError(
                f"Request body exceeds {settings.max_body_size} bytes"
            )
            return JSONResponse(status_code=error.status_code, content=error.to_dict())
        return await call_next(request)


async def aurabook_error_handler(
    request: Request,
    exc: AuraBookError,
) -> JSONResponse:
    """Convert structured AuraBook errors into JSON responses."""
    settings: Settings = request.app.state.settings
    content = exc.to_dict()
    if not settings.dev_mode and "details" in content["error"]:
        content["error"].pop("details", None)
    response = JSONResponse(status_code=exc.status_code, content=content)
    response.headers["X-AuraBook-Error"] = exc.error_type
    return response


def create_app(runtime: ApplicationRuntime) -> FastAPI:
    """Create the FastAPI application for the AuraBook daemon."""
    setup_logging(
        log_level=runtime.settings.log_level,
        dev_mode=runtime.settings.dev_mode,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "AuraBook starting with adapters=%s watch_root=%s",
            list(runtime.pipeline.adapters.keys()),
            runtime.settings.resolve_watch_root(),
        )
        await runtime.watcher.start()
        for service in runtime.background_services:
            await service.start()
        try:
            yield
        finally:
            for service in runtime.background_services:
                await service.stop()
            await runtime.watcher.stop()
            if runtime.cloud_client is not None:
                await runtime.cloud_client.close()
            for adapter in runtime.pipeline.adapters.values():
                close = getattr(adapter, "close", None)
                if callable(close):
                    await close()
            logger.info("AuraBook shut down")

    app = FastAPI(
        title="AuraBook Local Client",
        version="0.1.0",
        docs_url="/docs" if runtime.settings.dev_mode else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.state.runtime = runtime
    app.state.settings = runtime.settings

    app.add_exception_handler(AuraBookError, aurabook_error_handler)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(BodyLimitMiddleware)

    if runtime.settings.api_key:
        app.add_middleware(AuthMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in runtime.settings.cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from aurabook.server.routes import router

    app.include_router(router)
    return app
