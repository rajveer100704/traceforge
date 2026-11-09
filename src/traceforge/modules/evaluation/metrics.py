"""Benchmark metrics computed from rollout summaries (real values only)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from traceforge.contracts.experiments import RolloutSummary
from traceforge.contracts.verification import CheckStatus, Verdict
from traceforge.modules.evaluation.statistics import (
    bootstrap_mean_interval,
    cluster_bootstrap,
    proportion,
)

REWARD_POSITIVE_THRESHOLD = 0.5
DEFAULT_SENSITIVITY_THRESHOLDS: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 0.9)
STATISTICAL_UNITS = (
    "Wilson intervals treat episodes as independent (descriptive, episode level). "
    "Task-clustered bootstrap intervals resample independent tasks and are the appropriate "
    "uncertainty for claims about the task population; n_tasks is the number of independent units."
)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


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
    regression_fail = sum(
        (c := r.verification.check("regression")) is not None and c.status == CheckStatus.FAIL
        for r in runs
    )
    tool_calls = [float(r.tool_calls) for r in runs]
    successes = [r for r in runs if r.verification.verdict == Verdict.PASS]
    total_actions = sum(r.tool_calls for r in runs)
    rejected = sum(r.rejected_actions for r in runs)
    rewards = [r.reward.final_reward for r in runs]
    out: dict[str, Any] = {
        "n": n,
        "verified_success": proportion(verified, n, confidence),
        "partial_success": proportion(partial, n, confidence),
        "verifier_error_rate": proportion(errors, n, confidence),
        "reward_positive_rate": proportion(len(rewarded), n, confidence),
        "false_positive_reward_rate": proportion(len(false_positive), n, confidence),
        "regression_failure_rate": proportion(regression_fail, n, confidence),
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
        "action_rejection_rate": (rejected / total_actions) if total_actions else None,
        "mean_duration_seconds": _mean([r.duration_seconds for r in runs]),
        "environment_compute_seconds": sum(
            sum(c.duration_seconds for c in r.verification.checks) for r in runs
        ),
        "token_usage": _token_usage(runs),  # None when the provider reports no tokens (demo)
        "mean_generation_seconds": _mean(
            [_float_meta(r, "generation_seconds") for r in runs if _has(r, "generation_seconds")]
        ),
        "cost_per_verified_success_usd": None,
    }
    hidden = [_check_pass(r, "hidden_security") for r in runs]
    hidden_known = [h for h in hidden if h is not None]
    out["hidden_security_pass_rate"] = proportion(sum(hidden_known), len(hidden_known), confidence)
    tasks = sorted({r.task_id for r in runs})
    per_task_n = [sum(r.task_id == t for r in runs) for t in tasks]
    out["n_episodes"] = n
    out["n_tasks"] = len(tasks)
    out["episodes_per_task"] = (
        {"min": min(per_task_n), "max": max(per_task_n)} if per_task_n else None
    )
    out["n_seeds"] = len({str(r.metadata.get("seed")) for r in runs})
    out["reward_threshold"] = reward_threshold

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
    }
    raw_labels = [(r, ground_truth(r)) for r in runs] if ground_truth else []
    labelled: list[tuple[RolloutSummary, bool]] = [(r, g) for r, g in raw_labels if g is not None]
    if labelled:
        tp = sum(r.verification.verdict == Verdict.PASS and g for r, g in labelled)
        fp = sum(r.verification.verdict == Verdict.PASS and not g for r, g in labelled)
        fn = sum(r.verification.verdict != Verdict.PASS and g for r, g in labelled)
        out["verifier_precision"] = tp / (tp + fp) if tp + fp else None
        out["verifier_recall"] = tp / (tp + fn) if tp + fn else None
        out["labelled_n"] = len(labelled)
    return out


def _has(r: RolloutSummary, key: str) -> bool:
    return r.metadata.get(key) is not None


def _float_meta(r: RolloutSummary, key: str) -> float:
    return float(str(r.metadata.get(key)))


def _check_pass(r: RolloutSummary, name: str) -> bool | None:
    c = r.verification.check(name)
    if c is None or c.status == CheckStatus.SKIPPED:
        return None
    return c.status == CheckStatus.PASS


def _token_usage(runs: list[RolloutSummary]) -> dict[str, Any] | None:
    prompt = sum(int(str(r.metadata.get("prompt_tokens") or 0)) for r in runs)
    completion = sum(int(str(r.metadata.get("completion_tokens") or 0)) for r in runs)
    if prompt + completion == 0:
        return None
    successes = sum(r.verification.verdict == Verdict.PASS for r in runs)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "tokens_per_episode": (prompt + completion) / len(runs),
        "tokens_per_verified_success": ((prompt + completion) / successes) if successes else None,
    }


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
    return [
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
        }
        for r in runs
    ]
