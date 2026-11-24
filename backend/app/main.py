"""FastAPI application factory + entry point.

`create_app()` builds a fresh app wired to a shared settings/DB/provider/
EventBus. The module-level `app` (used by `uvicorn app.main:app`) is a
concrete instance created on import. In tests, call `create_app()` with
injected deps to avoid global state.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import RouteCtx, mount_routes
from app.config import Settings, get_settings, reset_settings_cache
from app.observability.logging import setup_logging
from app.observability.tracing import setup_tracing
from app.persistence.db import DB, create_engine_and_session
from app.orchestrator import EventBus
from app.providers.music_provider import build_provider
from app.worker import build_worker

log = logging.getLogger(__name__)


def create_app(
    settings: Optional[Settings] = None,
    *,
    enable_cors: bool = True,
    run_embedded_worker: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    setup_tracing(settings.otel_endpoint)

    engine, factory = create_engine_and_session(settings)
    db = DB(factory)
    bus = EventBus()
    provider = build_provider(settings)

    # shared worker (embedded runner optional). `run_embedded_worker`
    # explicitly overrides the settings flag so callers (tests, embedders)
    # can opt out without mutating the shared Settings instance.
    worker, embedded = build_worker(settings, db, bus,
                                    embedded=run_embedded_worker)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("songforge starting v%s provider=%s worker=%s",
                 __version__, settings.provider,
                 "embedded" if embedded else "none")
        yield
        if embedded is not None:
            log.info("shutting down embedded worker")
            embedded.stop()
            worker.shutdown(wait=False)
        engine.dispose()
        log.info("engine disposed")

    app = FastAPI(
        title="SongForge API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    if enable_cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # stash on the app for route helpers / tests
    app.state.db = db
    app.state.settings = settings
    app.state.bus = bus
    app.state.worker = worker
    app.state.embedded = embedded

    ctx = RouteCtx(db=db, settings=settings, bus=bus)
    mount_routes(app, ctx)
    return app


def _build_default_app() -> FastAPI:
    # reset cached settings so the app picks up env at import time
    reset_settings_cache()
    return create_app(get_settings())


app = _build_default_app()
