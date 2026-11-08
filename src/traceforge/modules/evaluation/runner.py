"""Benchmark runner: executes a provider over task splits and aggregates metrics."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from traceforge.contracts.experiments import BenchmarkConfig, RolloutSummary
from traceforge.contracts.verification import VerifierProfile
from traceforge.modules.agents.runner import CancelToken
from traceforge.modules.evaluation.contamination import LeakageReport, check_task_splits
from traceforge.modules.evaluation.metrics import group_by, per_task_rows, summarize
from traceforge.modules.tasks.registry import SPLIT_POLICY, SPLIT_POLICY_VERSION, TaskRegistry

if TYPE_CHECKING:  # typing only: modules must not import the application layer at runtime
    from traceforge.application.rollouts import RolloutArtifacts, RolloutExecutor


@dataclass
class BenchmarkResult:
    benchmark_id: uuid.UUID
    config: BenchmarkConfig
    runs: list[RolloutSummary]
    metrics: dict[str, Any]
    leakage: LeakageReport
    started_at: float
    duration_seconds: float
    cancelled: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class BenchmarkRunner:
    def __init__(self, executor: RolloutExecutor, registry: TaskRegistry) -> None:
        self.executor = executor
        self.registry = registry

    def select(self, config: BenchmarkConfig) -> list[Any]:
        bundles = self.registry.list(splits=config.splits, families=config.families or None)
        if config.max_tasks_per_family:
            per: dict[str, int] = {}
            selected = []
            for b in bundles:
                count = per.get(b.oracle.family_id, 0)
                if count < config.max_tasks_per_family:
                    selected.append(b)
                    per[b.oracle.family_id] = count + 1
            bundles = selected
        return bundles

    async def run(
        self,
        config: BenchmarkConfig,
        *,
        on_result: Callable[[RolloutArtifacts], None] | None = None,
        progress: Callable[[float], None] | None = None,
        cancel: CancelToken | None = None,
        benchmark_id: uuid.UUID | None = None,
    ) -> BenchmarkResult:
        benchmark_id = benchmark_id or uuid.uuid4()
        bundles = self.select(config)
        leakage = check_task_splits(self.registry.list())
        started = time.time()
        t0 = time.perf_counter()
        runs: list[RolloutSummary] = []
        cancelled = False
        for i, bundle in enumerate(bundles):
            if cancel is not None and cancel.cancelled:
                cancelled = True
                break
            artifacts = await self.executor.run(
                bundle,
                config.provider,
                reward_policy=config.reward_policy,
                verifier_profile=VerifierProfile(config.verifier_profile),
                correlation_id=f"bench:{benchmark_id}",
            )
            runs.append(artifacts.summary)
            if on_result is not None:
                on_result(artifacts)
            if progress is not None:
                progress((i + 1) / max(1, len(bundles)))
        metrics = self.aggregate(runs, config)
        metrics["split_policy"] = {"id": SPLIT_POLICY, "version": SPLIT_POLICY_VERSION}
        return BenchmarkResult(
            benchmark_id=benchmark_id,
            config=config,
            runs=runs,
            metrics=metrics,
            leakage=leakage,
            started_at=started,
            duration_seconds=time.perf_counter() - t0,
            cancelled=cancelled,
        )

    @staticmethod
    def aggregate(runs: list[RolloutSummary], config: BenchmarkConfig) -> dict[str, Any]:
        def s(xs: list[RolloutSummary]) -> dict[str, Any]:
            return summarize(
                xs,
                confidence=config.confidence,
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed,
            )

        return {
            "overall": s(runs),
            "by_split": {k: s(v) for k, v in group_by(runs, "split").items()},
            "by_family": {k: s(v) for k, v in group_by(runs, "family").items()},
            "by_difficulty": {k: s(v) for k, v in group_by(runs, "difficulty").items()},
            "per_task": per_task_rows(runs),
        }
