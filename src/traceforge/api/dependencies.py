"""FastAPI dependencies: platform access, authentication, correlation ids, pagination."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Query, Request, status

from traceforge.application.container import Platform


def get_platform(request: Request) -> Platform:
    platform: Platform | None = getattr(request.app.state, "platform", None)
    if platform is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "platform not initialised")
    return platform


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    platform: Platform = Depends(get_platform),
) -> None:
    """Single shared-key boundary for single-user/small-team deployments.

    Disabled only when no key is configured (local/test). In prod a key is mandatory (Settings).
    Multi-tenant deployments need a real identity provider in front of this service.
    """
    expected = platform.settings.api_key
    if expected is None:
        return
    if x_api_key is None or not hmac.compare_digest(
        x_api_key.encode(), expected.get_secret_value().encode()
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing or invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", None) or str(uuid.uuid4())


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def pagination(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> Pagination:
    return Pagination(limit=limit, offset=offset)
