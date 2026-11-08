"""Job state machine (pure domain logic)."""

from __future__ import annotations

from traceforge.contracts.common import ConflictError
from traceforge.contracts.jobs import ALLOWED_TRANSITIONS, TERMINAL_STATUSES, JobStatus


class IllegalTransition(ConflictError):
    pass


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def assert_transition(current: JobStatus, target: JobStatus) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(
            f"illegal job transition {current.value} -> {target.value}",
            details={"from": current.value, "to": target.value},
        )


def is_terminal(status: JobStatus) -> bool:
    return status in TERMINAL_STATUSES
