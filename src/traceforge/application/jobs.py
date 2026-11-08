"""Job lifecycle service.

Guarantees:
* The job row is committed before anything is enqueued (outbox semantics). If enqueueing fails
  the job stays CREATED and ``recover()`` re-dispatches it.
* Workers are idempotent per job id: a terminal or already-claimed job is a no-op, so duplicate
  or stale queue deliveries can never move a terminal job back to RUNNING.
* Retries are bounded and only for retryable errors; cancellation is cooperative.
"""

from __future__ import annotations

import logging
import socket
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from traceforge.contracts.common import ErrorCode, JSONValue, TraceForgeError
from traceforge.contracts.jobs import JobKind, JobStatus, JobView
from traceforge.domain.jobs import is_terminal
from traceforge.domain.ports import JobQueue
from traceforge.infrastructure.database.repositories import JobRepository, job_view
from traceforge.infrastructure.database.session import Database

log = logging.getLogger(__name__)


class JobCancelled(TraceForgeError):
    code = ErrorCode.CANCELLED


class JobTimedOut(TraceForgeError):
    code = ErrorCode.TIMEOUT


@dataclass
class JobContext:
    job_id: uuid.UUID
    correlation_id: str
    attempt: int
    _service: JobService

    def progress(self, value: float) -> None:
        with self._service.db.session() as s:
            JobRepository(s).set_progress(self.job_id, value)

    def cancelled(self) -> bool:
        with self._service.db.session() as s:
            return JobRepository(s).cancel_requested(self.job_id)

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise JobCancelled("cancellation requested")


class JobHandler(Protocol):
    def __call__(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, JSONValue]: ...


RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError)


class JobService:
    def __init__(
        self,
        db: Database,
        queue: JobQueue,
        handlers: dict[JobKind, JobHandler] | None = None,
        *,
        max_attempts: int = 3,
        lease_seconds: int = 900,
        worker_id: str | None = None,
    ) -> None:
        self.db = db
        self.queue = queue
        self.handlers: dict[JobKind, JobHandler] = handlers or {}
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"

    def register(self, kind: JobKind, handler: JobHandler) -> None:
        self.handlers[kind] = handler

    # ------------------------------------------------------------------ submission
    def submit(
        self,
        kind: JobKind,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> JobView:
        if kind not in self.handlers:
            raise TraceForgeError(f"no handler registered for job kind {kind.value}")
        with self.db.session() as s:
            job, _created = JobRepository(s).create(
                kind,
                payload,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id or str(uuid.uuid4()),
                max_attempts=self.max_attempts,
            )
            job_id, status = job.id, JobStatus(job.status)
        if status == JobStatus.CREATED:
            self.dispatch(job_id)
        return self.get(job_id)

    def dispatch(self, job_id: uuid.UUID) -> bool:
        with self.db.session() as s:
            moved = JobRepository(s).transition(
                job_id, JobStatus.QUEUED, expected=JobStatus.CREATED
            )
        if not moved:
            return False
        try:
            self.queue.enqueue(job_id)
        except Exception as exc:  # queue down: job stays QUEUED-undispatched; recover() retries
            log.warning("enqueue failed for job %s: %s", job_id, exc)
            return False
        with self.db.session() as s:
            JobRepository(s).mark_dispatched(job_id)
        return True

    def get(self, job_id: uuid.UUID) -> JobView:
        with self.db.session() as s:
            return job_view(JobRepository(s).get(job_id))

    def cancel(self, job_id: uuid.UUID) -> JobView:
        with self.db.session() as s:
            repo = JobRepository(s)
            job = repo.get(job_id)
            status = JobStatus(job.status)
            if status in (JobStatus.CREATED, JobStatus.QUEUED):
                repo.request_cancel(job_id)
                repo.transition(
                    job_id,
                    JobStatus.CANCELLED,
                    expected=status,
                    error_code=ErrorCode.CANCELLED.value,
                    error_message="cancelled before start",
                )
            elif status == JobStatus.RUNNING:
                repo.request_cancel(job_id)
        return self.get(job_id)

    # ------------------------------------------------------------------ execution
    def execute(self, job_id: uuid.UUID) -> None:
        """Worker entry point. Safe to call repeatedly for the same job id."""
        with self.db.session() as s:
            repo = JobRepository(s)
            job = repo.get(job_id)
            status = JobStatus(job.status)
            if is_terminal(status):
                log.info("job %s already terminal (%s); ignoring delivery", job_id, status.value)
                return
            if status == JobStatus.CREATED:  # delivered before dispatch bookkeeping completed
                repo.transition(job_id, JobStatus.QUEUED, expected=JobStatus.CREATED)
                status = JobStatus.QUEUED
            if status != JobStatus.QUEUED:
                log.info(
                    "job %s is %s (claimed elsewhere); ignoring delivery", job_id, status.value
                )
                return
            if job.cancel_requested:
                repo.transition(
                    job_id,
                    JobStatus.CANCELLED,
                    expected=JobStatus.QUEUED,
                    error_code=ErrorCode.CANCELLED.value,
                )
                return
            attempt = job.attempt_count + 1
            claimed = repo.transition(
                job_id,
                JobStatus.RUNNING,
                expected=JobStatus.QUEUED,
                attempt_count=attempt,
                lease_owner=self.worker_id,
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=self.lease_seconds),
            )
            if not claimed:
                return
            kind, payload, correlation_id, max_attempts = (
                JobKind(job.kind),
                dict(job.input),
                job.correlation_id,
                job.max_attempts,
            )
        ctx = JobContext(
            job_id=job_id, correlation_id=correlation_id, attempt=attempt, _service=self
        )
        try:
            output = self.handlers[kind](payload, ctx)
        except JobCancelled:
            self._finish(
                job_id,
                JobStatus.CANCELLED,
                error_code=ErrorCode.CANCELLED.value,
                error_message="cancelled",
            )
        except (JobTimedOut, TimeoutError) as exc:
            self._finish(
                job_id,
                JobStatus.TIMED_OUT,
                error_code=ErrorCode.TIMEOUT.value,
                error_message=str(exc)[:2000],
            )
        except Exception as exc:
            retryable = isinstance(exc, RETRYABLE_EXCEPTIONS) or (
                isinstance(exc, TraceForgeError) and exc.retryable
            )
            code = exc.code.value if isinstance(exc, TraceForgeError) else ErrorCode.INTERNAL.value
            message = f"{type(exc).__name__}: {exc}"[:2000]
            if retryable and attempt < max_attempts:
                log.warning("job %s attempt %d failed (retryable): %s", job_id, attempt, message)
                with self.db.session() as s:
                    JobRepository(s).transition(
                        job_id,
                        JobStatus.QUEUED,
                        expected=JobStatus.RUNNING,
                        error_code=code,
                        error_message=message,
                        lease_owner=None,
                    )
                self.queue.enqueue(job_id)
            else:
                log.error("job %s failed: %s", job_id, message)
                self._finish(job_id, JobStatus.FAILED, error_code=code, error_message=message)
        else:
            self._finish(job_id, JobStatus.SUCCEEDED, output=output, progress=1.0)

    def _finish(self, job_id: uuid.UUID, status: JobStatus, **fields: Any) -> None:
        with self.db.session() as s:
            JobRepository(s).transition(
                job_id, status, expected=JobStatus.RUNNING, lease_owner=None, **fields
            )

    # ------------------------------------------------------------------ recovery
    def recover(self, undispatched_after_seconds: float = 30.0) -> dict[str, int]:
        """Re-dispatch jobs stranded by queue outages and expire stale worker leases."""
        redispatched = expired = 0
        with self.db.session() as s:
            repo = JobRepository(s)
            created = repo.undispatched(undispatched_after_seconds)
            stale = repo.stale_running()
            from sqlalchemy import select

            from traceforge.infrastructure.database.models import Job

            cutoff = datetime.now(UTC) - timedelta(seconds=undispatched_after_seconds)
            queued_lost = list(
                s.scalars(
                    select(Job.id).where(
                        Job.status == JobStatus.QUEUED.value,
                        Job.dispatched_at.is_(None),
                        Job.updated_at <= cutoff,
                    )
                )
            )
        for job_id in created:
            redispatched += int(self.dispatch(job_id))
        for job_id in queued_lost:
            try:
                self.queue.enqueue(job_id)
                with self.db.session() as s:
                    JobRepository(s).mark_dispatched(job_id)
                redispatched += 1
            except Exception as exc:
                log.warning("re-enqueue failed for %s: %s", job_id, exc)
        for job_id in stale:
            with self.db.session() as s:
                repo = JobRepository(s)
                job = repo.get(job_id)
                if job.attempt_count < job.max_attempts:
                    repo.transition(
                        job_id,
                        JobStatus.QUEUED,
                        expected=JobStatus.RUNNING,
                        error_code="lease_expired",
                        error_message="worker lease expired",
                        lease_owner=None,
                    )
                    retry = True
                else:
                    repo.transition(
                        job_id,
                        JobStatus.FAILED,
                        expected=JobStatus.RUNNING,
                        error_code="lease_expired",
                        error_message="worker lease expired; attempts exhausted",
                    )
                    retry = False
            if retry:
                self.queue.enqueue(job_id)
            expired += 1
        return {"redispatched": redispatched, "expired_leases": expired}


Handler = Callable[[dict[str, Any], JobContext], dict[str, JSONValue]]
