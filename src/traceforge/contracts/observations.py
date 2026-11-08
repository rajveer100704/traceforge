"""Host-recorded episode facts consumed by verifiers (never agent-asserted)."""

from __future__ import annotations

from pydantic import Field

from traceforge.contracts.actions import EvidenceType
from traceforge.contracts.common import FrozenModel, JSONValue


class TestRunRecord(FrozenModel):
    __test__ = False  # not a pytest class

    run_id: str
    sequence_number: int
    selector: str
    total: int
    passed: int
    failed: int
    errors: int
    timed_out: bool
    harness_ok: bool
    after_patch_count: int
    output_sha256: str
    violations: tuple[str, ...] = ()
    case_status: dict[str, str] = Field(default_factory=dict)


class PatchRecord(FrozenModel):
    patch_id: str
    sequence_number: int
    path: str
    before_sha256: str
    after_sha256: str


class EvidenceSubmission(FrozenModel):
    sequence_number: int
    evidence_type: EvidenceType
    artifact_id: str
    explanation: str


class RejectedAction(FrozenModel):
    sequence_number: int
    tool_name: str
    code: str
    message: str


class FinalReport(FrozenModel):
    sequence_number: int
    summary: str
    claimed_success: bool


class EpisodeRecord(FrozenModel):
    """Everything the environment host observed during one episode."""

    task_id: str
    task_version: str
    run_id: str
    sandbox_provider: str
    environment_ref: str
    original_hashes: dict[str, str]
    final_files: dict[str, str]
    test_runs: tuple[TestRunRecord, ...] = ()
    patches: tuple[PatchRecord, ...] = ()
    file_reads: tuple[str, ...] = ()
    evidence: tuple[EvidenceSubmission, ...] = ()
    rejected_actions: tuple[RejectedAction, ...] = ()
    final_report: FinalReport | None = None
    tool_calls: int = 0
    max_actions: int = 1
    duration_seconds: float = 0.0
    termination_reason: str = "unknown"
    protected_tests: bool = True
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
