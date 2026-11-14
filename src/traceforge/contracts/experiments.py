"""Experiment, rollout, and evaluation contracts."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import Field, model_validator

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
    # USD per 1K tokens. None = pricing unknown (cost is reported as unknown, never as $0);
    # 0.0 = explicitly free (e.g. a self-hosted endpoint). Set both or neither.
    price_per_1k_prompt: float | None = Field(default=None, ge=0)
    price_per_1k_completion: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _pricing_complete(self) -> ProviderConfig:
        if (self.price_per_1k_prompt is None) != (self.price_per_1k_completion is None):
            raise ValueError(
                "set both price_per_1k_prompt and price_per_1k_completion (use 0.0 for free), "
                "or neither (pricing unknown)"
            )
        return self

    @property
    def pricing_known(self) -> bool:
        return self.price_per_1k_prompt is not None and self.price_per_1k_completion is not None


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


class TrainingLineage(FrozenModel):
    """What the evaluated model was trained on. Checked against the evaluated tasks before an
    official benchmark run starts (task id, fixture and private-template overlap)."""

    dataset_version_ids: tuple[str, ...] = ()
    training_run_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    description: str = ""

    @property
    def declared(self) -> bool:
        return bool(self.dataset_version_ids or self.training_run_ids or self.task_ids)


BENCHMARK_V02_DEFAULTS: dict[str, object] = {
    "seeds": [],
    "run_mode": "official",
    "training_lineage": TrainingLineage().model_dump(mode="json"),
    "reward_thresholds": [0.1, 0.25, 0.5, 0.75, 0.9],
}
# Provider fields added in v0.3; hashed only when set, so earlier benchmark hashes are stable.
PROVIDER_V03_DEFAULTS: dict[str, object] = {
    "price_per_1k_prompt": None,
    "price_per_1k_completion": None,
}


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
    # v0.2 -------------------------------------------------------------------------------------
    # Repeated-seed execution: every task runs once per seed (provider seed = that seed).
    # Empty = a single episode per task with provider.seed (v0.1 behaviour).
    seeds: tuple[int, ...] = ()
    # "official" refuses to run when leakage/lineage checks fail. "diagnostic" runs anyway and
    # marks every artifact as non-official; it must be selected explicitly.
    run_mode: str = Field(default="official", pattern="^(official|diagnostic)$")
    training_lineage: TrainingLineage = TrainingLineage()
    reward_thresholds: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 0.9)

    @property
    def effective_seeds(self) -> tuple[int, ...]:
        return self.seeds or (self.provider.seed if self.provider.seed is not None else 0,)

    @property
    def official(self) -> bool:
        return self.run_mode == "official"

    @property
    def config_hash(self) -> str:
        # v0.2 fields are hashed only when changed from their defaults, so v0.1 hashes are stable.
        data = self.model_dump(mode="json")
        for key, default in BENCHMARK_V02_DEFAULTS.items():
            if data.get(key) == default:
                data.pop(key, None)
        provider = data["provider"]
        for key, default in PROVIDER_V03_DEFAULTS.items():
            if provider.get(key) == default:
                provider.pop(key, None)
        return sha256_json(data)


class ExperimentSpec(FrozenModel):
    name: str
    kind: str  # trajectory_quality | reward_hacking | sft_vs_grpo | generalization | robustness
    description: str = ""
    config: dict[str, JSONValue] = Field(default_factory=dict)

    @property
    def config_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))
