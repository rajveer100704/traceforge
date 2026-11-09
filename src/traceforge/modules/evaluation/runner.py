"""Benchmark runner: executes a provider over task splits and aggregates metrics.

Integrity is fail-closed: before any rollout the runner validates split disjointness, task
lineage, and (when declared) overlap between the evaluated tasks and the model's training
lineage. An ``official`` run refuses to start when any check fails; only an explicitly selected
``diagnostic`` run proceeds, and its artifacts are marked non-official.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from traceforge.contracts.common import ValidationFailure
from traceforge.contracts.experiments import BenchmarkConfig, RolloutSummary
from traceforge.contracts.verification import VerifierProfile
from traceforge.modules.agents.runner import CancelToken
from traceforge.modules.evaluation.contamination import (
    LeakageReport,
    check_task_splits,
    check_training_overlap,
)
from traceforge.modules.evaluation.metrics import (
    STATISTICAL_UNITS,
    group_by,
    per_task_rows,
    summarize,
    task_level_rows,
    threshold_sensitivity,
)
from traceforge.modules.tasks.registry import SPLIT_POLICY, SPLIT_POLICY_VERSION, TaskRegistry

if TYPE_CHECKING:  # typing only: modules must not import the application layer at runtime
    from traceforge.application.rollouts import RolloutArtifacts, RolloutExecutor


class BenchmarkIntegrityError(ValidationFailure):
    """An official benchmark run was refused because leakage/lineage validation failed."""

    def __init__(self, message: str, report: LeakageReport) -> None:
        super().__init__(message, details={"leakage": report.as_dict()})
        self.report = report


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

    def validate(
        self,
        config: BenchmarkConfig,
        bundles: list[Any] | None = None,
        *,
        training_task_ids: Iterable[str] = (),
        training_sources: Iterable[str] = (),
        lineage_problems: Iterable[str] = (),
    ) -> LeakageReport:
        """Run every integrity check; never raises (see ``enforce``)."""
        bundles = self.select(config) if bundles is None else bundles
        report = check_task_splits(self.registry.list())
        train_ids = sorted(set(training_task_ids) | set(config.training_lineage.task_ids))
        report.checks.append("training_overlap")
        report.training_task_ids = train_ids
        sources = set(training_sources)
        if config.training_lineage.task_ids:
            sources.add(f"declared_task_ids:{len(config.training_lineage.task_ids)}")
        report.training_sources = sorted(sources)
        report.evaluated_task_ids = sorted(b.spec.task_id for b in bundles)
        lookup = {b.spec.task_id: b for b in self.registry.list()}
        report.unlinked_training_task_ids = [t for t in train_ids if t not in lookup]
        report.training_overlap = list(lineage_problems) + check_training_overlap(
            train_ids, bundles, lookup
        )
        return report

    @staticmethod
    def enforce(config: BenchmarkConfig, report: LeakageReport) -> None:
        if report.ok or not config.official:
            return
        problems = report.problems + report.training_overlap
        raise BenchmarkIntegrityError(
            f"official benchmark '{config.name}' refused: {len(problems)} leakage/lineage "
            f"problem(s), e.g. {problems[0]}. Fix the data or set run_mode=diagnostic "
            "(results are then marked non-official).",
            report,
        )

    async def run(
        self,
        config: BenchmarkConfig,
        *,
        on_result: Callable[[RolloutArtifacts], None] | None = None,
        progress: Callable[[float], None] | None = None,
        cancel: CancelToken | None = None,
        benchmark_id: uuid.UUID | None = None,
        training_task_ids: Iterable[str] = (),
        training_sources: Iterable[str] = (),
        leakage: LeakageReport | None = None,
    ) -> BenchmarkResult:
        benchmark_id = benchmark_id or uuid.uuid4()
        bundles = self.select(config)
        if leakage is None:
            leakage = self.validate(
                config,
                bundles,
                training_task_ids=training_task_ids,
                training_sources=training_sources,
            )
        self.enforce(config, leakage)  # before any rollout
        started = time.time()
        t0 = time.perf_counter()
        runs: list[RolloutSummary] = []
        cancelled = False
        seeds = config.effective_seeds
        total = max(1, len(bundles) * len(seeds))
        done = 0
        for seed in seeds:
            provider = config.provider.model_copy(update={"seed": seed})
            for bundle in bundles:
                if cancel is not None and cancel.cancelled:
                    cancelled = True
                    break
                artifacts = await self.executor.run(
                    bundle,
                    provider,
                    reward_policy=config.reward_policy,
                    verifier_profile=VerifierProfile(config.verifier_profile),
                    correlation_id=f"bench:{benchmark_id}",
                )
                artifacts.summary.metadata["seed"] = seed
                runs.append(artifacts.summary)
                if on_result is not None:
                    on_result(artifacts)
                done += 1
                if progress is not None:
                    progress(done / total)
            if cancelled:
                break
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
            "by_seed": {k: s(v) for k, v in group_by(runs, "seed").items()},
            "per_task": per_task_rows(runs),
            "task_level": task_level_rows(runs),
            "reward_threshold_sensitivity": threshold_sensitivity(
                runs,
                config.reward_thresholds,
                confidence=config.confidence,
                bootstrap_samples=config.bootstrap_samples,
                seed=config.seed,
            ),
            "statistical_units": STATISTICAL_UNITS,
            "evidence": evidence_label(config, runs),
        }


SCRIPTED_LABEL = (
    "SCRIPTED-AGENT PIPELINE VALIDATION — deterministic demo_provider policies, not a learned "
    "model. These results test the environment/verifier/reward/statistics pipeline only."
)


def evidence_label(config: BenchmarkConfig, runs: list[RolloutSummary]) -> dict[str, Any]:
    scripted = config.provider.kind == "demo"
    seeds = config.effective_seeds
    return {
        "kind": "scripted_agent_pipeline_validation" if scripted else "model_evaluation",
        "learned_model_evidence": not scripted,
        "label": SCRIPTED_LABEL if scripted else "Model-provider evaluation.",
        "provider_kind": config.provider.kind,
        "model_id": config.provider.model_id,
        "policy": config.provider.policy if scripted else None,
        "official": config.official,
        "seeds": list(seeds),
        "repeated_seed_note": (
            "demo_provider is deterministic: repeated seeds reproduce identical episodes and add "
            "no information about variance; n_tasks is the effective sample size."
            if scripted and len(seeds) > 1
            else None
        ),
        "n_episodes": len(runs),
        "n_tasks": len({r.task_id for r in runs}),
    }
