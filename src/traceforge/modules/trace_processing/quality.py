"""Quality assessment, deduplication and labelling of normalised trajectories."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from traceforge.contracts.common import sha256_json
from traceforge.contracts.datasets import (
    QualityAssessment,
    QualityIssue,
    QualityReport,
    QualityStatus,
)
from traceforge.contracts.traces import ExecutionStatus, Outcome, StepRole, Trajectory
from traceforge.modules.trace_processing.validators import Validator, default_validators

WARNING_PENALTY = 0.1


def near_duplicate_signature(t: Trajectory) -> str:
    """Configurable normalised signature: structure + whitespace/case-folded content."""
    parts = []
    for s in t.steps:
        content = re.sub(r"\s+", " ", (s.content or "")).strip().lower()[:200]
        parts.append([s.role.value, s.tool_name or "", s.action_type or "", content])
    return sha256_json({"task": t.task_id, "steps": parts})


def labels_for(t: Trajectory, issues: Iterable[QualityIssue]) -> tuple[str, ...]:
    labels = {f"outcome:{t.outcome.value.lower()}", f"source:{t.source_type.value}"}
    tool_steps = [s for s in t.steps if s.role == StepRole.TOOL]
    if tool_steps:
        labels.add("has_tool_calls")
    if any(i.code == "unverified_claimed_success" for i in issues):
        labels.add("unverified_claimed_success")
    if t.metadata.get("demo_provider"):
        labels.add("demo_provider")
    if t.outcome in (Outcome.FAILURE, Outcome.PARTIAL) and t.verification_id is not None:
        patched = any(
            s.tool_name == "apply_patch" and s.execution_status == ExecutionStatus.OK
            for s in tool_steps
        )
        violations = any(s.execution_status == ExecutionStatus.REJECTED for s in tool_steps)
        # Informative: a verified, refuted *attempt* (a real patch, no policy violations) — not an
        # empty claim, a no-op, or a tampering attempt.
        if patched and not violations:
            labels.add("informative_failure")
    if t.outcome == Outcome.SUCCESS and t.verification_id is not None:
        labels.add("verified_success")
    return tuple(sorted(labels))


@dataclass
class QualityEngine:
    validators: list[Validator] = field(default_factory=default_validators)
    exists_hash: Callable[[str], bool] | None = None
    _seen_hashes: set[str] = field(default_factory=set)
    _seen_signatures: set[str] = field(default_factory=set)

    def assess(self, t: Trajectory) -> QualityAssessment:
        issues: list[QualityIssue] = []
        for v in self.validators:
            issues.extend(v.validate(t))
        duplicate = t.content_hash in self._seen_hashes or bool(
            self.exists_hash and self.exists_hash(t.content_hash)
        )
        signature = near_duplicate_signature(t)
        if not duplicate and signature in self._seen_signatures:
            issues.append(
                QualityIssue(
                    validator="duplicate",
                    severity="warning",
                    code="near_duplicate",
                    message="normalised signature matches an earlier record",
                )
            )
        self._seen_hashes.add(t.content_hash)
        self._seen_signatures.add(signature)
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        if duplicate:
            status = QualityStatus.DUPLICATE
            issues.append(
                QualityIssue(
                    validator="duplicate",
                    severity="error",
                    code="exact_duplicate",
                    message=f"content hash {t.content_hash[:12]} already ingested",
                )
            )
        elif errors:
            status = QualityStatus.REJECTED
        else:
            status = QualityStatus.ACCEPTED
        score = 0.0 if errors or duplicate else max(0.0, 1.0 - WARNING_PENALTY * len(warnings))
        return QualityAssessment(
            trajectory_id=t.trajectory_id,
            status=status,
            score=round(score, 6),
            issues=tuple(issues),
            labels=labels_for(t, issues),
        )


def build_report(
    total_records: int,
    quarantined_reasons: list[str],
    pairs: list[tuple[Trajectory, QualityAssessment]],
) -> QualityReport:
    by_reason: Counter[str] = Counter(quarantined_reasons)
    by_outcome: Counter[str] = Counter()
    by_label: Counter[str] = Counter()
    for t, a in pairs:
        by_outcome[t.outcome.value] += 1
        by_label.update(a.labels)
        for issue in a.issues:
            if issue.severity == "error":
                by_reason[issue.code] += 1
    accepted = [a for _, a in pairs if a.status == QualityStatus.ACCEPTED]
    return QualityReport(
        total_records=total_records,
        parsed=len(pairs),
        accepted=len(accepted),
        rejected=sum(a.status == QualityStatus.REJECTED for _, a in pairs),
        duplicates=sum(a.status == QualityStatus.DUPLICATE for _, a in pairs),
        near_duplicates=sum(any(i.code == "near_duplicate" for i in a.issues) for _, a in pairs),
        invalid=len(quarantined_reasons),
        unknown_outcomes=sum(t.outcome == Outcome.UNKNOWN for t, _ in pairs),
        unverified_claimed_successes=sum(
            "unverified_claimed_success" in a.labels for _, a in pairs
        ),
        by_outcome=dict(sorted(by_outcome.items())),
        by_reason=dict(sorted(by_reason.items())),
        by_label=dict(sorted(by_label.items())),
        mean_quality_score=(
            round(sum(a.score for a in accepted) / len(accepted), 6) if accepted else None
        ),
    )
