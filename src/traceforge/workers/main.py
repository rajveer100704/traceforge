"""Worker process: RQ worker + periodic recovery (stranded dispatches, expired leases, stale sandboxes).

Ingestion, rollout/evaluation and training jobs share one queue by default; run several worker
processes (optionally with different TRACEFORGE_QUEUE_NAME values) to isolate heavy training jobs.
"""

from __future__ import annotations

import logging
import os
import threading

from traceforge.config import get_settings
from traceforge.contracts.common import CapabilityUnavailable
from traceforge.infrastructure.observability import configure_logging

log = logging.getLogger(__name__)
RECOVERY_INTERVAL_SECONDS = 60.0


def _recovery_loop(stop: threading.Event) -> None:
    from traceforge.workers.tasks import _platform

    while not stop.wait(RECOVERY_INTERVAL_SECONDS):
        try:
            platform = _platform()
            log.info("recovery: %s", platform.jobs.recover())
            reaper = getattr(platform.sandbox, "reap_stale", None)
            if callable(reaper):
                removed = reaper(platform.settings.stale_lease_seconds)
                if removed:
                    log.warning("reaped stale sandboxes: %s", removed)
        except Exception:
            log.exception("recovery loop iteration failed")


def run_worker(burst: bool = False) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.queue_backend != "rq":
        raise CapabilityUnavailable(
            "workers require TRACEFORGE_QUEUE_BACKEND=rq (inline mode runs jobs in-process)"
        )
    from redis import Redis
    from rq import Queue, SimpleWorker, Worker

    conn = Redis.from_url(settings.redis_url)
    queue = Queue(settings.queue_name, connection=conn)
    # Fork-based Worker is unavailable on Windows; SimpleWorker executes in-process.
    worker_cls = SimpleWorker if os.name == "nt" else Worker
    stop = threading.Event()
    threading.Thread(target=_recovery_loop, args=(stop,), daemon=True).start()
    try:
        worker_cls([queue], connection=conn).work(burst=burst, with_scheduler=False)
    finally:
        stop.set()
