"""Repositories: translate between contracts and ORM rows. No domain decisions here."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.orm import Session, selectinload

from traceforge.contracts.common import ArtifactRef, ConflictError, NotFoundError, sha256_json
from traceforge.contracts.datasets import DatasetManifest, QualityReport
from traceforge.contracts.experiments import RolloutSummary
from traceforge.contracts.jobs import JobKind, JobStatus, JobView
from traceforge.contracts.rewards import RewardBreakdown, RewardPolicy
from traceforge.contracts.tasks import TaskBundle, TaskSpec
from traceforge.contracts.traces import QuarantineRecord, Trajectory, TrajectoryStep
from traceforge.contracts.verification import VerificationResult
from traceforge.domain.jobs import assert_transition, is_terminal
from traceforge.infrastructure.database import models as m


def _now() -> datetime:
    return datetime.now(UTC)


# ----------------------------------------------------------------------------- artifacts


class ArtifactRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def record(
        self, ref: ArtifactRef, kind: str, metadata: dict[str, Any] | None = None
    ) -> uuid.UUID:
        existing = self.s.scalar(select(m.Artifact).where(m.Artifact.uri == ref.uri))
        if existing is not None:
            if existing.sha256 != ref.sha256:
                raise ConflictError(f"artifact {ref.uri} checksum mismatch")
            return existing.id
        row = m.Artifact(
            id=ref.artifact_id,
            uri=ref.uri,
            sha256=ref.sha256,
            size_bytes=ref.size_bytes,
            media_type=ref.media_type,
            kind=kind,
            metadata_=metadata or {},
        )
        self.s.add(row)
        self.s.flush()
        return row.id

    def get(self, artifact_id: uuid.UUID) -> m.Artifact:
        row = self.s.get(m.Artifact, artifact_id)
        if row is None:
            raise NotFoundError(f"artifact {artifact_id} not found")
        return row


# ----------------------------------------------------------------------------- tasks


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def _insert_if_absent(
        self, model: type[Any], values: dict[str, Any], conflict_on: list[str]
    ) -> None:
        """INSERT ... ON CONFLICT DO NOTHING on the given unique columns (SQLite, PostgreSQL).

        Only a conflict on exactly those columns is absorbed; any other integrity error still
        raises. Callers re-read and validate the stored row afterwards.
        """
        dialect = self.s.get_bind().dialect.name
        stmt: Any
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = (
                pg_insert(model).values(**values).on_conflict_do_nothing(index_elements=conflict_on)
            )
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = (
                sqlite_insert(model)
                .values(**values)
                .on_conflict_do_nothing(index_elements=conflict_on)
            )
        else:  # pragma: no cover - only SQLite and PostgreSQL are supported
            raise ConflictError(f"unsupported database dialect for idempotent upsert: {dialect}")
        self.s.execute(stmt)

    def upsert_bundle(self, bundle: TaskBundle, oracle_artifact_id: uuid.UUID | None) -> uuid.UUID:
        """Idempotent, concurrency-safe registration of an immutable task version.

        Concurrent callers registering the same version all succeed and get the same row id;
        a different fixture hash under an existing (task_id, version) raises ConflictError.
        """
        spec, oracle = bundle.spec, bundle.oracle
        self._insert_if_absent(
            m.Task,
            {
                "id": uuid.uuid4(),
                "task_id": spec.task_id,
                "family_id": oracle.family_id,
                "category": spec.category,
            },
            ["task_id"],
        )
        task_pk = self.s.scalar(select(m.Task.id).where(m.Task.task_id == spec.task_id))
        if task_pk is None:  # pragma: no cover - the row was inserted or already existed
            raise ConflictError(f"task {spec.task_id} could not be registered")
        self._insert_if_absent(
            m.TaskVersion,
            {
                "id": uuid.uuid4(),
                "task_pk": task_pk,
                "version": spec.version,
                "split": oracle.split.value,
                "template_id": oracle.template_id,
                "seed": oracle.seed,
                "generator_version": oracle.generator_version,
                "fixture_hash": oracle.fixture_hash,
                "difficulty": spec.difficulty.value,
                "spec": spec.model_dump(mode="json"),
                "oracle_artifact_id": oracle_artifact_id,
            },
            ["task_pk", "version"],
        )
        row = self.s.execute(
            select(m.TaskVersion.id, m.TaskVersion.fixture_hash).where(
                m.TaskVersion.task_pk == task_pk, m.TaskVersion.version == spec.version
            )
        ).one()
        if row.fixture_hash != oracle.fixture_hash:
            raise ConflictError(
                f"task {spec.key} is immutable; content differs from registered version"
            )
        return uuid.UUID(str(row.id))

    def version_id(self, task_id: str, version: str) -> uuid.UUID:
        row = self.s.scalar(
            select(m.TaskVersion.id)
            .join(m.Task)
            .where(m.Task.task_id == task_id, m.TaskVersion.version == version)
        )
        if row is None:
            raise NotFoundError(f"task {task_id}@{version} not registered")
        return row

    def list_specs(
        self,
        *,
        split: str | None = None,
        family: str | None = None,
        limit: int = 100,
        offset: int = 0,
        allowed: Iterable[tuple[str, str]] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        q = select(m.TaskVersion, m.Task).join(m.Task)
        if allowed is not None:
            # Only versions the calling instance can execute (rows synced by other instances,
            # e.g. a newer release during a rolling upgrade, are not advertised).
            pairs = list(allowed)
            q = q.where(tuple_(m.Task.task_id, m.TaskVersion.version).in_(pairs))
        if split:
            q = q.where(m.TaskVersion.split == split)
        if family:
            q = q.where(m.Task.family_id == family)
        total = self.s.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = self.s.execute(q.order_by(m.Task.task_id).limit(limit).offset(offset)).all()
        return [
            {"spec": v.spec, "split": v.split, "family": t.family_id, "difficulty": v.difficulty}
            for v, t in rows
        ], total

    def get_spec(self, task_id: str, version: str | None = None) -> TaskSpec:
        q = select(m.TaskVersion).join(m.Task).where(m.Task.task_id == task_id)
        if version:
            q = q.where(m.TaskVersion.version == version)
        row = self.s.scalars(q.order_by(m.TaskVersion.created_at.desc())).first()
        if row is None:
            raise NotFoundError(f"task {task_id} not found")
        return TaskSpec.model_validate(row.spec)


# ----------------------------------------------------------------------------- trajectories


def _step_payload(step: TrajectoryStep) -> dict[str, Any]:
    return step.model_dump(mode="json")


class TrajectoryRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def save(
        self,
        trajectory: Trajectory,
        *,
        task_run_id: uuid.UUID | None = None,
        raw_artifact_id: uuid.UUID | None = None,
        quality_status: str | None = None,
        quality_score: float | None = None,
    ) -> uuid.UUID:
        existing = self.s.get(m.TrajectoryRow, trajectory.trajectory_id)
        if existing is not None:
            if existing.content_hash != trajectory.content_hash:
                raise ConflictError(
                    f"trajectory {trajectory.trajectory_id} already stored with other content"
                )
            return existing.id
        row = m.TrajectoryRow(
            id=trajectory.trajectory_id,
            schema_version=trajectory.schema_version,
            source_id=trajectory.source_id,
            source_type=trajectory.source_type.value,
            task_id=trajectory.task_id,
            task_version=trajectory.task_version,
            model_id=trajectory.model_id,
            outcome=trajectory.outcome.value,
            claimed_outcome=trajectory.claimed_outcome.value
            if trajectory.claimed_outcome
            else None,
            verification_id=trajectory.verification_id,
            task_run_id=task_run_id,
            content_hash=trajectory.content_hash,
            quality_status=quality_status,
            quality_score=quality_score,
            raw_artifact_id=raw_artifact_id,
            started_at=trajectory.started_at,
            completed_at=trajectory.completed_at,
            metadata_=trajectory.metadata,
        )
        row.steps = [
            m.TrajectoryStepRow(
                id=s.step_id,
                sequence_number=s.sequence_number,
                role=s.role.value,
                tool_name=s.tool_name,
                execution_status=s.execution_status.value,
                started_at=s.started_at,
                completed_at=s.completed_at,
                payload=_step_payload(s),
            )
            for s in trajectory.steps
        ]
        self.s.add(row)
        self.s.flush()
        return row.id

    def get(self, trajectory_id: uuid.UUID) -> Trajectory:
        row = self.s.scalar(
            select(m.TrajectoryRow)
            .options(selectinload(m.TrajectoryRow.steps))
            .where(m.TrajectoryRow.id == trajectory_id)
        )
        if row is None:
            raise NotFoundError(f"trajectory {trajectory_id} not found")
        return Trajectory.model_validate(
            {
                "trajectory_id": row.id,
                "schema_version": row.schema_version,
                "source_id": row.source_id,
                "source_type": row.source_type,
                "task_id": row.task_id,
                "task_version": row.task_version,
                "model_id": row.model_id,
                "started_at": row.started_at,
                "completed_at": row.completed_at,
                "steps": [s.payload for s in row.steps],
                "outcome": row.outcome,
                "claimed_outcome": row.claimed_outcome,
                "verification_id": row.verification_id,
                "content_hash": row.content_hash,
                "metadata": row.metadata_,
            }
        )

    def exists_hash(self, content_hash: str) -> bool:
        return (
            self.s.scalar(
                select(m.TrajectoryRow.id).where(m.TrajectoryRow.content_hash == content_hash)
            )
            is not None
        )

    def quarantine(
        self, record: QuarantineRecord, raw_sha256: str, raw_artifact_id: uuid.UUID | None
    ) -> None:
        self.s.add(
            m.QuarantineRow(
                source_uri=record.source_uri,
                line_number=record.line_number,
                raw_sha256=raw_sha256,
                raw_artifact_id=raw_artifact_id,
                reasons=record.reasons,
                details=record.details,
            )
        )


# ----------------------------------------------------------------------------- rollouts


class RolloutRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def ensure_policy(self, policy: RewardPolicy) -> uuid.UUID:
        row = self.s.scalar(
            select(m.RewardPolicyRow).where(
                m.RewardPolicyRow.policy_id == policy.policy_id,
                m.RewardPolicyRow.version == policy.version,
            )
        )
        if row is not None:
            if row.policy_hash != policy.policy_hash:
                raise ConflictError(
                    f"reward policy {policy.policy_id}@{policy.version} is immutable"
                )
            return row.id
        row = m.RewardPolicyRow(
            policy_id=policy.policy_id,
            version=policy.version,
            policy_hash=policy.policy_hash,
            research_only=policy.research_only,
            document=policy.model_dump(mode="json"),
        )
        self.s.add(row)
        self.s.flush()
        return row.id

    def save_verification(self, v: VerificationResult, task_run_id: uuid.UUID | None) -> uuid.UUID:
        existing = self.s.get(m.VerificationRow, v.verification_id)
        if existing is not None:
            return existing.id
        doc = v.model_dump(mode="json")
        row = m.VerificationRow(
            id=v.verification_id,
            task_id=v.task_id,
            task_version=v.task_version,
            task_run_id=task_run_id,
            trajectory_id=v.trajectory_id,
            profile=v.profile.value,
            authoritative=v.authoritative,
            verdict=v.verdict.value,
            verifier_id=v.verifier_id,
            verifier_version=v.verifier_version,
            config_hash=v.config_hash,
            executed_at=v.executed_at,
            duration_seconds=v.duration_seconds,
            content_sha256=sha256_json(doc),
            document=doc,
        )
        row.checks = [
            m.VerificationCheckRow(
                name=c.name,
                category=c.category.value,
                status=c.status.value,
                reason_code=c.reason_code,
            )
            for c in v.checks
        ]
        self.s.add(row)
        self.s.flush()
        return row.id

    def save_reward(self, reward: RewardBreakdown, policy: RewardPolicy) -> uuid.UUID:
        policy_pk = self.ensure_policy(policy)
        existing = self.s.scalar(
            select(m.RewardRecord).where(
                m.RewardRecord.verification_id == reward.verification_id,
                m.RewardRecord.policy_pk == policy_pk,
                m.RewardRecord.calculation_version == reward.calculation_version,
            )
        )
        if existing is not None:
            return existing.id
        row = m.RewardRecord(
            id=reward.reward_id,
            verification_id=reward.verification_id,
            policy_pk=policy_pk,
            final_reward=reward.final_reward,
            calculation_version=reward.calculation_version,
            document=reward.model_dump(mode="json"),
        )
        self.s.add(row)
        self.s.flush()
        return row.id

    def save_rollout(
        self,
        summary: RolloutSummary,
        trajectory: Trajectory,
        policy: RewardPolicy,
        *,
        task_version_id: uuid.UUID,
        correlation_id: str,
        job_id: uuid.UUID | None = None,
        evaluation_run_id: uuid.UUID | None = None,
        episode_artifact_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        if self.s.get(m.TaskRun, summary.rollout_id) is not None:
            return summary.rollout_id  # idempotent replay
        run = m.TaskRun(
            id=summary.rollout_id,
            task_version_id=task_version_id,
            job_id=job_id,
            evaluation_run_id=evaluation_run_id,
            provider_kind=summary.provider_kind,
            model_id=summary.model_id,
            sandbox_provider=summary.sandbox_provider,
            environment_ref=summary.environment_ref,
            reward_policy=f"{policy.policy_id}@{policy.version}",
            verifier_profile=summary.verification.profile.value,
            termination_reason=summary.termination_reason,
            tool_calls=summary.tool_calls,
            rejected_actions=summary.rejected_actions,
            duration_seconds=summary.duration_seconds,
            correlation_id=correlation_id,
            episode_artifact_id=episode_artifact_id,
            summary=summary.model_dump(mode="json"),
        )
        self.s.add(run)
        self.s.flush()
        TrajectoryRepository(self.s).save(trajectory, task_run_id=run.id)
        self.save_verification(summary.verification, run.id)
        self.save_reward(summary.reward, policy)
        return run.id

    def get_rollout(self, rollout_id: uuid.UUID) -> RolloutSummary:
        row = self.s.get(m.TaskRun, rollout_id)
        if row is None:
            raise NotFoundError(f"rollout {rollout_id} not found")
        return RolloutSummary.model_validate(row.summary)

    def get_verification(self, verification_id: uuid.UUID) -> VerificationResult:
        row = self.s.get(m.VerificationRow, verification_id)
        if row is None:
            raise NotFoundError(f"verification {verification_id} not found")
        return VerificationResult.model_validate(row.document)

    def get_reward(self, reward_id: uuid.UUID) -> RewardBreakdown:
        row = self.s.get(m.RewardRecord, reward_id)
        if row is None:
            raise NotFoundError(f"reward {reward_id} not found")
        return RewardBreakdown.model_validate(row.document)

    def rewards_for_verification(self, verification_id: uuid.UUID) -> list[RewardBreakdown]:
        rows = self.s.scalars(
            select(m.RewardRecord).where(m.RewardRecord.verification_id == verification_id)
        )
        return [RewardBreakdown.model_validate(r.document) for r in rows]


# ----------------------------------------------------------------------------- datasets


class DatasetRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def save_version(
        self,
        manifest: DatasetManifest,
        quality: QualityReport,
        records: list[tuple[uuid.UUID, str, str, str]],
        manifest_artifact_id: uuid.UUID | None,
    ) -> uuid.UUID:
        existing = self.s.scalar(
            select(m.DatasetVersion).where(m.DatasetVersion.content_hash == manifest.content_hash)
        )
        if existing is not None:
            return existing.id  # identical content => same immutable version
        ds = self.s.scalar(select(m.Dataset).where(m.Dataset.name == manifest.dataset_id))
        if ds is None:
            ds = m.Dataset(name=manifest.dataset_id, kind=manifest.kind.value)
            self.s.add(ds)
            self.s.flush()
        clash = self.s.scalar(
            select(m.DatasetVersion).where(
                m.DatasetVersion.dataset_pk == ds.id, m.DatasetVersion.version == manifest.version
            )
        )
        if clash is not None:
            raise ConflictError(
                f"dataset {manifest.dataset_id}@{manifest.version} exists with other content"
            )
        dv = m.DatasetVersion(
            dataset_pk=ds.id,
            version=manifest.version,
            content_hash=manifest.content_hash,
            manifest=manifest.model_dump(mode="json"),
            quality_report=quality.model_dump(mode="json"),
            manifest_artifact_id=manifest_artifact_id,
        )
        self.s.add(dv)
        self.s.flush()
        for trajectory_id, content_hash, split, status in records:
            self.s.add(
                m.DatasetRecord(
                    dataset_version_id=dv.id,
                    trajectory_id=trajectory_id,
                    content_hash=content_hash,
                    split=split,
                    quality_status=status,
                )
            )
        self.s.flush()
        return dv.id

    def get(self, dataset_version_id: uuid.UUID) -> m.DatasetVersion:
        row = self.s.get(m.DatasetVersion, dataset_version_id)
        if row is None:
            raise NotFoundError(f"dataset version {dataset_version_id} not found")
        return row


# ----------------------------------------------------------------------------- jobs


def job_view(row: m.Job) -> JobView:
    return JobView(
        job_id=row.id,
        kind=JobKind(row.kind),
        status=JobStatus(row.status),
        progress=row.progress,
        idempotency_key=row.idempotency_key,
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        cancel_requested=row.cancel_requested,
        correlation_id=row.correlation_id,
        error_code=row.error_code,
        error_message=row.error_message,
        input=row.input,
        output=row.output,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def create(
        self,
        kind: JobKind,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None,
        correlation_id: str,
        max_attempts: int,
    ) -> tuple[m.Job, bool]:
        """Returns (job, created). An existing idempotency key returns the original job."""
        if idempotency_key:
            existing = self.s.scalar(select(m.Job).where(m.Job.idempotency_key == idempotency_key))
            if existing is not None:
                if existing.kind != kind.value or existing.input != payload:
                    raise ConflictError("idempotency key reused with a different request")
                return existing, False
        job = m.Job(
            kind=kind.value,
            status=JobStatus.CREATED.value,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            input=payload,
            max_attempts=max_attempts,
        )
        self.s.add(job)
        self.s.flush()
        return job, True

    def get(self, job_id: uuid.UUID) -> m.Job:
        row = self.s.get(m.Job, job_id)
        if row is None:
            raise NotFoundError(f"job {job_id} not found")
        return row

    def transition(
        self,
        job_id: uuid.UUID,
        target: JobStatus,
        *,
        expected: JobStatus | None = None,
        **fields: Any,
    ) -> bool:
        """Compare-and-set transition. Returns False if another actor changed the job first."""
        row = self.get(job_id)
        self.s.refresh(row)
        current = JobStatus(row.status)
        if expected is not None and current != expected:
            return False
        assert_transition(current, target)
        values: dict[str, Any] = {
            "status": target.value,
            "version": row.version + 1,
            "updated_at": _now(),
            **fields,
        }
        if target == JobStatus.RUNNING:
            values.setdefault("started_at", _now())
        if is_terminal(target):
            values.setdefault("finished_at", _now())
        result = self.s.execute(
            update(m.Job)
            .where(m.Job.id == job_id, m.Job.version == row.version, m.Job.status == current.value)
            .values(**values)
        )
        self.s.flush()
        self.s.expire(row)
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def mark_dispatched(self, job_id: uuid.UUID) -> None:
        self.s.execute(update(m.Job).where(m.Job.id == job_id).values(dispatched_at=_now()))

    def undispatched(self, older_than_seconds: float = 0.0) -> list[uuid.UUID]:
        cutoff = _now() - timedelta(seconds=older_than_seconds)
        rows = self.s.scalars(
            select(m.Job.id).where(
                m.Job.status == JobStatus.CREATED.value, m.Job.created_at <= cutoff
            )
        )
        return list(rows)

    def stale_running(self, now: datetime | None = None) -> list[uuid.UUID]:
        now = now or _now()
        rows = self.s.scalars(
            select(m.Job.id).where(
                m.Job.status == JobStatus.RUNNING.value, m.Job.lease_expires_at < now
            )
        )
        return list(rows)

    def set_progress(self, job_id: uuid.UUID, progress: float) -> None:
        self.s.execute(
            update(m.Job).where(m.Job.id == job_id).values(progress=max(0.0, min(1.0, progress)))
        )

    def request_cancel(self, job_id: uuid.UUID) -> None:
        self.s.execute(update(m.Job).where(m.Job.id == job_id).values(cancel_requested=True))

    def cancel_requested(self, job_id: uuid.UUID) -> bool:
        return bool(self.s.scalar(select(m.Job.cancel_requested).where(m.Job.id == job_id)))


# ----------------------------------------------------------------------------- experiments


class ExperimentRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def create_experiment(
        self, name: str, kind: str, spec: dict[str, Any], config_hash: str, code_commit: str | None
    ) -> uuid.UUID:
        row = m.Experiment(
            name=name, kind=kind, spec=spec, config_hash=config_hash, code_commit=code_commit
        )
        self.s.add(row)
        self.s.flush()
        return row.id

    def get_experiment(self, experiment_id: uuid.UUID) -> m.Experiment:
        row = self.s.get(m.Experiment, experiment_id)
        if row is None:
            raise NotFoundError(f"experiment {experiment_id} not found")
        return row

    def create_evaluation(
        self,
        name: str,
        config: dict[str, Any],
        config_hash: str,
        experiment_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        row = m.EvaluationRun(
            name=name,
            config=config,
            config_hash=config_hash,
            status="running",
            experiment_id=experiment_id,
        )
        self.s.add(row)
        self.s.flush()
        return row.id

    def finish_evaluation(
        self,
        evaluation_id: uuid.UUID,
        metrics: dict[str, Any],
        report_artifact_id: uuid.UUID | None,
        status: str = "completed",
    ) -> None:
        row = self.s.get(m.EvaluationRun, evaluation_id)
        if row is None:
            raise NotFoundError(f"evaluation {evaluation_id} not found")
        row.metrics, row.report_artifact_id, row.status = metrics, report_artifact_id, status

    def add_result(self, evaluation_id: uuid.UUID, summary: RolloutSummary) -> None:
        self.s.add(
            m.EvaluationResult(
                evaluation_run_id=evaluation_id,
                task_run_id=summary.rollout_id,
                task_id=summary.task_id,
                split=str(summary.metadata.get("split")),
                family_id=str(summary.metadata.get("family")),
                verdict=summary.verification.verdict.value,
                reward=summary.reward.final_reward,
            )
        )

    def get_evaluation(self, evaluation_id: uuid.UUID) -> m.EvaluationRun:
        row = self.s.get(m.EvaluationRun, evaluation_id)
        if row is None:
            raise NotFoundError(f"evaluation {evaluation_id} not found")
        return row

    def create_training_run(
        self,
        *,
        mode: str,
        status: str,
        base_model: str,
        config: dict[str, Any],
        experiment_id: uuid.UUID | None = None,
        base_model_revision: str | None = None,
        blocked_reason: str | None = None,
        metrics: dict[str, Any] | None = None,
        dataset_version_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        row = m.TrainingRun(
            mode=mode,
            status=status,
            base_model=base_model,
            config=config,
            experiment_id=experiment_id,
            base_model_revision=base_model_revision,
            blocked_reason=blocked_reason,
            metrics=metrics or {},
            dataset_version_id=dataset_version_id,
        )
        self.s.add(row)
        self.s.flush()
        return row.id

    def add_checkpoint(
        self, training_run_id: uuid.UUID, step: int, uri: str, sha256: str, metadata: dict[str, Any]
    ) -> uuid.UUID:
        row = m.Checkpoint(
            training_run_id=training_run_id, step=step, uri=uri, sha256=sha256, metadata_=metadata
        )
        self.s.add(row)
        self.s.flush()
        return row.id
