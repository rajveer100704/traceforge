"""Paired benchmark comparison request (v0.3 PR 2).

Three seeds are deliberately distinct:

* **benchmark episode seed**: ``metadata.seed`` of each episode (from
  ``BenchmarkConfig.effective_seeds``); part of the pair key;
* **provider generation seed**: ``BenchmarkConfig.provider.seed``; the runner sends each
  episode's seed to the provider, which may ignore it. It must match for official comparisons;
* **statistical bootstrap seed**: ``ComparisonRequest.bootstrap_seed``; used only to resample
  structural scenarios, independent of both runs.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field, model_validator

from traceforge.contracts.common import StrictModel

FINAL_COMPARISON_MIN_BOOTSTRAP_SAMPLES = 10_000

# BenchmarkConfig fields that change what is paired or how outcomes are defined: never variable.
NEVER_VARIABLE: tuple[str, ...] = (
    "splits",
    "families",
    "max_tasks_per_family",
    "verifier_profile",
    "seeds",
)
PROVIDER_SEED = "provider.seed"
# Fields that may differ between arms by default: the system under comparison and run labels.
DEFAULT_VARIED_FACTORS: tuple[str, ...] = (
    "name",
    "seed",
    "bootstrap_samples",
    "confidence",
    "report_tier",
    "run_mode",
    "training_lineage",
    "provider.kind",
    "provider.model_id",
    "provider.policy",
    "provider.base_url",
    "provider.api_key_env",
    "provider.price_per_1k_prompt",
    "provider.price_per_1k_completion",
)


class RunRef(StrictModel):
    """One side of a comparison: a database evaluation run, or a read-only bundle."""

    source: Literal["evaluation", "bundle"] = "evaluation"
    evaluation_id: uuid.UUID
    bundle_dir: str | None = None
    config_name: str | None = None

    @model_validator(mode="after")
    def _bundle_fields(self) -> RunRef:
        if self.source == "bundle" and not (self.bundle_dir and self.config_name):
            raise ValueError("a bundle run needs bundle_dir and config_name")
        if self.source == "evaluation" and (self.bundle_dir or self.config_name):
            raise ValueError("bundle_dir/config_name are only valid for source='bundle'")
        return self


class ComparisonRequest(StrictModel):
    baseline: RunRef
    candidate: RunRef
    coverage_policy: Literal["strict", "intersection"] = "strict"
    varied_factors: tuple[str, ...] = DEFAULT_VARIED_FACTORS
    metrics: tuple[str, ...] | None = None
    confidence: float = Field(0.95, gt=0, lt=1)
    bootstrap_samples: int = Field(2000, ge=100)
    bootstrap_seed: int = 0
    report_tier: Literal["development", "final"] = "development"
    include_pairs: bool = False

    @model_validator(mode="after")
    def _validate(self) -> ComparisonRequest:
        for path in self.varied_factors:
            root = path.split(".")[0]
            if root in NEVER_VARIABLE or path in NEVER_VARIABLE:
                raise ValueError(f"{path} can never differ between compared runs")
            if path == PROVIDER_SEED:
                raise ValueError(
                    "provider.seed cannot be declared variable: official comparisons require "
                    "it to match; use coverage_policy='intersection' for a diagnostic report"
                )
        if self.report_tier == "final":
            if self.coverage_policy != "strict":
                raise ValueError("a final comparison report requires coverage_policy='strict'")
            if self.bootstrap_samples < FINAL_COMPARISON_MIN_BOOTSTRAP_SAMPLES:
                raise ValueError(
                    "a final comparison report requires bootstrap_samples >= "
                    f"{FINAL_COMPARISON_MIN_BOOTSTRAP_SAMPLES}"
                )
            if self.baseline.source != "evaluation" or self.candidate.source != "evaluation":
                raise ValueError("a final comparison report requires database evaluation runs")
        return self
