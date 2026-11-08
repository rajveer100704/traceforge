"""Reward policy and reward breakdown contracts."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import Field, model_validator

from traceforge.contracts.common import FrozenModel, UTCDateTime, new_id, sha256_json, utc_now

CALCULATION_VERSION = "1"


class ComponentKind(StrEnum):
    TASK_COMPLETION = "task_completion"
    REGRESSION = "regression_quality"
    EVIDENCE = "evidence_quality"
    EFFICIENCY = "efficiency"
    SAFETY = "safety_compliance"
    SELF_REPORT = "self_report"
    VISIBLE_TESTS = "visible_tests"


class GateKind(StrEnum):
    VERDICT_PASS = "verdict_pass"  # noqa: S105 - enum value, not a credential
    SAFETY = "safety"
    EVIDENCE_INTEGRITY = "evidence_integrity"
    REGRESSION = "regression"
    AUTHORITATIVE = "authoritative"


class ComponentSpec(FrozenModel):
    kind: ComponentKind
    weight: float = Field(ge=0.0, le=1.0)
    floor: float = 0.0
    cap: float = 1.0


class RewardPolicy(FrozenModel):
    policy_id: str
    version: str
    description: str = ""
    components: tuple[ComponentSpec, ...]
    hard_gates: tuple[GateKind, ...]
    research_only: bool = False

    @model_validator(mode="after")
    def _check(self) -> RewardPolicy:
        kinds = [c.kind for c in self.components]
        if len(set(kinds)) != len(kinds):
            raise ValueError("duplicate component kinds")
        total = sum(c.weight for c in self.components)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"component weights must sum to 1.0 (got {total})")
        weights = {c.kind: c.weight for c in self.components}
        completion = weights.get(ComponentKind.TASK_COMPLETION, 0.0)
        if weights.get(ComponentKind.EFFICIENCY, 0.0) > completion and not self.research_only:
            raise ValueError("efficiency weight must never exceed task completion weight")
        if not self.research_only:
            required = {GateKind.VERDICT_PASS, GateKind.SAFETY, GateKind.AUTHORITATIVE}
            if not required.issubset(set(self.hard_gates)):
                raise ValueError(f"non-research policies require gates {sorted(required)}")
        return self

    @property
    def policy_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class ComponentValue(FrozenModel):
    name: ComponentKind
    raw_value: float
    normalized_value: float
    weight: float
    floor: float
    cap: float
    weighted_value: float
    rationale: str


class GateOutcome(FrozenModel):
    gate: GateKind
    passed: bool
    rationale: str


class RewardBreakdown(FrozenModel):
    reward_id: uuid.UUID = Field(default_factory=new_id)
    policy_id: str
    policy_version: str
    policy_hash: str
    verification_id: uuid.UUID
    components: tuple[ComponentValue, ...]
    gates: tuple[GateOutcome, ...]
    final_reward: float
    calculation_version: str = CALCULATION_VERSION
    computed_at: UTCDateTime = Field(default_factory=utc_now)

    def deterministic_view(self) -> dict[str, object]:
        """Fields that must be identical for identical verification + policy."""
        data = self.model_dump(mode="json", exclude={"reward_id", "computed_at"})
        return data
