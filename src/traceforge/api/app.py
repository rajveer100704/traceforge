"""FastAPI application factory with lifecycle management.

Route handlers only translate HTTP <-> application services; no domain logic lives here.
Long-running work (rollouts, evaluations, dataset builds, training) is persisted as a job and
dispatched to a worker; handlers return 202 with a status URL.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from traceforge import __version__
from traceforge.api import error_handlers
from traceforge.api.routes import data, experiments, runs, system, tasks
from traceforge.application.container import Platform
from traceforge.config import Settings, get_settings
from traceforge.infrastructure.observability import (
    configure_logging,
    correlation_id_var,
    setup_tracing,
)

log = logging.getLogger(__name__)
MAX_CORRELATION_ID = 128

TAGS = [
    {"name": "health", "description": "Liveness and readiness probes (unauthenticated)."},
    {"name": "system", "description": "Capability report: sandboxes, providers, training modes."},
    {
        "name": "traces",
        "description": "Raw trajectory ingestion, normalisation and quality assessment.",
    },
    {"name": "datasets", "description": "Immutable, provenance-preserving dataset versions."},
    {"name": "tasks", "description": "Versioned benchmark tasks (public specs only)."},
    {"name": "rollouts", "description": "Agent episodes in restricted sandboxes (async jobs)."},
    {"name": "verifications", "description": "Independent verification results."},
    {"name": "rewards", "description": "Versioned reward breakdowns derived from verification."},
    {"name": "experiments", "description": "Experiment metadata and training jobs."},
    {"name": "evaluations", "description": "Benchmark runs with metrics and confidence intervals."},
    {"name": "jobs", "description": "Background job status and cancellation."},
]


def create_app(settings: Settings | None = None, platform: Platform | None = None) -> FastAPI:
    settings = settings or (platform.settings if platform else get_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        owned = platform is None
        app.state.platform = platform or Platform.build(settings, background_jobs=True)
        if settings.otel_enabled:
            setup_tracing("traceforge-api", console=settings.otel_console_export)
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
                from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

                FastAPIInstrumentor.instrument_app(app)
                SQLAlchemyInstrumentor().instrument(engine=app.state.platform.db.engine)
            except Exception as exc:  # instrumentation is best-effort
                log.warning("OpenTelemetry instrumentation unavailable: %s", exc)
        recovered = app.state.platform.jobs.recover()
        log.info("traceforge api started (recovery: %s)", recovered)
        try:
            yield
        finally:
            if owned:
                app.state.platform.close()
            app.state.platform = None
            log.info("traceforge api stopped")

    app = FastAPI(
        title="TraceForge",
        version=__version__,
        summary="Verifiable agent training & evaluation infrastructure",
        description=(
            "From raw agent experience to verifiably better agents. Authenticate with `X-API-Key` when "
            "the server is configured with TRACEFORGE_API_KEY. Long-running operations return 202 + job id."
        ),
        openapi_tags=TAGS,
        lifespan=lifespan,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["X-API-Key", "Content-Type", "Idempotency-Key", "X-Correlation-ID"],
        )

    @app.middleware("http")
    async def correlation(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Correlation-ID", "")
        cid = (
            incoming
            if 0 < len(incoming) <= MAX_CORRELATION_ID and incoming.isprintable()
            else str(uuid.uuid4())
        )
        request.state.correlation_id = cid
        token = correlation_id_var.set(cid)
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        response.headers["X-Correlation-ID"] = cid
        return response

    error_handlers.install(app)
    for router in (
        system.health,
        system.system,
        data.router,
        tasks.router,
        runs.router,
        experiments.router,
    ):
        app.include_router(router)
    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn traceforge.api.app:app_factory --factory`."""
    return create_app()
