"""Job lifecycle contracts and the transition table."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import Field

from traceforge.contracts.common import FrozenModel, JSONValue, UTCDateTime


class JobStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMED_OUT, JobStatus.CANCELLED}
)

ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED}),
    # RUNNING -> QUEUED is the bounded retry path for retryable failures.
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.TIMED_OUT,
            JobStatus.CANCELLED,
            JobStatus.QUEUED,
        }
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.TIMED_OUT: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


class JobKind(StrEnum):
    INGEST = "ingest"
    DATASET_BUILD = "dataset_build"
    ROLLOUT = "rollout"
    VERIFICATION = "verification"
    EVALUATION = "evaluation"
    TRAINING = "training"
    DEMO = "demo"


class JobView(FrozenModel):
    job_id: uuid.UUID
    kind: JobKind
    status: JobStatus
    progress: float = Field(ge=0.0, le=1.0)
    idempotency_key: str | None
    attempt_count: int
    max_attempts: int
    cancel_requested: bool
    correlation_id: str
    error_code: str | None
    error_message: str | None
    input: dict[str, JSONValue]
    output: dict[str, JSONValue] | None
    created_at: UTCDateTime
    updated_at: UTCDateTime
    started_at: UTCDateTime | None
    finished_at: UTCDateTime | None


class JobAccepted(FrozenModel):
    job_id: uuid.UUID
    status: JobStatus
    status_url: str
