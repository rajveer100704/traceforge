"""Deterministic trajectory validators. Each returns QualityIssues (error => reject)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from traceforge.contracts.datasets import QualityIssue
from traceforge.contracts.traces import ExecutionStatus, Outcome, SourceType, StepRole, Trajectory
from traceforge.modules.trace_processing.normalization import SUPPORTED_SCHEMA_VERSIONS

ERROR, WARNING = "error", "warning"


class Validator(Protocol):
    name: str

    def validate(self, t: Trajectory) -> list[QualityIssue]: ...


def _issue(v: str, sev: str, code: str, msg: str) -> QualityIssue:
    return QualityIssue(validator=v, severity=sev, code=code, message=msg)


class SchemaValidator:
    name = "schema"

    def validate(self, t: Trajectory) -> list[QualityIssue]:
        out = []
        if t.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            out.append(_issue(self.name, ERROR, "unsupported_schema", t.schema_version))
        if not t.steps:
            out.append(_issue(self.name, ERROR, "empty_trajectory", "trajectory has no steps"))
        return out


class ChronologyValidator:
    name = "chronology"

    def validate(self, t: Trajectory) -> list[QualityIssue]:
        out = []
        last = None
        for s in t.steps:
            if s.started_at < t.started_at:
                out.append(
                    _issue(self.name, ERROR, "step_before_start", f"step {s.sequence_number}")
                )
            if t.completed_at and s.started_at > t.completed_at:
                out.append(_issue(self.name, ERROR, "step_after_end", f"step {s.sequence_number}"))
            if last is not None and s.started_at < last:
                out.append(
                    _issue(self.name, ERROR, "non_monotonic_time", f"step {s.sequence_number}")
                )
            last = s.started_at
        expected = (
            list(range(t.steps[0].sequence_number, t.steps[0].sequence_number + len(t.steps)))
            if t.steps
            else []
        )
        if [s.sequence_number for s in t.steps] != expected:
            out.append(
                _issue(self.name, WARNING, "sequence_gaps", "sequence numbers are not contiguous")
            )
        return out


class ToolCallValidator:
    name = "tool_calls"

    def validate(self, t: Trajectory) -> list[QualityIssue]:
        out = []
        issued: set[str] = set()
        for s in t.steps:
            if s.role == StepRole.ASSISTANT:
                for call in s.metadata.get("tool_calls", []) or []:
                    if isinstance(call, dict) and call.get("id"):
                        issued.add(str(call["id"]))
                    fn = call.get("function", {}) if isinstance(call, dict) else {}
                    if not fn.get("name"):
                        out.append(
                            _issue(
                                self.name,
                                ERROR,
                                "tool_call_missing_name",
                                f"step {s.sequence_number}",
                            )
                        )
            if s.role == StepRole.TOOL:
                call_id = s.metadata.get("call_id")
                if call_id and issued and str(call_id) not in issued:
                    out.append(
                        _issue(
                            self.name,
                            ERROR,
                            "orphan_tool_result",
                            f"step {s.sequence_number} answers unknown call {call_id}",
                        )
                    )
        return out


class ObservationShapeValidator:
    name = "observation_shape"

    def validate(self, t: Trajectory) -> list[QualityIssue]:
        out = []
        for s in t.steps:
            if s.role != StepRole.TOOL:
                continue
            if s.execution_status == ExecutionStatus.OK and s.observation is None:
                out.append(
                    _issue(self.name, WARNING, "missing_observation", f"step {s.sequence_number}")
                )
            if (
                s.execution_status in (ExecutionStatus.ERROR, ExecutionStatus.REJECTED)
                and s.error is None
            ):
                out.append(
                    _issue(self.name, WARNING, "error_without_detail", f"step {s.sequence_number}")
                )
        return out


@dataclass
class CompletionEvidenceValidator:
    known_verification: Callable[[object], bool] | None = None
    name: str = "completion_evidence"

    def validate(self, t: Trajectory) -> list[QualityIssue]:
        out = []
        # Fail closed: a verification reference counts only if it resolves to a stored verification.
        if t.verification_id is not None and (
            self.known_verification is None or not self.known_verification(t.verification_id)
        ):
            out.append(
                _issue(
                    self.name,
                    ERROR,
                    "dangling_verification_ref",
                    "outcome references a verification that does not exist",
                )
            )
        if t.claimed_outcome == Outcome.SUCCESS and t.verification_id is None:
            out.append(
                _issue(
                    self.name,
                    WARNING,
                    "unverified_claimed_success",
                    "success is claimed but not independently verified",
                )
            )
        if t.outcome == Outcome.UNKNOWN and t.claimed_outcome is None:
            out.append(
                _issue(self.name, WARNING, "unknown_outcome", "no verified or claimed outcome")
            )
        return out


@dataclass
class ReferentialIntegrityValidator:
    known_task: Callable[[str, str | None], bool] | None = None
    name: str = "referential_integrity"

    def validate(self, t: Trajectory) -> list[QualityIssue]:
        if t.task_id and self.known_task and not self.known_task(t.task_id, t.task_version):
            return [_issue(self.name, WARNING, "unknown_task", f"{t.task_id}@{t.task_version}")]
        return []


class ProvenanceValidator:
    name = "provenance"

    def validate(self, t: Trajectory) -> list[QualityIssue]:
        provenance, license_ = t.metadata.get("provenance"), t.metadata.get("license")
        severity = ERROR if t.source_type == SourceType.IMPORTED else WARNING
        out = []
        if not provenance:
            out.append(
                _issue(self.name, severity, "missing_provenance", "metadata.provenance is required")
            )
        if not license_:
            out.append(
                _issue(self.name, severity, "missing_license", "metadata.license is required")
            )
        return out


@dataclass
class SplitLeakageValidator:
    """Rejects trajectories whose task belongs to a split forbidden for this dataset."""

    task_split: Callable[[str], str | None]
    forbidden_splits: frozenset[str] = field(
        default_factory=lambda: frozenset({"public_test", "private_test"})
    )
    name: str = "split_leakage"

    def validate(self, t: Trajectory) -> list[QualityIssue]:
        if t.task_id is None:
            return []
        split = self.task_split(t.task_id)
        if split in self.forbidden_splits:
            return [_issue(self.name, ERROR, "split_leakage", f"{t.task_id} belongs to {split}")]
        return []


def default_validators(
    known_task: Callable[[str, str | None], bool] | None = None,
    known_verification: Callable[[object], bool] | None = None,
) -> list[Validator]:
    return [
        SchemaValidator(),
        ChronologyValidator(),
        ToolCallValidator(),
        ObservationShapeValidator(),
        CompletionEvidenceValidator(known_verification),
        ReferentialIntegrityValidator(known_task),
        ProvenanceValidator(),
    ]
