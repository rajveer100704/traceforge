"""Verification result contracts."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import Field

from traceforge.contracts.common import FrozenModel, JSONValue, UTCDateTime, new_id, utc_now


class Verdict(StrEnum):
    PASS = "PASS"  # noqa: S105 - verdict enum, not a credential
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"


class CheckStatus(StrEnum):
    PASS = "pass"  # noqa: S105 - status enum, not a credential
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"
    SKIPPED = "skipped"


class CheckCategory(StrEnum):
    STATE = "state"
    EXECUTION = "execution"
    EVIDENCE = "evidence"
    REGRESSION = "regression"
    SAFETY = "safety"
    SELF_REPORT = "self_report"
    VISIBLE_TESTS = "visible_tests"


class EvidenceRecord(FrozenModel):
    kind: str
    sha256: str
    uri: str | None = None
    description: str = ""


class VerificationCheck(FrozenModel):
    name: str
    category: CheckCategory
    status: CheckStatus
    reason_code: str
    details: str = Field(default="", max_length=8000)
    duration_seconds: float = 0.0
    evidence: tuple[EvidenceRecord, ...] = ()
    metrics: dict[str, JSONValue] = Field(default_factory=dict)


class VerifierProfile(StrEnum):
    """V3 is authoritative. V1/V2 are research-only baselines for reward-hacking studies."""

    SELF_REPORT = "v1_self_report"
    VISIBLE_TESTS = "v2_visible_tests"
    FULL = "v3_full"


class VerificationResult(FrozenModel):
    verification_id: uuid.UUID = Field(default_factory=new_id)
    task_id: str
    task_version: str
    run_id: uuid.UUID | None = None
    trajectory_id: uuid.UUID | None = None
    profile: VerifierProfile = VerifierProfile.FULL
    authoritative: bool = True
    verdict: Verdict
    verifier_id: str
    verifier_version: str
    config_hash: str
    checks: tuple[VerificationCheck, ...]
    evidence: tuple[EvidenceRecord, ...] = ()
    failure_reasons: tuple[str, ...] = ()
    duration_seconds: float = 0.0
    executed_at: UTCDateTime = Field(default_factory=utc_now)
    agent_feedback: str = Field(default="", description="Non-leaking summary safe for agents")
    # Behavioural statistics used by bounded secondary reward components.
    stats: dict[str, JSONValue] = Field(default_factory=dict)

    def check(self, name: str) -> VerificationCheck | None:
        return next((c for c in self.checks if c.name == name), None)
