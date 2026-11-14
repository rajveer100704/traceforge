"""Minimal stand-ins for RolloutSummary used by metric tests (only the attributes summarize reads).

``ep()`` builds one episode. Telemetry is given as v0.3 ``generation_stats``-style metadata;
``legacy=True`` builds a pre-v0.3 record (no telemetry fields at all).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from traceforge.contracts.verification import CheckStatus, Verdict


def ep(
    task: str = "t1",
    verdict: Verdict = Verdict.PASS,
    *,
    seed: int | None = 0,
    reward: float = 1.0,
    calls: int = 2,
    rejected: int = 0,
    duration: float = 1.0,
    termination: str = "final_report",
    checks: dict[str, CheckStatus] | None = None,
    requests: int = 1,
    usage: tuple[int, int] | None = (100, 20),
    cost: float | None = 0.01,
    request_seconds: list[float] | None = None,
    attempts: int | None = 1,
    finish: dict[str, int] | None = None,
    provider_failure: str | None = None,
    failure_source: str | None = None,
    generation_seconds: float | None = 0.5,
    legacy: bool = False,
    authoritative: bool = True,
    extra: dict[str, Any] | None = None,
) -> SimpleNamespace:
    statuses = {"hidden_security": CheckStatus.PASS, "regression": CheckStatus.PASS}
    statuses.update(checks or {})
    chk = {
        name: SimpleNamespace(status=st, duration_seconds=0.1)
        for name, st in statuses.items()
        if st is not None
    }
    verification = SimpleNamespace(
        verdict=verdict,
        authoritative=authoritative,
        checks=list(chk.values()),
        failure_reasons=[],
        verification_id=uuid.uuid4(),
        check=lambda name, c=chk: c.get(name),
    )
    md: dict[str, Any] = {"family": "f", "split": "public_test", "difficulty": "easy", "seed": seed}
    if generation_seconds is not None:
        md["generation_seconds"] = generation_seconds
    if not legacy:
        md.update(
            {
                "n_generation_requests": requests,
                "n_requests_usage_reported": requests if usage is not None else 0,
                "prompt_tokens": usage[0] if usage else 0,
                "completion_tokens": usage[1] if usage else 0,
                "cost_usd": cost,
                "n_requests_cost_known": requests if cost is not None else 0,
                "generation_request_seconds": request_seconds
                if request_seconds is not None
                else [0.5] * requests,
                "n_requests_attempts_known": requests if attempts is not None else 0,
                "provider_attempts": attempts,
                "provider_retries": None if attempts is None else max(attempts - requests, 0),
                "finish_reasons": finish if finish is not None else {"stop": requests},
                "provider_failure": provider_failure,
                "provider_failure_source": failure_source,
            }
        )
    else:
        md.update({"prompt_tokens": usage[0] if usage else 0, "completion_tokens": 0})
    md.update(extra or {})
    return SimpleNamespace(
        task_id=task,
        verification=verification,
        reward=SimpleNamespace(final_reward=reward),
        tool_calls=calls,
        rejected_actions=rejected,
        duration_seconds=duration,
        termination_reason=termination,
        metadata=md,
        rollout_id=uuid.uuid4(),
    )
