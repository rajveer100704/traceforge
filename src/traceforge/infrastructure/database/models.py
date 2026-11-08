"""SQLAlchemy 2.x ORM models (PostgreSQL in deployment; SQLite for local/test mode)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    """Stores UTC; always returns timezone-aware datetimes (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


# ----------------------------------------------------------------------------- artifacts


class Artifact(TimestampMixin, Base):
    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict)
    __table_args__ = (UniqueConstraint("uri", name="uq_artifacts_uri"),)


# ----------------------------------------------------------------------------- tasks


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    versions: Mapped[list[TaskVersion]] = relationship(back_populates="task")


class TaskVersion(TimestampMixin, Base):
    __tablename__ = "task_versions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    split: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(32), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    generator_version: Mapped[str] = mapped_column(String(32), nullable=False)
    fixture_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)  # public spec only
    oracle_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    task: Mapped[Task] = relationship(back_populates="versions")
    __table_args__ = (UniqueConstraint("task_pk", "version", name="uq_task_versions_task_version"),)


# ----------------------------------------------------------------------------- runs


class TaskRun(TimestampMixin, Base):
    __tablename__ = "task_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_versions.id"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    evaluation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evaluation_runs.id"), index=True
    )
    provider_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    sandbox_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    environment_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    reward_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    termination_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_actions: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    episode_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)


class TrajectoryRow(TimestampMixin, Base):
    __tablename__ = "trajectories"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    task_version: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[str | None] = mapped_column(String(256))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    claimed_outcome: Mapped[str | None] = mapped_column(String(16))
    verification_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    task_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task_runs.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    quality_status: Mapped[str | None] = mapped_column(String(32), index=True)
    quality_score: Mapped[float | None] = mapped_column(Float)
    raw_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict)
    steps: Mapped[list[TrajectoryStepRow]] = relationship(
        back_populates="trajectory",
        order_by="TrajectoryStepRow.sequence_number",
        cascade="all, delete-orphan",
    )


class TrajectoryStepRow(Base):
    __tablename__ = "trajectory_steps"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    trajectory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trajectories.id"), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(64), index=True)
    execution_status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    trajectory: Mapped[TrajectoryRow] = relationship(back_populates="steps")
    __table_args__ = (
        UniqueConstraint("trajectory_id", "sequence_number", name="uq_trajectory_steps_seq"),
    )


class QuarantineRow(TimestampMixin, Base):
    __tablename__ = "quarantine_records"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    line_number: Mapped[int | None] = mapped_column(Integer)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    reasons: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    details: Mapped[list[str]] = mapped_column(JSONType, nullable=False)


# ----------------------------------------------------------------------------- verification


class VerificationRow(TimestampMixin, Base):
    __tablename__ = "verifications"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_version: Mapped[str] = mapped_column(String(32), nullable=False)
    task_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task_runs.id"), index=True)
    trajectory_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    profile: Mapped[str] = mapped_column(String(32), nullable=False)
    authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    verifier_id: Mapped[str] = mapped_column(String(128), nullable=False)
    verifier_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)  # immutable result
    checks: Mapped[list[VerificationCheckRow]] = relationship(cascade="all, delete-orphan")


class VerificationCheckRow(Base):
    __tablename__ = "verification_checks"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    verification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verifications.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("verification_id", "name", name="uq_verification_checks_name"),
    )


# ----------------------------------------------------------------------------- rewards


class RewardPolicyRow(TimestampMixin, Base):
    __tablename__ = "reward_policies"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    research_only: Mapped[bool] = mapped_column(Boolean, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_reward_policies_version"),)


class RewardRecord(TimestampMixin, Base):
    __tablename__ = "reward_records"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    verification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verifications.id"), nullable=False
    )
    policy_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("reward_policies.id"), nullable=False)
    final_reward: Mapped[float] = mapped_column(Float, nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(16), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "verification_id", "policy_pk", "calculation_version", name="uq_reward_records"
        ),
    )


# ----------------------------------------------------------------------------- datasets


class Dataset(TimestampMixin, Base):
    __tablename__ = "datasets"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)


class DatasetVersion(TimestampMixin, Base):
    __tablename__ = "dataset_versions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dataset_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    manifest_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    __table_args__ = (UniqueConstraint("dataset_pk", "version", name="uq_dataset_versions"),)


class DatasetRecord(Base):
    __tablename__ = "dataset_records"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_versions.id"), nullable=False
    )
    trajectory_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    split: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    __table_args__ = (
        UniqueConstraint("dataset_version_id", "trajectory_id", name="uq_dataset_records"),
    )


# ----------------------------------------------------------------------------- jobs


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    input: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_now, onupdate=_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (Index("ix_jobs_status_dispatched", "status", "dispatched_at"),)


# ----------------------------------------------------------------------------- experiments


class Experiment(TimestampMixin, Base):
    __tablename__ = "experiments"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    code_commit: Mapped[str | None] = mapped_column(String(64))


class TrainingRun(TimestampMixin, Base):
    __tablename__ = "training_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiments.id"), index=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    base_model: Mapped[str] = mapped_column(String(256), nullable=False)
    base_model_revision: Mapped[str | None] = mapped_column(String(128))
    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dataset_versions.id"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    blocked_reason: Mapped[str | None] = mapped_column(Text)


class Checkpoint(TimestampMixin, Base):
    __tablename__ = "checkpoints"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    training_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("training_runs.id"), nullable=False
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict)
    __table_args__ = (UniqueConstraint("training_run_id", "step", name="uq_checkpoints_step"),)


class EvaluationRun(TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiments.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    report_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id"), nullable=False
    )
    task_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("task_runs.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    split: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    reward: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (
        UniqueConstraint("evaluation_run_id", "task_run_id", name="uq_evaluation_results"),
    )
