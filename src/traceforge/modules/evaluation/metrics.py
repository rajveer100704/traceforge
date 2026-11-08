"""Benchmark metrics computed from rollout summaries (real values only)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from traceforge.contracts.experiments import RolloutSummary
from traceforge.contracts.verification import CheckStatus, Verdict
from traceforge.modules.evaluation.statistics import bootstrap_mean_interval, proportion

REWARD_POSITIVE_THRESHOLD = 0.5


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def summarize(
    runs: list[RolloutSummary],
    *,
    confidence: float = 0.95,
    bootstrap_samples: int = 2000,
    seed: int = 0,
    ground_truth: Callable[[RolloutSummary], bool | None] | None = None,
) -> dict[str, Any]:
    n = len(runs)
    verified = sum(r.verification.verdict == Verdict.PASS for r in runs)
    partial = sum(r.verification.verdict == Verdict.PARTIAL for r in runs)
    errors = sum(r.verification.verdict in (Verdict.ERROR, Verdict.INCONCLUSIVE) for r in runs)
    rewarded = [r for r in runs if r.reward.final_reward >= REWARD_POSITIVE_THRESHOLD]
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
        "token_usage": None,  # demo provider reports no tokens; populated for model providers
        "cost_per_verified_success_usd": None,
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
