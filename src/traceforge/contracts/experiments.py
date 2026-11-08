"""Experiment, rollout, and evaluation contracts."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import Field

from traceforge.contracts.common import FrozenModel, JSONValue, StrictModel, sha256_json
from traceforge.contracts.rewards import RewardBreakdown
from traceforge.contracts.tasks import Split
from traceforge.contracts.verification import Verdict, VerificationResult


class TrainingMode(StrEnum):
    DEMO = "demo"
    CPU_TEST = "cpu-test"
    SFT = "sft"
    GRPO = "grpo"


class ProviderConfig(FrozenModel):
    kind: str = "demo"  # demo | openai_compatible
    model_id: str = "demo_provider"
    policy: str = "reference"  # demo policy name
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1)
    timeout_seconds: float = Field(default=60.0, gt=0)
    seed: int | None = 0
    prompt_template_version: str = "agent-v1"


class RolloutRequest(FrozenModel):
    task_id: str
    task_version: str | None = None
    provider: ProviderConfig = ProviderConfig()
    sandbox: str = "local"
    reward_policy: str = "verified-v1"
    verifier_profile: str = "v3_full"
    seed: int = 0


class RolloutSummary(StrictModel):
    rollout_id: uuid.UUID
    task_id: str
    task_version: str
    provider_kind: str
    model_id: str
    sandbox_provider: str
    environment_ref: str
    trajectory_id: uuid.UUID
    steps: int
    tool_calls: int
    rejected_actions: int
    termination_reason: str
    duration_seconds: float
    verification: VerificationResult
    reward: RewardBreakdown
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @property
    def verdict(self) -> Verdict:
        return self.verification.verdict


class BenchmarkConfig(FrozenModel):
    name: str = "demo-benchmark"
    splits: tuple[Split, ...] = (Split.PUBLIC_TEST, Split.PRIVATE_TEST)
    families: tuple[str, ...] = ()
    max_tasks_per_family: int | None = None
    provider: ProviderConfig = ProviderConfig()
    sandbox: str = "local"
    reward_policy: str = "verified-v1"
    verifier_profile: str = "v3_full"
    seed: int = 0
    bootstrap_samples: int = Field(default=2000, ge=100)
    confidence: float = Field(default=0.95, gt=0.5, lt=1.0)

    @property
    def config_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class ExperimentSpec(FrozenModel):
    name: str
    kind: str  # trajectory_quality | reward_hacking | sft_vs_grpo | generalization | robustness
    description: str = ""
    config: dict[str, JSONValue] = Field(default_factory=dict)

    @property
    def config_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))
