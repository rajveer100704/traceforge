"""Canonical trajectory schema (schema_version 1.0)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from traceforge.contracts.common import (
    SCHEMA_VERSION,
    JSONValue,
    StrictModel,
    StructuredError,
    UTCDateTime,
    new_id,
    sha256_json,
)


class SourceType(StrEnum):
    SYNTHETIC = "synthetic"
    IMPORTED = "imported"
    LIVE = "live"


class Outcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PARTIAL = "PARTIAL"
    TIMEOUT = "TIMEOUT"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class StepRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ExecutionStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    NOT_EXECUTED = "not_executed"


class TokenUsage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TrajectoryStep(StrictModel):
    step_id: uuid.UUID = Field(default_factory=new_id)
    sequence_number: int = Field(ge=0)
    role: StepRole
    content: str | None = Field(default=None, max_length=200_000)
    action_type: str | None = None
    tool_name: str | None = None
    action_id: str | None = None
    action_input: JSONValue | None = None
    observation: JSONValue | None = None
    started_at: UTCDateTime
    completed_at: UTCDateTime | None = None
    execution_status: ExecutionStatus = ExecutionStatus.OK
    token_usage: TokenUsage | None = None
    error: StructuredError | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> TrajectoryStep:
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError(f"step {self.sequence_number}: completed_at precedes started_at")
        if self.role == StepRole.TOOL and not self.tool_name:
            raise ValueError(f"step {self.sequence_number}: tool step requires tool_name")
        return self


class Trajectory(StrictModel):
    trajectory_id: uuid.UUID = Field(default_factory=new_id)
    schema_version: str = SCHEMA_VERSION
    source_id: str = Field(min_length=1, max_length=512)
    source_type: SourceType
    task_id: str | None = None
    task_version: str | None = None
    model_id: str | None = None
    started_at: UTCDateTime
    completed_at: UTCDateTime | None = None
    steps: list[TrajectoryStep] = Field(default_factory=list)
    outcome: Outcome = Outcome.UNKNOWN
    claimed_outcome: Outcome | None = None
    verification_id: uuid.UUID | None = None
    content_hash: str = ""
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Trajectory:
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("trajectory completed_at precedes started_at")
        previous = -1
        for step in self.steps:
            if step.sequence_number <= previous:
                raise ValueError(
                    f"sequence numbers must be strictly increasing (got {step.sequence_number} "
                    f"after {previous})"
                )
            previous = step.sequence_number
        # A SUCCESS outcome is only legitimate when backed by a verification record.
        if self.outcome == Outcome.SUCCESS and self.verification_id is None:
            raise ValueError("outcome SUCCESS requires verification_id; use claimed_outcome")
        if not self.content_hash:
            object.__setattr__(self, "content_hash", compute_content_hash(self))
        return self


def _step_fingerprint(step: TrajectoryStep) -> dict[str, Any]:
    return {
        "role": step.role.value,
        "content": step.content,
        "tool_name": step.tool_name,
        "action_type": step.action_type,
        "action_input": step.action_input,
        "observation": step.observation,
        "execution_status": step.execution_status.value,
        # Structural linkage between tool calls and results is part of the content.
        "tool_calls": step.metadata.get("tool_calls"),
        "call_id": step.metadata.get("call_id"),
    }


def compute_content_hash(trajectory: Trajectory) -> str:
    """Content hash over semantically meaningful fields (ids/timestamps excluded)."""
    payload = {
        "schema_version": trajectory.schema_version,
        "task_id": trajectory.task_id,
        "task_version": trajectory.task_version,
        "model_id": trajectory.model_id,
        "provenance": trajectory.metadata.get("provenance"),
        "license": trajectory.metadata.get("license"),
        "steps": [_step_fingerprint(s) for s in trajectory.steps],
    }
    return sha256_json(payload)


class RawRecord(StrictModel):
    """A raw source record as ingested, preserved independently of normalisation."""

    raw_id: uuid.UUID = Field(default_factory=new_id)
    source_uri: str
    line_number: int | None = None
    sha256: str
    payload: str
    received_at: datetime


class QuarantineRecord(StrictModel):
    raw_id: uuid.UUID
    source_uri: str
    line_number: int | None = None
    reasons: list[str]
    details: list[str] = Field(default_factory=list)
