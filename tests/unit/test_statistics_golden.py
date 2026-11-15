"""Golden tests: historical statistical behaviour is frozen.

`tests/unit/data/golden_statistics_v02.json` was generated from main at 99de348 (before v0.3
PR 1B). Wilson intervals, proportions with a non-empty denominator, the mean bootstrap and the
pooled-ratio task-cluster bootstrap must keep producing exactly these values for fixed inputs
and seeds, and benchmark config hashes must not move. v0.3 metric work may add outputs; it may
not change these.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from traceforge.contracts.experiments import BenchmarkConfig, ProviderConfig
from traceforge.modules.evaluation.statistics import (
    bootstrap_mean_interval,
    cluster_bootstrap,
    proportion,
    wilson_interval,
)

GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "golden_statistics_v02.json").read_text(encoding="utf-8")
)


def _same(a: object, b: object) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@pytest.mark.parametrize("case", GOLDEN["wilson"], ids=lambda c: f"{c[0]}/{c[1]}@{c[2]}")
def test_wilson_interval_is_unchanged(case: list[object]) -> None:
    s, n, conf, expected = case
    assert list(wilson_interval(s, n, conf)) == expected  # type: ignore[arg-type]


def test_proportion_with_non_empty_denominator_is_unchanged() -> None:
    for expected in GOLDEN["proportion"]:
        got = proportion(expected["successes"], expected["n"], expected["confidence"])
        assert _same(got, expected), (got, expected)


def test_mean_bootstrap_is_unchanged() -> None:
    got = bootstrap_mean_interval([0.1, 0.5, 0.9, 1.0, 0.0], samples=500, seed=3)
    assert list(got) == GOLDEN["bootstrap_mean"]


def test_pooled_ratio_cluster_bootstrap_is_unchanged() -> None:
    inputs = [
        (([[1.0, 1.0], [0.0], [1.0, 0.0, 1.0], [0.0, 0.0]],), {"samples": 500, "seed": 3}),
        (
            ([[1.0] * 3 for _ in range(5)] + [[0.0] * 3 for _ in range(5)],),
            {"samples": 2000, "seed": 0},
        ),
        (([[1.0] * 8, [0.0]],), {"samples": 2000, "seed": 0}),
        (([[1.0, 0.0, 1.0]],), {}),
        (([],), {}),
    ]
    for (args, kwargs), expected in zip(inputs, GOLDEN["cluster"], strict=True):
        assert _same(cluster_bootstrap(*args, **kwargs), expected)


def test_benchmark_config_hashes_are_unchanged() -> None:
    hashes = GOLDEN["hashes"]
    assert BenchmarkConfig().config_hash == hashes["default"]
    seeded = BenchmarkConfig(seeds=(0, 1, 2), provider=ProviderConfig(policy="reference"))
    assert seeded.config_hash == hashes["seeds"]
    assert BenchmarkConfig(bootstrap_samples=10000).config_hash == hashes["bootstrap10k"]
    assert BenchmarkConfig(run_mode="diagnostic").config_hash == hashes["diagnostic"]
