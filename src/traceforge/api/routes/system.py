"""Health, readiness, capabilities and metrics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from traceforge import __version__
from traceforge.api.dependencies import get_platform, require_api_key
from traceforge.api.schemas import HealthResponse
from traceforge.application.container import Platform
from traceforge.application.system import capabilities
from traceforge.infrastructure.observability import METRICS

health = APIRouter(tags=["health"])
system = APIRouter(
    prefix="/api/v1/system", tags=["system"], dependencies=[Depends(require_api_key)]
)


@health.get("/health/live", response_model=HealthResponse)
def live() -> HealthResponse:
    return HealthResponse(status="ok", checks={"version": __version__})


@health.get(
    "/health/ready", response_model=HealthResponse, responses={503: {"model": HealthResponse}}
)
def ready(response: Response, platform: Platform = Depends(get_platform)) -> HealthResponse:
    checks: dict[str, Any] = {"database": platform.db.ping()}
    checks["migrations_current"] = checks["database"] and platform.db.is_up_to_date()
    ping = getattr(platform.jobs.queue, "ping", None)
    checks["queue"] = ping() if callable(ping) else True
    checks["queue_backend"] = platform.jobs.queue.backend_name
    checks["task_versions"] = platform.registry.supported_versions()
    checks["task_version_skew"] = platform.tasks.version_skew()
    info = ("queue_backend", "task_versions", "task_version_skew")
    ok = all(v for k, v in checks.items() if k not in info)
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="ready" if ok else "not_ready", checks=checks)


@health.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(METRICS.registry), media_type=CONTENT_TYPE_LATEST)


@system.get("/capabilities")
def get_capabilities(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    return capabilities(platform.settings, platform)
