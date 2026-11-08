"""Persistence, jobs/queue semantics, storage, API, benchmark and experiments."""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

from tests.conftest import EXAMPLE_TRACES
from traceforge.application.jobs import JobService
from traceforge.contracts.common import ConflictError, ErrorCode, TraceForgeError
from traceforge.contracts.datasets import CurationLevel
from traceforge.contracts.experiments import BenchmarkConfig, ProviderConfig, RolloutRequest
from traceforge.contracts.jobs import JobKind, JobStatus
from traceforge.contracts.tasks import Split
from traceforge.infrastructure.database.session import Database
from traceforge.infrastructure.queue import InlineQueue, RQQueue
from traceforge.infrastructure.storage import LocalObjectStore, S3ObjectStore

# ----------------------------------------------------------------------------- persistence


def test_migrations_up_to_date_and_no_drift(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from traceforge.infrastructure.database.models import Base

    db = Database(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    db.migrate()
    assert db.is_up_to_date()
    with db.engine.connect() as conn:
        assert (
            compare_metadata(
                MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata
            )
            == []
        )


def test_rollout_persistence_and_reward_recompute(platform) -> None:  # type: ignore[no-untyped-def]
    s = asyncio.run(platform.rollouts.run(RolloutRequest(task_id="sqli-t1-s000")))
    stored = platform.rollouts.get(s.rollout_id)
    assert stored.verification.verdict == s.verification.verdict
    assert (
        platform.verifications.get(s.verification.verification_id).config_hash
        == s.verification.config_hash
    )
    assert platform.rewards.get(s.reward.reward_id).final_reward == s.reward.final_reward
    rc = platform.rewards.recompute(s.verification.verification_id, "verified-v1", "1.0.0")
    assert rc["matches_stored"] is True
    from traceforge.infrastructure.database.repositories import TrajectoryRepository

    with platform.db.session() as sess:
        t = TrajectoryRepository(sess).get(s.trajectory_id)
    assert len(t.steps) == s.steps and t.verification_id == s.verification.verification_id
    # Re-verification of the stored, immutable episode reproduces the verdict.
    v, _ = platform.rollouts.reverify(s.rollout_id, s.verification.profile, None)
    assert v.verdict == s.verification.verdict


def test_ingest_and_dataset_build(platform) -> None:  # type: ignore[no-untyped-def]
    summary = platform.ingestion.ingest_bytes("ex.jsonl", EXAMPLE_TRACES.read_bytes())
    assert summary.quarantined == 4 and summary.stored_trajectory_ids
    again = platform.ingestion.ingest_bytes("ex.jsonl", EXAMPLE_TRACES.read_bytes())
    assert again.stored_trajectory_ids == []  # everything is now an exact duplicate
    asyncio.run(platform.rollouts.run(RolloutRequest(task_id="authz-t0-s000")))
    out = platform.datasets.build("t", level=CurationLevel.D3_VERIFIED_SUCCESS)
    assert out["manifest"]["counts_by_outcome"] == {"SUCCESS": 1}
    assert (
        platform.datasets.get(uuid.UUID(out["dataset_version_id"]))["quality"]["total_records"] > 0
    )
    assert (
        platform.datasets.build("t", level=CurationLevel.D3_VERIFIED_SUCCESS)["dataset_version_id"]
        == out["dataset_version_id"]
    )


def test_task_versions_immutable_in_db(platform, registry) -> None:  # type: ignore[no-untyped-def]
    from traceforge.infrastructure.database.repositories import TaskRepository

    bundle = registry.list()[0]
    tampered = bundle.model_copy(
        update={"oracle": bundle.oracle.model_copy(update={"fixture_hash": "1" * 64})}
    )
    with pytest.raises(ConflictError), platform.db.session() as s:
        TaskRepository(s).upsert_bundle(tampered, None)


# ----------------------------------------------------------------------------- jobs


class FlakyQueue:
    backend_name = "flaky"

    def __init__(self) -> None:
        self.fail = True
        self.items: list[uuid.UUID] = []

    def enqueue(self, job_id: uuid.UUID) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.items.append(job_id)


def _db(tmp_path):  # type: ignore[no-untyped-def]
    db = Database(f"sqlite:///{(tmp_path / 'j.db').as_posix()}")
    db.migrate()
    return db


def test_job_success_and_duplicate_delivery(tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls: list[int] = []
    q = InlineQueue()
    svc = JobService(_db(tmp_path), q)
    q.bind(svc.execute)
    svc.register(JobKind.DEMO, lambda payload, ctx: calls.append(1) or {"ok": True})
    job = svc.submit(JobKind.DEMO, {"x": 1}, idempotency_key="k1")
    assert job.status == JobStatus.SUCCEEDED and calls == [1] and job.output == {"ok": True}
    svc.execute(job.job_id)  # stale duplicate message
    assert calls == [1] and svc.get(job.job_id).status == JobStatus.SUCCEEDED
    same = svc.submit(JobKind.DEMO, {"x": 1}, idempotency_key="k1")
    assert same.job_id == job.job_id and calls == [1]
    with pytest.raises(ConflictError):
        svc.submit(JobKind.DEMO, {"x": 2}, idempotency_key="k1")


def test_bounded_retries_then_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    attempts: list[int] = []
    q = InlineQueue()
    svc = JobService(_db(tmp_path), q, max_attempts=3)
    q.bind(svc.execute)

    def flaky(payload, ctx):  # type: ignore[no-untyped-def]
        attempts.append(ctx.attempt)
        raise ConnectionError("transient")

    svc.register(JobKind.DEMO, flaky)
    job = svc.submit(JobKind.DEMO, {})
    assert attempts == [1, 2, 3] and job.status == JobStatus.FAILED and job.attempt_count == 3


def test_non_retryable_fails_once(tmp_path) -> None:  # type: ignore[no-untyped-def]
    attempts: list[int] = []
    q = InlineQueue()
    svc = JobService(_db(tmp_path), q)
    q.bind(svc.execute)

    def bad(payload, ctx):  # type: ignore[no-untyped-def]
        attempts.append(1)
        raise TraceForgeError("bad input")

    svc.register(JobKind.DEMO, bad)
    job = svc.submit(JobKind.DEMO, {})
    assert (
        attempts == [1]
        and job.status == JobStatus.FAILED
        and job.error_code == ErrorCode.INTERNAL.value
    )


def test_queue_outage_recovery_and_cancel(tmp_path) -> None:  # type: ignore[no-untyped-def]
    q = FlakyQueue()
    svc = JobService(_db(tmp_path), q)
    svc.register(JobKind.DEMO, lambda p, c: {"ok": True})
    job = svc.submit(JobKind.DEMO, {})
    assert job.status == JobStatus.QUEUED and q.items == []  # persisted even though queue is down
    q.fail = False
    assert svc.recover(undispatched_after_seconds=0)["redispatched"] == 1 and q.items == [
        job.job_id
    ]
    svc.execute(job.job_id)
    assert svc.get(job.job_id).status == JobStatus.SUCCEEDED
    other = svc.submit(JobKind.DEMO, {"n": 2})
    assert svc.cancel(other.job_id).status == JobStatus.CANCELLED
    svc.execute(other.job_id)  # delivered after cancellation: must not run
    assert svc.get(other.job_id).status == JobStatus.CANCELLED


def test_expired_lease_requeued(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from traceforge.infrastructure.database.repositories import JobRepository

    q = FlakyQueue()
    q.fail = False
    db = _db(tmp_path)
    svc = JobService(db, q)
    svc.register(JobKind.DEMO, lambda p, c: {})
    job = svc.submit(JobKind.DEMO, {})
    with db.session() as s:  # simulate a worker that claimed the job then crashed
        JobRepository(s).transition(
            job.job_id,
            JobStatus.RUNNING,
            expected=JobStatus.QUEUED,
            attempt_count=1,
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    assert svc.recover()["expired_leases"] == 1
    assert svc.get(job.job_id).status == JobStatus.QUEUED and q.items.count(job.job_id) == 2


def test_cooperative_cancellation_while_running(tmp_path) -> None:  # type: ignore[no-untyped-def]
    q = InlineQueue(background=True)
    svc = JobService(_db(tmp_path), q)
    q.bind(svc.execute)

    def slow(payload, ctx):  # type: ignore[no-untyped-def]
        for _ in range(100):
            ctx.check_cancelled()
            time.sleep(0.05)
        return {}

    svc.register(JobKind.DEMO, slow)
    job = svc.submit(JobKind.DEMO, {})
    for _ in range(50):
        if svc.get(job.job_id).status == JobStatus.RUNNING:
            break
        time.sleep(0.05)
    svc.cancel(job.job_id)
    q.join(10)
    assert svc.get(job.job_id).status == JobStatus.CANCELLED


def test_rq_queue_with_fakeredis(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import fakeredis
    from rq import SimpleWorker

    conn = fakeredis.FakeStrictRedis()
    rq_queue = RQQueue("redis://unused", "tf-test", connection=conn)
    svc = JobService(_db(tmp_path), rq_queue)
    svc.register(JobKind.DEMO, lambda p, c: {"done": True})

    class Holder:
        jobs = svc

    import traceforge.workers.tasks as tasks

    monkeypatch.setattr(tasks, "_platform", lambda: Holder)
    job = svc.submit(JobKind.DEMO, {})
    assert job.status == JobStatus.QUEUED and rq_queue.queue.count == 1
    rq_queue.enqueue(job.job_id)  # duplicate delivery
    SimpleWorker([rq_queue.queue], connection=conn).work(burst=True)
    assert (
        svc.get(job.job_id).status == JobStatus.SUCCEEDED and svc.get(job.job_id).attempt_count == 1
    )


# ----------------------------------------------------------------------------- storage


def test_local_store_immutable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = LocalObjectStore(tmp_path)
    ref = store.put_bytes("a/b.bin", b"hello")
    assert store.get_bytes("a/b.bin") == b"hello" and len(ref.sha256) == 64
    assert store.put_bytes("a/b.bin", b"hello").sha256 == ref.sha256
    with pytest.raises(ConflictError):
        store.put_bytes("a/b.bin", b"other")
    with pytest.raises(ValueError):
        store.put_bytes("../escape", b"x")


def test_s3_store_with_moto() -> None:
    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        store = S3ObjectStore("tf-bucket", client=client, create_bucket=True)
        ref = store.put_bytes("x/y.json", b"{}", "application/json")
        assert ref.uri == "s3://tf-bucket/x/y.json" and store.get_bytes("x/y.json") == b"{}"
        with pytest.raises(ConflictError):
            store.put_bytes("x/y.json", b"[]")


# ----------------------------------------------------------------------------- benchmark & experiments


def test_benchmark_metrics_and_report(platform) -> None:  # type: ignore[no-untyped-def]
    cfg = BenchmarkConfig(
        name="t",
        splits=(Split.PUBLIC_TEST,),
        max_tasks_per_family=1,
        provider=ProviderConfig(policy="noop"),
        bootstrap_samples=200,
    )
    result, paths = asyncio.run(platform.evaluations.run(cfg))
    o = result.metrics["overall"]
    assert (
        o["n"] == 3
        and o["verified_success"]["successes"] == 0
        and o["false_positive_reward_rate"]["rate"] == 0.0
    )
    assert set(result.metrics["by_family"]) == {"sql_injection", "access_control", "ssrf"}
    md = open(paths["report_md"], encoding="utf-8").read()
    assert "Wilson" in md and "demo_provider" in md and cfg.config_hash in md
    assert platform.evaluations.get(result.benchmark_id)["status"] == "completed"


def test_experiment_e_robustness_all_as_expected(sandbox, registry) -> None:  # type: ignore[no-untyped-def]
    from traceforge.modules.evaluation.experiments import run_verifier_robustness
    from traceforge.modules.rewards.policies import PolicyRegistry

    doc = run_verifier_robustness(sandbox, registry, PolicyRegistry())
    bad = [c for c in doc["cases"] if not c["as_expected"]]
    assert not bad, bad
    assert doc["n_cases"] >= 9 * 12


def test_experiment_b_designs_differ(executor, registry) -> None:  # type: ignore[no-untyped-def]
    from traceforge.modules.evaluation.experiments import run_reward_hacking
    from traceforge.modules.tasks.registry import TaskRegistry

    small = TaskRegistry()
    small.register(registry.bundle("ssrf-t1-s000"))
    doc = asyncio.run(
        run_reward_hacking(
            executor, small, policies=("reference", "noop", "false_claim", "test_tamper")
        )
    )
    d = doc["designs"]
    assert d["R3"]["false_positive_reward_rate"]["successes"] == 0
    assert (
        d["R1"]["false_positive_reward_rate"]["rate"]
        > d["R3"]["false_positive_reward_rate"]["rate"]
    )
    assert (
        d["R2"]["false_positive_reward_rate"]["rate"] > 0
    )  # visible tests fooled by noop / permissive tamper
    assert d["R3"]["true_positive_reward_rate"]["rate"] == 1.0


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TRACEFORGE_TEST_POSTGRES_URL"),
    reason="TRACEFORGE_TEST_POSTGRES_URL not set",
)
def test_postgres_migrations() -> None:
    db = Database(os.environ["TRACEFORGE_TEST_POSTGRES_URL"])
    db.migrate()
    assert db.is_up_to_date()
