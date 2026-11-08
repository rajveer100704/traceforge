"""GRPO training entry point (real TRL GRPOTrainer; no simulated RL)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from traceforge.contracts.common import CapabilityUnavailable, sha256_json
from traceforge.modules.training.device import (
    DEVICE_PATTERN,
    PRECISION_PATTERN,
    DeviceInfo,
    grpo_device_arguments,
    host_environment,
    resolve_device,
)
from traceforge.modules.training.grpo.adapter import (
    EnvironmentDeps,
    assert_training_prompts_only,
    check_grpo_compatibility,
    make_environment_factory,
    rl_prompt_rows,
)

V02_FIELD_DEFAULTS: dict[str, object] = {"device": "auto", "precision": "auto"}


class GRPOTrainingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_model: str
    base_model_revision: str | None = None
    allow_download: bool = False
    output_dir: str = ".traceforge/checkpoints/grpo"
    max_steps: int = Field(default=10, ge=1)
    per_device_train_batch_size: int = Field(default=2, ge=1)
    num_generations: int = Field(default=2, ge=2)
    max_completion_length: int = Field(default=256, ge=16)
    max_tool_calling_iterations: int = Field(default=12, ge=1, le=100)
    learning_rate: float = 1e-6
    beta: float = 0.0
    seed: int = 0
    reward_policy: str = "verified-v1"
    resume_from_checkpoint: str | None = None
    device: str = Field(default="auto", pattern=DEVICE_PATTERN)
    precision: str = Field(default="auto", pattern=PRECISION_PATTERN)

    @property
    def config_hash(self) -> str:
        # Fields added in v0.2 are hashed only when set, so v0.1 config hashes stay stable.
        data = self.model_dump(mode="json")
        for key, default in V02_FIELD_DEFAULTS.items():
            if data.get(key) == default:
                data.pop(key, None)
        return sha256_json(data)


def grpo_arguments(config: GRPOTrainingConfig, device: DeviceInfo) -> dict[str, Any]:
    """Keyword arguments for ``trl.GRPOConfig``; device flags come only from ``device``."""
    return {
        "output_dir": config.output_dir,
        "max_steps": config.max_steps,
        "per_device_train_batch_size": config.per_device_train_batch_size,
        "num_generations": config.num_generations,
        "max_completion_length": config.max_completion_length,
        "max_tool_calling_iterations": config.max_tool_calling_iterations,
        "learning_rate": config.learning_rate,
        "beta": config.beta,
        "seed": config.seed,
        "report_to": "none",
        "logging_steps": 1,
        "save_strategy": "steps",
        "save_steps": config.max_steps,
        **grpo_device_arguments(device),
    }


def run_grpo(config: GRPOTrainingConfig, deps: EnvironmentDeps) -> dict[str, Any]:
    compat = check_grpo_compatibility()
    device = resolve_device(config.device, config.precision)
    try:
        from datasets import Dataset
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import GRPOConfig, GRPOTrainer
    except ImportError as exc:  # pragma: no cover
        raise CapabilityUnavailable("GRPO requires the ml extra (uv sync --extra ml)") from exc
    if config.allow_download and not config.base_model_revision:
        raise CapabilityUnavailable(
            "allow_download requires base_model_revision (a pinned commit) for supply-chain integrity"
        )
    kwargs: dict[str, Any] = {
        "revision": config.base_model_revision,
        "local_files_only": not config.allow_download,
    }
    try:
        tokenizer = AutoTokenizer.from_pretrained(config.base_model, **kwargs)  # nosec B615 - revision passed via kwargs
        model = AutoModelForCausalLM.from_pretrained(  # nosec B615 - revision passed via kwargs
            config.base_model, use_safetensors=True, **kwargs
        )
    except OSError as exc:
        raise CapabilityUnavailable(
            f"model {config.base_model} not available locally (set allow_download=true to fetch it)"
        ) from exc
    rows = rl_prompt_rows(deps.registry)
    if not rows:
        raise CapabilityUnavailable("no training-split tasks registered")
    assert_training_prompts_only(rows, deps.registry)
    deps.reward_policy = config.reward_policy
    args = GRPOConfig(**grpo_arguments(config, device))
    trainer = GRPOTrainer(
        model=model,
        processing_class=tokenizer,
        args=args,
        train_dataset=Dataset.from_list(rows),
        environment_factory=make_environment_factory(deps),
    )
    t0 = time.perf_counter()
    trainer.train(resume_from_checkpoint=config.resume_from_checkpoint)
    out = Path(config.output_dir)
    meta = {
        "mode": "grpo",
        "compatibility": compat,
        "base_model": config.base_model,
        "base_model_revision": config.base_model_revision,
        "config_hash": config.config_hash,
        "device": device.as_dict(),
        "environment": host_environment(),
        "prompt_task_ids": sorted(str(r["task_id"]) for r in rows),
        "steps": trainer.state.global_step,
        "seconds": time.perf_counter() - t0,
        "log_history": trainer.state.log_history,
        "rollouts": [r.__dict__ for r in deps.records],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "traceforge_metadata.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8"
    )
    return meta
