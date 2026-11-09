"""Shared contract primitives: base models, identifiers, hashing, structured errors."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

JSONValue = Any  # JSON-serialisable value; validated at boundaries via canonical_json.


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return value.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(_require_aware)]


def canonical_json(value: Any) -> str:
    """Deterministic JSON encoding used for hashing (sorted keys, no whitespace)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


class StrictModel(BaseModel):
    """Base for all persisted/external contracts: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=False)


class FrozenModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAUTHORIZED_TOOL = "unauthorized_tool"
    UNKNOWN_TOOL = "unknown_tool"
    PATH_VIOLATION = "path_violation"
    PROTECTED_PATH = "protected_path"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    TERMINAL_STATE = "terminal_state"
    MALFORMED_ACTION = "malformed_action"
    SIZE_LIMIT = "size_limit"
    PATCH_FAILED = "patch_failed"
    SANDBOX_ERROR = "sandbox_error"
    VERIFIER_ERROR = "verifier_error"
    PROVIDER_ERROR = "provider_error"
    CANCELLED = "cancelled"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    INTERNAL = "internal"


class StructuredError(FrozenModel):
    code: ErrorCode
    message: str = Field(max_length=4000)
    retryable: bool = False
    details: dict[str, JSONValue] = Field(default_factory=dict)


class TraceForgeError(Exception):
    """Base domain exception carrying a structured error code."""

    code: ErrorCode = ErrorCode.INTERNAL
    retryable: bool = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_structured(self) -> StructuredError:
        return StructuredError(
            code=self.code, message=self.message, retryable=self.retryable, details=self.details
        )


class NotFoundError(TraceForgeError):
    code = ErrorCode.NOT_FOUND


class ConflictError(TraceForgeError):
    code = ErrorCode.CONFLICT


class UnsupportedTaskVersion(ConflictError):
    """The task exists but this instance cannot execute the requested version (never substituted)."""


class ValidationFailure(TraceForgeError):
    code = ErrorCode.VALIDATION_ERROR


class CapabilityUnavailable(TraceForgeError):
    code = ErrorCode.CAPABILITY_UNAVAILABLE


class ArtifactRef(FrozenModel):
    """Reference to an immutable, content-addressed artifact."""

    artifact_id: uuid.UUID
    uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = "application/octet-stream"


class Page[T](StrictModel):
    items: list[T]
    total: int
    limit: int
    offset: int
