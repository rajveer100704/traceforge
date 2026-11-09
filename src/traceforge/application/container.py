"""Composition root: builds every service from Settings via explicit constructor injection."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from traceforge.application.jobs import JobContext, JobService
from traceforge.application.rollouts import RolloutExecutor
from traceforge.application.services import (
    ArtifactWriter,
    DatasetService,
    EvaluationService,
    ExperimentService,
    IngestionService,
    RewardService,
    RolloutService,
    TaskService,
    VerificationService,
)
from traceforge.config import Settings
from traceforge.contracts.common import CapabilityUnavailable, JSONValue
from traceforge.contracts.datasets import CurationLevel, DatasetKind
from traceforge.contracts.experiments import BenchmarkConfig, RolloutRequest
from traceforge.contracts.jobs import JobKind
from traceforge.domain.ports import JobQueue, ObjectStore, SandboxProvider
from traceforge.infrastructure.database.session import Database
from traceforge.infrastructure.queue import InlineQueue, RQQueue
from traceforge.infrastructure.sandbox.local_provider import LocalTestSandboxProvider
from traceforge.infrastructure.storage import LocalObjectStore, S3ObjectStore
from traceforge.modules.agents.runner import AgentRunner, CancelToken
from traceforge.modules.evaluation.runner import BenchmarkRunner
from traceforge.modules.rewards.engine import RewardEngine
from traceforge.modules.rewards.policies import PolicyRegistry
from traceforge.modules.tasks.registry import TaskRegistry, default_registry
from traceforge.modules.verification.engine import VerificationEngine


def build_store(settings: Settings) -> ObjectStore:
    if settings.storage_backend == "s3":
        return S3ObjectStore(
            settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            access_key_id=settings.s3_access_key_id.get_secret_value()
            if settings.s3_access_key_id
            else None,
            secret_access_key=(
                settings.s3_secret_access_key.get_secret_value()
                if settings.s3_secret_access_key
                else None
            ),
            region=settings.s3_region,
            create_bucket=settings.env != "prod",
        )
    return LocalObjectStore(settings.artifacts_dir)


def build_sandbox(settings: Settings, kind: str | None = None) -> SandboxProvider:
    kind = kind or settings.sandbox_provider
    if kind == "docker":
        from traceforge.infrastructure.sandbox.docker_provider import DockerSandboxProvider

        provider = DockerSandboxProvider(
            image=settings.sandbox_image, base_dir=settings.data_dir / "sandboxes"
        )
        ok, why = provider.available()
        if not ok:
            raise CapabilityUnavailable(f"docker sandbox unavailable: {why}")
        return provider
    return LocalTestSandboxProvider(settings.data_dir / "sandboxes")


def build_queue(settings: Settings, background: bool) -> JobQueue:
    if settings.queue_backend == "rq":
        return RQQueue(settings.redis_url, settings.queue_name)
    return InlineQueue(background=background)


@dataclass
class Platform:
    settings: Settings
    db: Database
    store: ObjectStore
    sandbox: SandboxProvider
    registry: TaskRegistry
    policies: PolicyRegistry
    verifier: VerificationEngine
    executor: RolloutExecutor
    artifacts: ArtifactWriter
    tasks: TaskService
    ingestion: IngestionService
    datasets: DatasetService
    rollouts: RolloutService
    rewards: RewardService
    verifications: VerificationService
    experiments: ExperimentService
    evaluations: EvaluationService
    jobs: JobService

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        background_jobs: bool = False,
        migrate: bool = True,
        registry: TaskRegistry | None = None,
    ) -> Platform:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.resolved_database_url)
        if migrate:
            db.migrate()
        store = build_store(settings)
        sandbox = build_sandbox(settings)
        registry = registry if registry is not None else default_registry()
        policies = PolicyRegistry()
        verifier = VerificationEngine(sandbox)
        executor = RolloutExecutor(
            AgentRunner(
                sandbox, allow_untrusted_local_execution=settings.allow_untrusted_local_execution
            ),
            verifier,
            RewardEngine(),
            policies,
        )
        artifacts = ArtifactWriter(db, store)
        rollouts = RolloutService(db, artifacts, registry, executor)
        queue = build_queue(settings, background_jobs)
        jobs = JobService(
            db,
            queue,
            max_attempts=settings.job_max_attempts,
            lease_seconds=settings.stale_lease_seconds,
        )
        platform = cls(
            settings=settings,
            db=db,
            store=store,
            sandbox=sandbox,
            registry=registry,
            policies=policies,
            verifier=verifier,
            executor=executor,
            artifacts=artifacts,
            tasks=TaskService(db, registry, artifacts),
            ingestion=IngestionService(db, artifacts, registry),
            datasets=DatasetService(db, artifacts, registry),
            rollouts=rollouts,
            rewards=RewardService(db, policies),
            verifications=VerificationService(db),
            experiments=ExperimentService(db),
            evaluations=EvaluationService(
                db, artifacts, BenchmarkRunner(executor, registry), rollouts, settings.reports_dir
            ),
            jobs=jobs,
        )
        platform.evaluations.platform = platform  # lineage resolution needs DB + store
        if isinstance(queue, InlineQueue):
            queue.bind(jobs.execute)
        platform.register_handlers()
        platform.tasks.sync_registry()
        return platform

    # ------------------------------------------------------------------ job handlers
    def register_handlers(self) -> None:
        self.jobs.register(JobKind.ROLLOUT, self._handle_rollout)
        self.jobs.register(JobKind.EVALUATION, self._handle_evaluation)
        self.jobs.register(JobKind.DATASET_BUILD, self._handle_dataset)
        self.jobs.register(JobKind.INGEST, self._handle_ingest)
        self.jobs.register(JobKind.TRAINING, self._handle_training)

    def _cancel_token(self, ctx: JobContext) -> CancelToken:
        token = CancelToken()
        if ctx.cancelled():
            token.cancel()
        return token

    def _handle_rollout(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, JSONValue]:
        request = RolloutRequest.model_validate(payload["request"])
        rollout_id = uuid.UUID(payload["rollout_id"])
        summary = asyncio.run(
            self.rollouts.run(
                request, job_id=ctx.job_id, cancel=self._cancel_token(ctx), rollout_id=rollout_id
            )
        )
        return {
            "rollout_id": str(summary.rollout_id),
            "verdict": summary.verification.verdict.value,
            "reward": summary.reward.final_reward,
            "verification_id": str(summary.verification.verification_id),
            "reward_id": str(summary.reward.reward_id),
        }

    def _handle_evaluation(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, JSONValue]:
        config = BenchmarkConfig.model_validate(payload["config"])
        token = CancelToken()

        def progress(p: float) -> None:
            ctx.progress(p)
            if ctx.cancelled():
                token.cancel()

        result, paths = asyncio.run(self.evaluations.run(config, progress=progress, cancel=token))
        if result.cancelled:
            ctx.check_cancelled()
        overall = result.metrics["overall"]
        return {
            "evaluation_id": str(result.benchmark_id),
            "n": overall["n"],
            "verified_success_rate": overall["verified_success"]["rate"],
            **paths,
        }

    def _handle_dataset(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, JSONValue]:
        out = self.datasets.build(
            payload["name"],
            level=CurationLevel(payload.get("level", "d3_verified_success")),
            kind=DatasetKind(payload.get("kind", "sft")),
            source_prefix=payload.get("source_prefix"),
        )
        return {
            "dataset_version_id": out["dataset_version_id"],
            "manifest_uri": out["manifest_uri"],
            "version": out["manifest"]["version"],
        }

    def _handle_ingest(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, JSONValue]:
        data = payload["content"].encode("utf-8")
        summary = self.ingestion.ingest_bytes(payload.get("source_name", "api"), data)
        return {
            "stored": len(summary.stored_trajectory_ids),
            "quarantined": summary.quarantined,
            "report": summary.report.model_dump(mode="json"),
        }

    def _handle_training(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, JSONValue]:
        from traceforge.application.training import TrainingService

        return TrainingService(self).run(payload)

    def close(self, timeout: float = 10.0) -> None:
        """Graceful shutdown: let in-flight inline jobs finish (bounded), then release DB pools."""
        queue = self.jobs.queue
        if isinstance(queue, InlineQueue):
            queue.join(timeout)
        self.db.dispose()
