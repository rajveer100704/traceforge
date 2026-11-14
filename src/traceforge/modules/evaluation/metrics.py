"""Benchmark metrics computed from rollout summaries (real values only).

Every emitted key is defined in ``metric_contract.py`` (schema 2). Missing telemetry is never
treated as zero: it is excluded, counted in ``telemetry_coverage`` and makes dependent totals
null. v0.2 keys keep their meaning for non-empty inputs; an empty denominator is null.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from traceforge.contracts.experiments import RolloutSummary
from traceforge.contracts.verification import CheckStatus, Verdict
from traceforge.modules.evaluation.metric_contract import METRICS_SCHEMA_VERSION
from traceforge.modules.evaluation.statistics import (
    CLUSTER_ESTIMAND,
    CLUSTER_METHOD,
    PERCENTILE_METHOD,
    bootstrap_mean_interval,
    cluster_bootstrap,
    percentiles,
    proportion,
)

REWARD_POSITIVE_THRESHOLD = 0.5
DEFAULT_SENSITIVITY_THRESHOLDS: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 0.9)
STATISTICAL_UNITS = (
    "Wilson intervals treat episodes as independent (descriptive, episode level). "
    "Task-clustered bootstrap intervals resample independent tasks and are the appropriate "
    "uncertainty for claims about the task population; n_tasks is the number of independent units."
)
PROVIDER_FAILURE_TERMINATIONS = frozenset({"provider_timeout", "provider_error"})


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def _ratio(num: float | None, den: float | None) -> float | None:
    """num/den, or None when either is unknown, the denominator is 0, or the result is not
    finite. Never a silent zero."""
    if num is None or den is None or den == 0:
        return None
    out = num / den
    return out if math.isfinite(out) else None


@dataclass
class _Telemetry:
    """One episode's generation telemetry as captured by v0.3 rollouts (generation_stats)."""

    recorded: bool = False  # the v0.3 telemetry fields are present
    usage_complete: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None
    request_seconds: list[float] = field(default_factory=list)
    request_latency_complete: bool = False
    attempts: int | None = None
    retries: int | None = None
    finish_reasons: dict[str, int] = field(default_factory=dict)
    provider_failure: str | None = None
    provider_failure_source: str | None = None


def episode_telemetry(r: RolloutSummary) -> _Telemetry:
    md = r.metadata
    t = _Telemetry()
    requests = _int(md.get("n_generation_requests"))
    if requests is not None:
        t.recorded = True
        t.usage_complete = _int(md.get("n_requests_usage_reported")) == requests
        t.prompt_tokens = _int(md.get("prompt_tokens")) or 0
        t.completion_tokens = _int(md.get("completion_tokens")) or 0
        t.cost = _finite(md.get("cost_usd"))
        if t.cost is not None and t.cost < 0:
            t.cost = None
        raw = md.get("generation_request_seconds")
        if isinstance(raw, list):
            t.request_seconds = [v for v in (_finite(x) for x in raw) if v is not None]
        t.request_latency_complete = len(t.request_seconds) == requests
        t.attempts = _int(md.get("provider_attempts"))
        t.retries = _int(md.get("provider_retries"))
        reasons = md.get("finish_reasons")
        if isinstance(reasons, dict):
            t.finish_reasons = {
                str(k): v for k, v in reasons.items() if _int(v) is not None and v >= 0
            }
    failure = md.get("provider_failure")
    if isinstance(failure, str) and failure:
        t.provider_failure = failure
        source = md.get("provider_failure_source")
        t.provider_failure_source = source if isinstance(source, str) and source else "unknown"
    elif r.termination_reason in PROVIDER_FAILURE_TERMINATIONS:
        # Records from before v0.3 telemetry: the termination reason is all we know.
        t.provider_failure = "timeout" if r.termination_reason == "provider_timeout" else "unknown"
        t.provider_failure_source = "unknown"
    return t


def _coverage(covered: int, n: int) -> dict[str, Any]:
    return {"covered": covered, "n": n, "fraction": (covered / n) if n else None}


def summarize(
    runs: list[RolloutSummary],
    *,
    confidence: float = 0.95,
    bootstrap_samples: int = 2000,
    seed: int = 0,
    ground_truth: Callable[[RolloutSummary], bool | None] | None = None,
    reward_threshold: float = REWARD_POSITIVE_THRESHOLD,
) -> dict[str, Any]:
    n = len(runs)
    verified = sum(r.verification.verdict == Verdict.PASS for r in runs)
    partial = sum(r.verification.verdict == Verdict.PARTIAL for r in runs)
    errors = sum(r.verification.verdict in (Verdict.ERROR, Verdict.INCONCLUSIVE) for r in runs)
    rewarded = [r for r in runs if r.reward.final_reward >= reward_threshold]
    false_positive = [r for r in rewarded if not _truly_successful(r, ground_truth)]
    regression_executed = [
        r
        for r in runs
        if (c := r.verification.check("regression")) is not None and c.status != CheckStatus.SKIPPED
    ]
    regression_fail = sum(
        (c := r.verification.check("regression")) is not None and c.status == CheckStatus.FAIL
        for r in runs
    )
    tool_calls = [float(r.tool_calls) for r in runs]
    successes = [r for r in runs if r.verification.verdict == Verdict.PASS]
    total_actions = sum(r.tool_calls for r in runs)
    rejected = sum(r.rejected_actions for r in runs)
    rewards = [r.reward.final_reward for r in runs]
    tel = [episode_telemetry(r) for r in runs]

    # ---- tokens (null unless every episode reported complete usage) ----------------------
    usage_covered = sum(t.usage_complete for t in tel)
    usage_all = n > 0 and usage_covered == n
    prompt_total = sum(t.prompt_tokens for t in tel) if usage_all else None
    completion_total = sum(t.completion_tokens for t in tel) if usage_all else None
    token_total = (
        prompt_total + completion_total
        if prompt_total is not None and completion_total is not None
        else None
    )
    tokens = {
        "prompt_total": prompt_total,
        "completion_total": completion_total,
        "total": token_total,
        "per_attempt": _ratio(token_total, n),
        "per_verified_success": _ratio(token_total, verified),
        "verified_successes_per_1000_tokens": _ratio(
            verified * 1000 if token_total else None, token_total
        ),
        "episodes_with_complete_usage": usage_covered,
        "n_episodes": n,
    }
    legacy_token_usage = (
        {
            "prompt_tokens": prompt_total,
            "completion_tokens": completion_total,
            "total_tokens": token_total,
            "tokens_per_episode": tokens["per_attempt"],
            "tokens_per_verified_success": tokens["per_verified_success"],
        }
        if token_total  # v0.2 shape; None when not fully reported or zero (e.g. demo provider)
        else None
    )

    # ---- cost (unknown is never 0; a lower bound only when coverage is partial) ---------
    known_costs = [t.cost for t in tel if t.cost is not None]
    cost_complete = n > 0 and len(known_costs) == n
    total_cost = _finite(sum(known_costs)) if cost_complete else None
    lower_bound = _finite(sum(known_costs)) if known_costs and not cost_complete else None
    cost = {
        "total_usd": total_cost,
        "per_attempt_usd": _ratio(total_cost, n),
        "per_verified_success_usd": _ratio(total_cost, verified),
        "lower_bound_usd": lower_bound,
        "episodes_with_known_cost": len(known_costs),
        "n_episodes": n,
    }

    # ---- provider failures vs verifier outcomes ------------------------------------------
    failed = [t.provider_failure is not None for t in tel]
    n_failed = sum(failed)
    timeouts = sum(t.provider_failure == "timeout" for t in tel)
    ok_runs = [r for r, f in zip(runs, failed, strict=True) if not f]
    provider_failures = {
        "rate": proportion(n_failed, n, confidence),
        "timeout_rate": proportion(timeouts, n, confidence),
        "by_kind": dict(
            sorted(Counter(t.provider_failure for t in tel if t.provider_failure).items())
        ),
        "by_source": dict(
            sorted(Counter(t.provider_failure_source for t in tel if t.provider_failure).items())
        ),
    }
    m = len(ok_runs)
    verifier_outcomes = {
        "n": m,
        "verified_success": proportion(
            sum(r.verification.verdict == Verdict.PASS for r in ok_runs), m, confidence
        ),
        "partial_success": proportion(
            sum(r.verification.verdict == Verdict.PARTIAL for r in ok_runs), m, confidence
        ),
        "verifier_error_rate": proportion(
            sum(r.verification.verdict in (Verdict.ERROR, Verdict.INCONCLUSIVE) for r in ok_runs),
            m,
            confidence,
        ),
    }

    # ---- latency, finish reasons ---------------------------------------------------------
    finish: Counter[str] = Counter()
    for t in tel:
        finish.update(t.finish_reasons)
    gen_seconds = [
        v for v in (_finite(r.metadata.get("generation_seconds")) for r in runs) if v is not None
    ]
    latency = {
        "request_seconds": percentiles([s for t in tel for s in t.request_seconds]),
        "episode_seconds": percentiles([r.duration_seconds for r in runs]),
        "generation_seconds_per_episode": percentiles(gen_seconds),
    }

    hidden = [_check_pass(r, "hidden_security") for r in runs]
    hidden_known = [h for h in hidden if h is not None]
    tasks = sorted({r.task_id for r in runs})
    per_task_n = [sum(r.task_id == t for r in runs) for t in tasks]

    raw_labels = [(r, ground_truth(r)) for r in runs] if ground_truth else []
    labelled: list[tuple[RolloutSummary, bool]] = [(r, g) for r, g in raw_labels if g is not None]
    if not labelled:
        label_source = "verifier_derived"
    elif len(labelled) == n:
        label_source = "independent_labels"
    else:
        label_source = "mixed"

    out: dict[str, Any] = {
        "metrics_schema_version": METRICS_SCHEMA_VERSION,
        "n": n,
        "verified_success": proportion(verified, n, confidence),
        "partial_success": proportion(partial, n, confidence),
        "verifier_error_rate": proportion(errors, n, confidence),
        "reward_positive_rate": proportion(len(rewarded), n, confidence),
        "false_positive_reward_rate": proportion(len(false_positive), n, confidence),
        "false_positive_reward_label_source": label_source,
        "regression_failure_rate": proportion(regression_fail, n, confidence),
        "regression_failure_rate_executed": proportion(
            regression_fail, len(regression_executed), confidence
        ),
        "hidden_security_pass_rate": proportion(sum(hidden_known), len(hidden_known), confidence),
        "mean_reward": _mean(rewards),
        "mean_reward_ci": bootstrap_mean_interval(
            rewards, samples=bootstrap_samples, confidence=confidence, seed=seed
        )
        if rewards
        else None,
        "tool_calls_per_attempt": _mean(tool_calls),
        "tool_calls_per_success": (sum(r.tool_calls for r in successes) / len(successes))
        if successes
        else None,
        "tool_calls": {
            "total": total_actions,
            "per_attempt": _ratio(total_actions, n),
            "per_success": _ratio(sum(r.tool_calls for r in successes), len(successes)),
        },
        "action_rejection_rate": (rejected / total_actions) if total_actions else None,
        "mean_duration_seconds": _mean([r.duration_seconds for r in runs]),
        "environment_compute_seconds": sum(
            sum(c.duration_seconds for c in r.verification.checks) for r in runs
        ),
        "token_usage": legacy_token_usage,
        "tokens": tokens,
        "mean_generation_seconds": _mean(gen_seconds),
        "cost_per_verified_success_usd": cost["per_verified_success_usd"],
        "cost": cost,
        "latency": latency,
        "finish_reasons": {
            "counts": dict(sorted(finish.items())),
            "truncated_length": finish.get("length", 0),
        },
        "provider_failures": provider_failures,
        "verifier_outcomes_without_provider_failure": verifier_outcomes,
        "telemetry_coverage": {
            "generation_telemetry": _coverage(sum(t.recorded for t in tel), n),
            "usage": _coverage(usage_covered, n),
            "cost": _coverage(len(known_costs), n),
            "request_latency": _coverage(sum(t.request_latency_complete for t in tel), n),
            "attempts": _coverage(sum(t.attempts is not None for t in tel), n),
            "hidden_security_check": _coverage(len(hidden_known), n),
            "regression_check": _coverage(len(regression_executed), n),
        },
        "n_episodes": n,
        "n_tasks": len(tasks),
        "episodes_per_task": {"min": min(per_task_n), "max": max(per_task_n)}
        if per_task_n
        else None,
        "n_seeds": len({str(s) for r in runs if (s := r.metadata.get("seed")) is not None}),
        "reward_threshold": reward_threshold,
        "labelled_n": len(labelled),
        "verifier_precision": None,
        "verifier_recall": None,
        "reward_precision": None,
        "reward_recall": None,
        "statistics": {
            "confidence": confidence,
            "bootstrap_samples": bootstrap_samples,
            "bootstrap_seed": seed,
            "proportion_interval": "wilson (episode-level, descriptive)",
            "task_cluster_interval": CLUSTER_METHOD,
            "task_cluster_estimand": CLUSTER_ESTIMAND,
            "mean_reward_interval": "episode-level percentile bootstrap (descriptive)",
            "percentile_method": PERCENTILE_METHOD,
        },
    }

    def clustered(value: Callable[[RolloutSummary], float | None]) -> dict[str, Any]:
        groups: dict[str, list[float]] = defaultdict(list)
        for r in runs:
            v = value(r)
            if v is not None:
                groups[r.task_id].append(v)
        return cluster_bootstrap(
            [groups[t] for t in sorted(groups)],
            samples=bootstrap_samples,
            confidence=confidence,
            seed=seed,
        )

    def hidden_value(r: RolloutSummary) -> float | None:
        h = _check_pass(r, "hidden_security")
        return None if h is None else float(h)

    def regression_value(r: RolloutSummary) -> float:
        c = r.verification.check("regression")
        return float(c is not None and c.status == CheckStatus.FAIL)

    failed_by_run = {id(r): f for r, f in zip(runs, failed, strict=True)}
    out["task_clustered"] = {
        "verified_success": clustered(lambda r: float(r.verification.verdict == Verdict.PASS)),
        "false_positive_reward_rate": clustered(
            lambda r: float(
                r.reward.final_reward >= reward_threshold and not _truly_successful(r, ground_truth)
            )
        ),
        "hidden_security_pass_rate": clustered(hidden_value),
        "regression_failure_rate": clustered(regression_value),
        "mean_reward": clustered(lambda r: r.reward.final_reward),
        "provider_failure_rate": clustered(lambda r: float(failed_by_run[id(r)])),
    }
    if labelled:
        tp = sum(r.verification.verdict == Verdict.PASS and g for r, g in labelled)
        fp = sum(r.verification.verdict == Verdict.PASS and not g for r, g in labelled)
        fn = sum(r.verification.verdict != Verdict.PASS and g for r, g in labelled)
        out["verifier_precision"] = tp / (tp + fp) if tp + fp else None
        out["verifier_recall"] = tp / (tp + fn) if tp + fn else None
        rewarded_l = [g for r, g in labelled if r.reward.final_reward >= reward_threshold]
        truly = [r for r, g in labelled if g]
        out["reward_precision"] = proportion(sum(rewarded_l), len(rewarded_l), confidence)
        out["reward_recall"] = proportion(
            sum(r.reward.final_reward >= reward_threshold for r in truly), len(truly), confidence
        )
    return out


def _check_pass(r: RolloutSummary, name: str) -> bool | None:
    c = r.verification.check(name)
    if c is None or c.status == CheckStatus.SKIPPED:
        return None
    return c.status == CheckStatus.PASS


def threshold_sensitivity(
    runs: list[RolloutSummary],
    thresholds: tuple[float, ...] = DEFAULT_SENSITIVITY_THRESHOLDS,
    *,
    ground_truth: Callable[[RolloutSummary], bool | None] | None = None,
    confidence: float = 0.95,
    bootstrap_samples: int = 2000,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Reward-positive and false-positive reward rates as a function of the reward threshold."""
    rows = []
    for t in thresholds:
        s = summarize(
            runs,
            confidence=confidence,
            bootstrap_samples=bootstrap_samples,
            seed=seed,
            ground_truth=ground_truth,
            reward_threshold=t,
        )
        rows.append(
            {
                "threshold": t,
                "reward_positive_rate": s["reward_positive_rate"],
                "false_positive_reward_rate": s["false_positive_reward_rate"],
                "false_positive_reward_rate_task_clustered": s["task_clustered"][
                    "false_positive_reward_rate"
                ],
                "false_positive_reward_label_source": s["false_positive_reward_label_source"],
            }
        )
    return rows


def task_level_rows(runs: Iterable[RolloutSummary]) -> list[dict[str, Any]]:
    """One row per independent task, aggregating its episodes (seeds / repeats)."""
    groups: dict[str, list[RolloutSummary]] = defaultdict(list)
    for r in runs:
        groups[r.task_id].append(r)
    out = []
    for tid in sorted(groups):
        rs = groups[tid]
        m = rs[0].metadata
        verdicts: dict[str, int] = defaultdict(int)
        for r in rs:
            verdicts[r.verification.verdict.value] += 1
        out.append(
            {
                "task_id": tid,
                "family": m.get("family"),
                "template": m.get("template"),
                "split": m.get("split"),
                "difficulty": m.get("difficulty"),
                "n_episodes": len(rs),
                "seeds": sorted({str(r.metadata.get("seed")) for r in rs}),
                "verified_success_rate": sum(r.verification.verdict == Verdict.PASS for r in rs)
                / len(rs),
                "mean_reward": sum(r.reward.final_reward for r in rs) / len(rs),
                "verdicts": dict(sorted(verdicts.items())),
                "outcome_consistent_across_episodes": len(verdicts) == 1,
                "provider_failures": sum(
                    episode_telemetry(r).provider_failure is not None for r in rs
                ),
            }
        )
    return out


def _truly_successful(
    r: RolloutSummary, ground_truth: Callable[[RolloutSummary], bool | None] | None
) -> bool:
    if ground_truth is not None:
        g = ground_truth(r)
        if g is not None:
            return g
    return r.verification.authoritative and r.verification.verdict == Verdict.PASS


def group_by(runs: Iterable[RolloutSummary], key: str) -> dict[str, list[RolloutSummary]]:
    groups: dict[str, list[RolloutSummary]] = defaultdict(list)
    for r in runs:
        groups[str(r.metadata.get(key))].append(r)
    return dict(sorted(groups.items()))


def per_task_rows(runs: Iterable[RolloutSummary]) -> list[dict[str, Any]]:
    rows = []
    for r in runs:
        t = episode_telemetry(r)
        rows.append(
            {
                "task_id": r.task_id,
                "family": r.metadata.get("family"),
                "split": r.metadata.get("split"),
                "difficulty": r.metadata.get("difficulty"),
                "policy": r.metadata.get("policy"),
                "seed": r.metadata.get("seed"),
                "verdict": r.verification.verdict.value,
                "reward": r.reward.final_reward,
                "tool_calls": r.tool_calls,
                "rejected_actions": r.rejected_actions,
                "failure_reasons": list(r.verification.failure_reasons),
                "rollout_id": str(r.rollout_id),
                "verification_id": str(r.verification.verification_id),
                "termination_reason": r.termination_reason,
                "provider_failure": t.provider_failure,
                "duration_seconds": r.duration_seconds,
                "cost_usd": t.cost,
                "tokens": t.prompt_tokens + t.completion_tokens if t.usage_complete else None,
            }
        )
    return rows
