"""RQ task entry points. The queue message carries only a job id; state lives in the DB."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from traceforge.application.container import Platform


@lru_cache(maxsize=1)
def _platform() -> Platform:
    from traceforge.application.container import Platform
    from traceforge.config import get_settings

    return Platform.build(get_settings(), migrate=False)


def execute_job(job_id: str) -> None:
    """Idempotent: duplicate or stale deliveries for terminal/claimed jobs are no-ops."""
    _platform().jobs.execute(uuid.UUID(job_id))
