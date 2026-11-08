"""Dataset manifest and quality report contracts."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import Field

from traceforge.contracts.common import FrozenModel, StrictModel, UTCDateTime, utc_now


class QualityStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    QUARANTINED = "quarantined"


class DatasetKind(StrEnum):
    SFT = "sft"
    RL_PROMPTS = "rl_prompts"
    EVALUATION = "evaluation"
    CURATED = "curated"


class CurationLevel(StrEnum):
    """Experiment A ablation levels."""

    D1_RAW = "d1_raw"
    D2_SCHEMA_VALID = "d2_schema_valid"
    D3_VERIFIED_SUCCESS = "d3_verified_success"
    D4_VERIFIED_PLUS_INFORMATIVE = "d4_verified_plus_informative"


class QualityIssue(FrozenModel):
    validator: str
    severity: str  # "error" rejects; "warning" annotates
    code: str
    message: str


class QualityAssessment(FrozenModel):
    trajectory_id: uuid.UUID
    status: QualityStatus
    score: float = Field(ge=0.0, le=1.0)
    issues: tuple[QualityIssue, ...] = ()
    labels: tuple[str, ...] = ()


class QualityReport(StrictModel):
    total_records: int = 0
    parsed: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    near_duplicates: int = 0
    invalid: int = 0
    unknown_outcomes: int = 0
    unverified_claimed_successes: int = 0
    by_outcome: dict[str, int] = Field(default_factory=dict)
    by_reason: dict[str, int] = Field(default_factory=dict)
    by_label: dict[str, int] = Field(default_factory=dict)
    mean_quality_score: float | None = None


class PartitionFile(FrozenModel):
    name: str
    split: str
    path: str
    sha256: str
    num_rows: int


class DatasetManifest(FrozenModel):
    dataset_id: str
    version: str
    kind: DatasetKind
    curation_level: CurationLevel | None = None
    schema_version: str
    source_trajectories: tuple[tuple[str, str], ...]  # (trajectory_id, content_hash)
    transformation_id: str
    transformation_version: str
    code_commit: str | None = None
    split_policy: str
    split_policy_version: str
    counts_by_outcome: dict[str, int]
    counts_by_quality: dict[str, int]
    rejection_summary: dict[str, int]
    license: str
    provenance: str
    partitions: tuple[PartitionFile, ...]
    parent_versions: tuple[str, ...] = ()
    created_at: UTCDateTime = Field(default_factory=utc_now)
    content_hash: str = ""
