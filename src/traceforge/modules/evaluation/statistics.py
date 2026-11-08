"""Uncertainty estimates for small-sample benchmark proportions."""

from __future__ import annotations

import math
import random
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
    lo, hi = wilson_interval(successes, n, confidence)
    return {
        "successes": successes,
        "n": n,
        "rate": (successes / n) if n else 0.0,
        "ci_low": lo,
        "ci_high": hi,
        "confidence": confidence,
        "method": "wilson",
    }
