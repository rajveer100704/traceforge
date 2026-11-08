"""Job queue adapters: inline (local/dev/test) and Redis/RQ (deployment)."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)
RQ_TASK = "traceforge.workers.tasks.execute_job"


class InlineQueue:
    """Executes jobs in-process. ``background=True`` uses daemon threads (API non-blocking)."""

    backend_name = "inline"

    def __init__(self, background: bool = False) -> None:
        self.background = background
        self._executor: Callable[[uuid.UUID], None] | None = None
        self._threads: list[threading.Thread] = []
        self.enqueued: list[uuid.UUID] = []

    def bind(self, executor: Callable[[uuid.UUID], None]) -> None:
        self._executor = executor

    def enqueue(self, job_id: uuid.UUID) -> None:
        if self._executor is None:
            raise RuntimeError("InlineQueue is not bound to an executor")
        self.enqueued.append(job_id)
        if self.background:
            t = threading.Thread(
                target=self._safe, args=(job_id,), daemon=True, name=f"job-{job_id}"
            )
            self._threads.append(t)
            t.start()
        else:
            self._safe(job_id)

    def _safe(self, job_id: uuid.UUID) -> None:
        assert self._executor is not None
        try:
            self._executor(job_id)
        except Exception:
            log.exception("inline job %s crashed", job_id)

    def join(self, timeout: float | None = None) -> None:
        for t in list(self._threads):
            t.join(timeout)


class RQQueue:
    backend_name = "rq"

    def __init__(
        self, redis_url: str, queue_name: str = "traceforge", connection: Any = None
    ) -> None:
        from redis import Redis
        from rq import Queue

        self.connection = connection or Redis.from_url(redis_url)
        self.queue = Queue(queue_name, connection=self.connection)

    def enqueue(self, job_id: uuid.UUID) -> None:
        # Message carries only the job id; all state lives in the database (idempotent workers).
        self.queue.enqueue(
            RQ_TASK, str(job_id), job_timeout=7200, result_ttl=3600, failure_ttl=86400
        )

    def ping(self) -> bool:
        try:
            return bool(self.connection.ping())
        except Exception:
            return False
