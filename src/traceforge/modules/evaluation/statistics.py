"""Uncertainty estimates for small-sample benchmark proportions."""

from __future__ import annotations

import math
import random
import statistics
from statistics import NormalDist
from typing import Any


def z_for(confidence: float) -> float:
    return NormalDist().inv_cdf(0.5 + confidence / 2.0)


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. (0, 1) when n == 0 (no information)."""
    if n < 0 or successes < 0 or successes > n:
        raise ValueError("require 0 <= successes <= n")
    if n == 0:
        return 0.0, 1.0
    z = z_for(confidence)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_mean_interval(
    values: list[float], *, samples: int = 2000, confidence: float = 0.95, seed: int = 0
) -> tuple[float, float]:
    """Percentile bootstrap CI for a mean. Deterministic for a fixed seed."""
    if not values:
        return 0.0, 0.0
    rng = random.Random(seed)  # noqa: S311 - statistical resampling, not security
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(samples))
    lo_idx = int(((1 - confidence) / 2) * samples)
    hi_idx = min(samples - 1, int((1 - (1 - confidence) / 2) * samples) - 1)
    return means[lo_idx], means[hi_idx]


def proportion(successes: int, n: int, confidence: float = 0.95) -> dict[str, Any]:
    """Wilson proportion. An empty denominator has no rate: ``rate`` is None (never 0.0), and
    the interval is (0, 1), meaning no information. Non-empty results are unchanged from v0.2."""
    lo, hi = wilson_interval(successes, n, confidence)
    return {
        "successes": successes,
        "n": n,
        "rate": (successes / n) if n else None,
        "ci_low": lo,
        "ci_high": hi,
        "confidence": confidence,
        "method": "wilson",
    }


def format_rate(rate: float | None, spec: str = ".1%") -> str:
    """Format a proportion's rate for reports; an undefined rate (empty denominator) is 'n/a'."""
    return "n/a" if rate is None else format(rate, spec)


PERCENTILE_METHOD = (
    "n=0: all null; n=1: every percentile equals the single value; n>=2: "
    "statistics.quantiles(n=100, method='inclusive') (linear interpolation, clamped to the "
    "sample range); max = maximum"
)


def percentiles(values: list[float]) -> dict[str, Any]:
    """p50/p90/p95/max of a sample, with its size. Defined for every n (see PERCENTILE_METHOD)."""
    vals = sorted(
        float(v)
        for v in values
        if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)
    )
    out: dict[str, Any] = {"n": len(vals), "method": PERCENTILE_METHOD}
    if not vals:
        return {**out, "p50": None, "p90": None, "p95": None, "max": None}
    if len(vals) == 1:
        v = vals[0]
        return {**out, "p50": v, "p90": v, "p95": v, "max": v}
    cuts = statistics.quantiles(vals, n=100, method="inclusive")  # cuts[k-1] = k-th percentile
    # Linear interpolation can overshoot by one float ulp (e.g. two equal values give a p95 just
    # above the max). Clamp to the sample range and keep p50 <= p90 <= p95 <= max exactly.
    lo, hi = vals[0], vals[-1]
    p50 = min(max(cuts[49], lo), hi)
    p90 = min(max(cuts[89], p50), hi)
    p95 = min(max(cuts[94], p90), hi)
    return {**out, "p50": p50, "p90": p90, "p95": p95, "max": hi}


CLUSTER_METHOD = "task-cluster percentile bootstrap (pooled ratio)"
# Documented (not changed) in v0.3: the estimand is the pooled episode-level ratio, so a task
# contributes in proportion to its number of episodes. With equal episodes per task (the benchmark
# runner's design) this equals the mean of per-task rates; with unequal counts it does not.
CLUSTER_ESTIMAND = (
    "pooled ratio sum(values)/count over all episodes; tasks are weighted by their episode "
    "count (equal weights when every task has the same number of episodes)"
)


def cluster_bootstrap(
    clusters: list[list[float]],
    *,
    samples: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
) -> dict[str, Any]:
    """Cluster (task-level) bootstrap CI for a pooled mean of per-episode values.

    Episodes of the same task are not independent (same fixture, same hidden tests, and for a
    deterministic provider identical outcomes across seeds). Resampling whole tasks with
    replacement gives an interval whose width reflects the number of *independent tasks*, not the
    number of episodes. The estimate is the pooled ratio sum(values) / count over all episodes,
    which equals the episode-level proportion. Deterministic for a fixed seed.

    With fewer than two clusters the interval is undefined and reported as None.
    """
    clusters = [c for c in clusters if c]
    n_obs = sum(len(c) for c in clusters)
    base: dict[str, Any] = {
        "estimate": (sum(sum(c) for c in clusters) / n_obs) if n_obs else None,
        "ci_low": None,
        "ci_high": None,
        "n_clusters": len(clusters),
        "n_observations": n_obs,
        "confidence": confidence,
        "samples": samples,
        "method": CLUSTER_METHOD,
    }
    if len(clusters) < 2:
        base["note"] = "fewer than 2 independent tasks: cluster interval undefined"
        return base
    rng = random.Random(seed)  # noqa: S311 - statistical resampling, not security
    sums = [(sum(c), len(c)) for c in clusters]
    k = len(sums)
    stats = []
    for _ in range(samples):
        tot = cnt = 0.0
        for _j in range(k):
            s, n = sums[rng.randrange(k)]
            tot += s
            cnt += n
        stats.append(tot / cnt)
    stats.sort()
    lo_idx = int(((1 - confidence) / 2) * samples)
    hi_idx = min(samples - 1, int((1 - (1 - confidence) / 2) * samples) - 1)
    base["ci_low"], base["ci_high"] = stats[lo_idx], stats[hi_idx]
    return base
