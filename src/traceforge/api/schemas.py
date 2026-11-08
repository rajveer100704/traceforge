"""Request/response schemas for the control plane (external boundary: strict validation)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from traceforge.contracts.datasets import CurationLevel, DatasetKind
from traceforge.contracts.experiments import ProviderConfig
from traceforge.contracts.jobs import JobStatus
from traceforge.contracts.verification import VerifierProfile


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(Strict):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(Strict):
    error: ErrorBody
    correlation_id: str | None = None


class HealthResponse(Strict):
    status: str
    checks: dict[str, Any] = Field(default_factory=dict)


class JobAcceptedResponse(Strict):
    job_id: uuid.UUID
    status: JobStatus
    status_url: str
    resource_id: str | None = None
    resource_url: str | None = None


class IngestRequest(Strict):
    source_name: str = Field(
        default="api", min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:\-/]+$"
    )
    content: str = Field(min_length=1, description="JSONL: one trajectory record per line")


class IngestResponse(Strict):
    source_uri: str
    raw_artifact_uri: str
    stored: int
    quarantined: int
    stored_trajectory_ids: list[str]
    report: dict[str, Any]


class DatasetBuildRequest(Strict):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_\-]*$")
    level: CurationLevel = CurationLevel.D3_VERIFIED_SUCCESS
    kind: DatasetKind = DatasetKind.SFT
    source_prefix: str | None = Field(default=None, max_length=256)


class TaskCreateRequest(Strict):
    """Register a new deterministic instance of a known task family (oracle generated server-side)."""

    family: str
    template_id: str = Field(pattern=r"^t\d+$")
    seed: int = Field(ge=0, le=1_000_000)


class TaskListItem(Strict):
    spec: dict[str, Any]
    split: str
    family: str
    difficulty: str


class TaskPage(Strict):
    items: list[TaskListItem]
    total: int
    limit: int
    offset: int


class RolloutCreateRequest(Strict):
    task_id: str
    task_version: str | None = None
    provider: ProviderConfig = ProviderConfig()
    reward_policy: str = "verified-v1"
    verifier_profile: VerifierProfile = VerifierProfile.FULL
    seed: int = 0


class VerificationCreateRequest(Strict):
    rollout_id: uuid.UUID
    profile: VerifierProfile = VerifierProfile.FULL
    reward_policy: str | None = "verified-v1"


class VerificationCreateResponse(Strict):
    verification: dict[str, Any]
    reward: dict[str, Any] | None


class ExperimentCreateRequest(Strict):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(
        pattern=r"^(trajectory_quality|reward_hacking|sft_vs_grpo|generalization|robustness|custom)$"
    )
    description: str = Field(default="", max_length=4000)
    config: dict[str, Any] = Field(default_factory=dict)


class TrainRequest(Strict):
    mode: str = Field(pattern=r"^(cpu-test|sft|grpo)$")
    dataset_version_id: uuid.UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class CancelResponse(Strict):
    job_id: uuid.UUID
    status: JobStatus
    cancel_requested: bool
