"""Application services: orchestrate domain modules with persistence and object storage."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from traceforge.contracts.common import NotFoundError, sha256_json
from traceforge.contracts.datasets import CurationLevel, DatasetKind, QualityReport
from traceforge.contracts.experiments import (
    BenchmarkConfig,
    ExperimentSpec,
    RolloutRequest,
    RolloutSummary,
)
from traceforge.contracts.observations import EpisodeRecord
from traceforge.contracts.rewards import RewardBreakdown
from traceforge.contracts.verification import VerificationResult, VerifierProfile
from traceforge.domain.ports import ObjectStore
from traceforge.infrastructure.database import models as m
from traceforge.infrastructure.database.repositories import (
    ArtifactRepository,
    DatasetRepository,
    ExperimentRepository,
    RolloutRepository,
    TaskRepository,
    TrajectoryRepository,
)
from traceforge.infrastructure.database.session import Database
from traceforge.infrastructure.storage import content_key, key_from_uri
from traceforge.modules.agents.runner import CancelToken
from traceforge.modules.evaluation.reports import benchmark_document, benchmark_markdown
from traceforge.modules.evaluation.runner import BenchmarkResult, BenchmarkRunner
from traceforge.modules.rewards.engine import RewardEngine
from traceforge.modules.rewards.policies import PolicyRegistry
from traceforge.modules.tasks.registry import TaskRegistry
from traceforge.modules.trace_processing.dataset_builder import DatasetBuilder
from traceforge.modules.trace_processing.ingestion import IngestionPipeline, split_jsonl
from traceforge.modules.trace_processing.quality import QualityEngine, build_report
from traceforge.modules.trace_processing.validators import default_validators

from .rollouts import RolloutArtifacts, RolloutExecutor

log = logging.getLogger(__name__)


@dataclass
class ArtifactWriter:
    db: Database
    store: ObjectStore

    def put(
        self,
        key: str,
        data: bytes,
        kind: str,
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[uuid.UUID, str]:
        ref = self.store.put_bytes(key, data, media_type)
        with self.db.session() as s:
            artifact_id = ArtifactRepository(s).record(ref, kind, metadata)
        return artifact_id, ref.uri

    def put_content_addressed(
        self,
        prefix: str,
        data: bytes,
        kind: str,
        suffix: str = "",
        media_type: str = "application/octet-stream",
    ) -> tuple[uuid.UUID, str]:
        return self.put(content_key(prefix, data, suffix), data, kind, media_type)


# ----------------------------------------------------------------------------- tasks


class TaskService:
    def __init__(self, db: Database, registry: TaskRegistry, artifacts: ArtifactWriter) -> None:
        self.db, self.registry, self.artifacts = db, registry, artifacts

    def sync_registry(self) -> int:
        """Persist every registered task version. Oracle material goes to a restricted prefix."""
        count = 0
        for bundle in self.registry.list():
            oracle_doc = json.dumps(
                {
                    "oracle": bundle.oracle.model_dump(mode="json"),
                    "hidden_files": bundle.hidden_files,
                },
                sort_keys=True,
            ).encode()
            oracle_id, _ = self.artifacts.put_content_addressed(
                "oracle", oracle_doc, "task_oracle", ".json", "application/json"
            )
            with self.db.session() as s:
                TaskRepository(s).upsert_bundle(bundle, oracle_id)
            count += 1
        return count


# ----------------------------------------------------------------------------- ingestion


@dataclass
class IngestionSummary:
    source_uri: str
    raw_artifact_uri: str
    stored_trajectory_ids: list[str] = field(default_factory=list)
    quarantined: int = 0
    report: QualityReport = field(default_factory=QualityReport)


class IngestionService:
    def __init__(self, db: Database, artifacts: ArtifactWriter, registry: TaskRegistry) -> None:
        self.db, self.artifacts, self.registry = db, artifacts, registry

    def _known_task(self, task_id: str, version: str | None) -> bool:
        try:
            self.registry.bundle(task_id, version)
            return True
        except NotFoundError:
            return False

    def _known_verification(self, verification_id: object) -> bool:
        with self.db.session() as s:
            return s.get(m.VerificationRow, verification_id) is not None

    def ingest_bytes(self, source_name: str, data: bytes) -> IngestionSummary:
        # 1) persist raw bytes BEFORE any transformation
        raw_id, raw_uri = self.artifacts.put_content_addressed(
            "raw/traces", data, "raw_traces", ".jsonl", "application/x-ndjson"
        )
        with self.db.session() as s:
            repo = TrajectoryRepository(s)
            quality = QualityEngine(
                default_validators(self._known_task, self._known_verification),
                exists_hash=repo.exists_hash,
            )
            result = IngestionPipeline(quality).run(source_name, split_jsonl(data))
            summary = IngestionSummary(
                source_uri=source_name, raw_artifact_uri=raw_uri, report=result.report
            )
            for q, raw in result.quarantined:
                repo.quarantine(q, raw.sha256, raw_id)
                summary.quarantined += 1
            for trajectory, assessment, _raw in result.trajectories:
                if assessment.status.value == "duplicate":
                    continue
                repo.save(
                    trajectory,
                    raw_artifact_id=raw_id,
                    quality_status=assessment.status.value,
                    quality_score=assessment.score,
                )
                summary.stored_trajectory_ids.append(str(trajectory.trajectory_id))
        return summary

    def validate_bytes(self, source_name: str, data: bytes) -> QualityReport:
        quality = QualityEngine(default_validators(self._known_task))
        return IngestionPipeline(quality).run(source_name, split_jsonl(data)).report


# ----------------------------------------------------------------------------- datasets


class DatasetService:
    def __init__(self, db: Database, artifacts: ArtifactWriter, registry: TaskRegistry) -> None:
        self.db, self.artifacts, self.registry = db, artifacts, registry

    def _task_split(self, task_id: str) -> str | None:
        try:
            return self.registry.bundle(task_id).oracle.split.value
        except NotFoundError:
            return None

    def build(
        self,
        name: str,
        *,
        level: CurationLevel,
        kind: DatasetKind = DatasetKind.SFT,
        source_prefix: str | None = None,
    ) -> dict[str, Any]:
        with self.db.session() as s:
            q = select(m.TrajectoryRow.id)
            if source_prefix:
                q = q.where(m.TrajectoryRow.source_id.startswith(source_prefix))
            ids = list(s.scalars(q.order_by(m.TrajectoryRow.content_hash)))
            repo = TrajectoryRepository(s)
            trajectories = [repo.get(i) for i in ids]

        def known_verification(vid: object) -> bool:
            with self.db.session() as s:
                return s.get(m.VerificationRow, vid) is not None

        quality = QualityEngine(
            default_validators(lambda t, v: self._task_split(t) is not None, known_verification)
        )
        pairs = [(t, quality.assess(t)) for t in trajectories]
        report = build_report(len(pairs), [], pairs)
        built = DatasetBuilder().build(
            name, pairs, kind=kind, level=level, task_split=self._task_split
        )
        with self.db.session() as s:
            existing = s.scalar(
                select(m.DatasetVersion).where(
                    m.DatasetVersion.content_hash == built.manifest.content_hash
                )
            )
            if existing is not None:  # identical inputs + transformation => same immutable version
                uri = (
                    ArtifactRepository(s).get(existing.manifest_artifact_id).uri
                    if existing.manifest_artifact_id
                    else ""
                )
                return {
                    "dataset_version_id": str(existing.id),
                    "manifest_uri": uri,
                    "manifest": existing.manifest,
                    "quality": existing.quality_report,
                    "reused": True,
                }
        prefix = f"datasets/{name}/{built.manifest.version}"
        for fname, data in built.partitions.items():
            self.artifacts.put(
                f"{prefix}/{fname}", data, "dataset_partition", "application/vnd.apache.parquet"
            )
        manifest_bytes = built.manifest.model_dump_json(indent=2).encode()
        manifest_id, manifest_uri = self.artifacts.put(
            f"{prefix}/manifest.json", manifest_bytes, "dataset_manifest", "application/json"
        )
        quality_md = render_quality_markdown(name, built.manifest.version, report)
        self.artifacts.put(
            f"{prefix}/quality_report.md", quality_md.encode(), "quality_report", "text/markdown"
        )
        with self.db.session() as s:
            dv_id = DatasetRepository(s).save_version(
                built.manifest,
                report,
                [(uuid.UUID(t), h, split, status) for t, h, split, status in built.records],
                manifest_id,
            )
        return {
            "dataset_version_id": str(dv_id),
            "manifest_uri": manifest_uri,
            "manifest": built.manifest.model_dump(mode="json"),
            "quality": report.model_dump(mode="json"),
        }

    def get(self, dataset_version_id: uuid.UUID) -> dict[str, Any]:
        with self.db.session() as s:
            row = DatasetRepository(s).get(dataset_version_id)
            return {
                "dataset_version_id": str(row.id),
                "manifest": row.manifest,
                "quality": row.quality_report,
            }


def render_quality_markdown(name: str, version: str, r: QualityReport) -> str:
    rows = "\n".join(f"| {k} | {v} |" for k, v in r.by_reason.items()) or "| — | 0 |"
    return (
        f"# Quality report — {name} {version}\n\n"
        f"| Measure | Count |\n|---|---|\n| Total | {r.total_records} |\n| Parsed | {r.parsed} |\n"
        f"| Accepted | {r.accepted} |\n| Rejected | {r.rejected} |\n| Duplicates | {r.duplicates} |\n"
        f"| Near-duplicates | {r.near_duplicates} |\n| Invalid (quarantined) | {r.invalid} |\n"
        f"| Unknown outcomes | {r.unknown_outcomes} |\n"
        f"| Unverified claimed successes | {r.unverified_claimed_successes} |\n\n"
        f"## By outcome\n\n{json.dumps(r.by_outcome)}\n\n## Rejection reasons\n\n| Reason | Count |\n|---|---|\n"
        f"{rows}\n"
    )


# ----------------------------------------------------------------------------- rollouts


class RolloutService:
    def __init__(
        self,
        db: Database,
        artifacts: ArtifactWriter,
        registry: TaskRegistry,
        executor: RolloutExecutor,
    ) -> None:
        self.db, self.artifacts, self.registry, self.executor = db, artifacts, registry, executor

    def persist(
        self,
        art: RolloutArtifacts,
        reward_policy: str,
        *,
        job_id: uuid.UUID | None = None,
        evaluation_run_id: uuid.UUID | None = None,
    ) -> None:
        episode_bytes = art.episode.model_dump_json().encode()
        ep_id, _ = self.artifacts.put_content_addressed(
            "episodes", episode_bytes, "episode_record", ".json", "application/json"
        )
        policy = self.executor.policies.get(reward_policy)
        with self.db.session() as s:
            tv = TaskRepository(s).version_id(art.summary.task_id, art.summary.task_version)
            RolloutRepository(s).save_rollout(
                art.summary,
                art.trajectory,
                policy,
                task_version_id=tv,
                correlation_id=str(
                    art.summary.metadata.get("correlation_id") or art.summary.rollout_id
                ),
                job_id=job_id,
                evaluation_run_id=evaluation_run_id,
                episode_artifact_id=ep_id,
            )

    async def run(
        self,
        request: RolloutRequest,
        *,
        job_id: uuid.UUID | None = None,
        cancel: CancelToken | None = None,
        rollout_id: uuid.UUID | None = None,
    ) -> RolloutSummary:
        bundle = self.registry.bundle(request.task_id, request.task_version)
        art = await self.executor.run(
            bundle,
            request.provider,
            reward_policy=request.reward_policy,
            verifier_profile=VerifierProfile(request.verifier_profile),
            cancel=cancel,
            rollout_id=rollout_id,
        )
        self.persist(art, request.reward_policy, job_id=job_id)
        return art.summary

    def get(self, rollout_id: uuid.UUID) -> RolloutSummary:
        with self.db.session() as s:
            return RolloutRepository(s).get_rollout(rollout_id)

    def load_episode(self, rollout_id: uuid.UUID) -> EpisodeRecord:
        with self.db.session() as s:
            run = s.get(m.TaskRun, rollout_id)
            if run is None or run.episode_artifact_id is None:
                raise NotFoundError(f"rollout {rollout_id} has no stored episode")
            uri = ArtifactRepository(s).get(run.episode_artifact_id).uri
        return EpisodeRecord.model_validate_json(self.artifacts.store.get_bytes(key_from_uri(uri)))

    def reverify(
        self, rollout_id: uuid.UUID, profile: VerifierProfile, reward_policy: str | None = None
    ) -> tuple[VerificationResult, RewardBreakdown | None]:
        """Re-run independent verification on a stored, immutable episode record."""
        summary = self.get(rollout_id)
        episode = self.load_episode(rollout_id)
        bundle = self.registry.bundle(summary.task_id, summary.task_version)
        verification = self.executor.verifier.verify(
            bundle, episode, profile=profile, run_id=rollout_id, trajectory_id=summary.trajectory_id
        )
        reward = None
        with self.db.session() as s:
            repo = RolloutRepository(s)
            repo.save_verification(verification, rollout_id)
            if reward_policy:
                policy = self.executor.policies.get(reward_policy)
                reward = self.executor.rewards.compute(verification, policy)
                repo.save_reward(reward, policy)
        return verification, reward


# ----------------------------------------------------------------------------- rewards


class VerificationService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, verification_id: uuid.UUID) -> VerificationResult:
        with self.db.session() as s:
            return RolloutRepository(s).get_verification(verification_id)


class RewardService:
    def __init__(self, db: Database, policies: PolicyRegistry) -> None:
        self.db, self.policies, self.engine = db, policies, RewardEngine()

    def recompute(
        self, verification_id: uuid.UUID, policy_id: str, version: str | None
    ) -> dict[str, Any]:
        with self.db.session() as s:
            repo = RolloutRepository(s)
            verification = repo.get_verification(verification_id)
            stored = [
                r
                for r in repo.rewards_for_verification(verification_id)
                if r.policy_id == policy_id and (version is None or r.policy_version == version)
            ]
        policy = self.policies.get(policy_id, version)
        fresh = self.engine.compute(verification, policy)
        matches = [
            sha256_json(r.deterministic_view()) == sha256_json(fresh.deterministic_view())
            for r in stored
        ]
        return {
            "recomputed": fresh.model_dump(mode="json"),
            "stored_records": len(stored),
            "matches_stored": all(matches) if matches else None,
        }

    def get(self, reward_id: uuid.UUID) -> RewardBreakdown:
        with self.db.session() as s:
            return RolloutRepository(s).get_reward(reward_id)


# ----------------------------------------------------------------------------- evaluation


class EvaluationService:
    def __init__(
        self,
        db: Database,
        artifacts: ArtifactWriter,
        runner: BenchmarkRunner,
        rollouts: RolloutService,
        reports_dir: Path,
    ) -> None:
        self.db, self.artifacts, self.runner, self.rollouts = db, artifacts, runner, rollouts
        self.reports_dir = reports_dir

    async def run(
        self,
        config: BenchmarkConfig,
        *,
        progress: Any = None,
        cancel: CancelToken | None = None,
        experiment_id: uuid.UUID | None = None,
    ) -> tuple[BenchmarkResult, dict[str, Any]]:
        with self.db.session() as s:
            eval_id = ExperimentRepository(s).create_evaluation(
                config.name, config.model_dump(mode="json"), config.config_hash, experiment_id
            )

        def on_result(art: RolloutArtifacts) -> None:
            self.rollouts.persist(art, config.reward_policy, evaluation_run_id=eval_id)
            with self.db.session() as s:
                ExperimentRepository(s).add_result(eval_id, art.summary)

        result = await self.runner.run(
            config, on_result=on_result, progress=progress, cancel=cancel, benchmark_id=eval_id
        )
        doc = benchmark_document(result, {"evaluation_run_id": str(eval_id)})
        md = benchmark_markdown(doc)
        json_bytes = json.dumps(doc, indent=2, default=str).encode()
        report_id, report_uri = self.artifacts.put(
            f"reports/{eval_id}/report.json", json_bytes, "benchmark_report", "application/json"
        )
        _, md_uri = self.artifacts.put(
            f"reports/{eval_id}/report.md", md.encode(), "benchmark_report_md", "text/markdown"
        )
        out_dir = self.reports_dir / str(eval_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_bytes(json_bytes)
        (out_dir / "report.md").write_text(md, encoding="utf-8")
        status = "cancelled" if result.cancelled else "completed"
        with self.db.session() as s:
            ExperimentRepository(s).finish_evaluation(
                eval_id,
                {k: v for k, v in result.metrics.items() if k != "per_task"},
                report_id,
                status,
            )
        paths = {
            "report_json": str(out_dir / "report.json"),
            "report_md": str(out_dir / "report.md"),
            "report_uri": report_uri,
            "report_md_uri": md_uri,
        }
        return result, paths

    def get(self, evaluation_id: uuid.UUID) -> dict[str, Any]:
        with self.db.session() as s:
            row = ExperimentRepository(s).get_evaluation(evaluation_id)
            return {
                "evaluation_id": str(row.id),
                "name": row.name,
                "status": row.status,
                "config_hash": row.config_hash,
                "metrics": row.metrics,
                "report_artifact_id": str(row.report_artifact_id)
                if row.report_artifact_id
                else None,
            }


# ----------------------------------------------------------------------------- experiments


class ExperimentService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, spec: ExperimentSpec, code_commit: str | None = None) -> uuid.UUID:
        with self.db.session() as s:
            return ExperimentRepository(s).create_experiment(
                spec.name, spec.kind, spec.model_dump(mode="json"), spec.config_hash, code_commit
            )

    def get(self, experiment_id: uuid.UUID) -> dict[str, Any]:
        with self.db.session() as s:
            row = ExperimentRepository(s).get_experiment(experiment_id)
            runs = list(
                s.scalars(select(m.TrainingRun).where(m.TrainingRun.experiment_id == experiment_id))
            )
            evals = list(
                s.scalars(
                    select(m.EvaluationRun).where(m.EvaluationRun.experiment_id == experiment_id)
                )
            )
            return {
                "experiment_id": str(row.id),
                "name": row.name,
                "kind": row.kind,
                "config_hash": row.config_hash,
                "spec": row.spec,
                "code_commit": row.code_commit,
                "created_at": row.created_at.isoformat(),
                "training_runs": [
                    {
                        "training_run_id": str(r.id),
                        "mode": r.mode,
                        "status": r.status,
                        "base_model": r.base_model,
                        "blocked_reason": r.blocked_reason,
                        "metrics": r.metrics,
                    }
                    for r in runs
                ],
                "evaluation_runs": [
                    {"evaluation_id": str(e.id), "name": e.name, "status": e.status} for e in evals
                ],
            }
