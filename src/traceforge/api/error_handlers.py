"""Consistent error envelope: {"error": {code, message, details}, "correlation_id"}."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from traceforge.contracts.common import ErrorCode, TraceForgeError

log = logging.getLogger(__name__)

STATUS_FOR_CODE: dict[ErrorCode, int] = {
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.CONFLICT: status.HTTP_409_CONFLICT,
    ErrorCode.VALIDATION_ERROR: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorCode.CAPABILITY_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.SIZE_LIMIT: status.HTTP_413_CONTENT_TOO_LARGE,
}


def _body(
    request: Request, code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": details or {}},
        "correlation_id": getattr(request.state, "correlation_id", None),
    }


def install(app: FastAPI) -> None:
    @app.exception_handler(TraceForgeError)
    async def _domain(request: Request, exc: TraceForgeError) -> JSONResponse:
        code = exc.code
        http = STATUS_FOR_CODE.get(code, status.HTTP_400_BAD_REQUEST)
        return JSONResponse(_body(request, code.value, exc.message, exc.details), status_code=http)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": str(e.get("msg", ""))}
            for e in exc.errors()
        ]
        return JSONResponse(
            _body(
                request,
                ErrorCode.VALIDATION_ERROR.value,
                "request validation failed",
                {"errors": errors[:20]},
            ),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            413: "size_limit",
            503: "unavailable",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            _body(request, code, str(exc.detail)),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            _body(request, ErrorCode.INTERNAL.value, "internal server error"),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
