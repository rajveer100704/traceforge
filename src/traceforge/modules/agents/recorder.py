"""Trajectory recorder: captures every message, action, observation and error."""

from __future__ import annotations

from datetime import datetime

from traceforge.contracts.common import JSONValue, StructuredError, utc_now
from traceforge.contracts.traces import (
    ExecutionStatus,
    Outcome,
    SourceType,
    StepRole,
    TokenUsage,
    Trajectory,
    TrajectoryStep,
)


class TrajectoryRecorder:
    def __init__(
        self,
        *,
        source_id: str,
        task_id: str,
        task_version: str,
        model_id: str,
        source_type: SourceType = SourceType.LIVE,
        metadata: dict[str, JSONValue] | None = None,
    ) -> None:
        self.source_id = source_id
        self.task_id = task_id
        self.task_version = task_version
        self.model_id = model_id
        self.source_type = source_type
        self.metadata = metadata or {}
        self.started_at = utc_now()
        self.steps: list[TrajectoryStep] = []

    def _next(self) -> int:
        return len(self.steps)

    def message(
        self,
        role: StepRole,
        content: str,
        *,
        started_at: datetime | None = None,
        token_usage: TokenUsage | None = None,
        metadata: dict[str, JSONValue] | None = None,
    ) -> TrajectoryStep:
        now = utc_now()
        step = TrajectoryStep(
            sequence_number=self._next(),
            role=role,
            content=content,
            started_at=started_at or now,
            completed_at=now,
            token_usage=token_usage,
            metadata=metadata or {},
        )
        self.steps.append(step)
        return step

    def tool(
        self,
        *,
        tool_name: str,
        action_id: str,
        action_input: JSONValue,
        observation: JSONValue,
        status: ExecutionStatus,
        started_at: datetime,
        error: StructuredError | None = None,
        metadata: dict[str, JSONValue] | None = None,
    ) -> TrajectoryStep:
        step = TrajectoryStep(
            sequence_number=self._next(),
            role=StepRole.TOOL,
            action_type="tool_call",
            tool_name=tool_name or "<missing>",
            action_id=action_id,
            action_input=action_input,
            observation=observation,
            started_at=started_at,
            completed_at=utc_now(),
            execution_status=status,
            error=error,
            metadata=metadata or {},
        )
        self.steps.append(step)
        return step

    def build(self, *, claimed: Outcome | None = None) -> Trajectory:
        # Outcome stays UNKNOWN until verification links a verdict to this trajectory.
        return Trajectory(
            source_id=self.source_id,
            source_type=self.source_type,
            task_id=self.task_id,
            task_version=self.task_version,
            model_id=self.model_id,
            started_at=self.started_at,
            completed_at=utc_now(),
            steps=list(self.steps),
            outcome=Outcome.UNKNOWN,
            claimed_outcome=claimed,
            metadata=dict(self.metadata),
        )
