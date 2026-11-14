"""Property tests for schema-2 metric aggregation (missingness never becomes zero)."""

from __future__ import annotations

import json
import math
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.metric_fakes import ep
from traceforge.contracts.verification import Verdict
from traceforge.modules.evaluation.metrics import summarize
from traceforge.modules.evaluation.serialization import dumps_finite
from traceforge.modules.evaluation.statistics import percentiles

costs = st.one_of(st.none(), st.floats(min_value=0, max_value=5, allow_nan=False))
usages = st.one_of(st.none(), st.tuples(st.integers(0, 5000), st.integers(0, 5000)))
episodes = st.builds(
    lambda task, verdict, cost, usage, secs: ep(
        task=f"t{task}",
        verdict=verdict,
        reward=1.0 if verdict == Verdict.PASS else 0.0,
        cost=cost,
        usage=usage,
        requests=len(secs) or 1,
        request_seconds=secs or [0.1],
    ),
    st.integers(0, 4),
    st.sampled_from([Verdict.PASS, Verdict.FAIL, Verdict.PARTIAL, Verdict.ERROR]),
    costs,
    usages,
    st.lists(st.floats(min_value=0, max_value=60, allow_nan=False), max_size=4),
)
run_lists = st.lists(episodes, max_size=12)
SETTINGS = settings(max_examples=60, deadline=None)


def _s(runs: list[Any]) -> dict[str, Any]:
    return summarize(runs, bootstrap_samples=100)  # type: ignore[arg-type]


@SETTINGS
@given(run_lists)
def test_coverage_is_bounded(runs: list[Any]) -> None:
    for c in _s(runs)["telemetry_coverage"].values():
        assert 0 <= c["covered"] <= c["n"] == len(runs)
        assert c["fraction"] is None if not runs else 0.0 <= c["fraction"] <= 1.0


@SETTINGS
@given(run_lists)
def test_per_success_is_null_exactly_when_undefined(runs: list[Any]) -> None:
    o = _s(runs)
    successes = o["verified_success"]["successes"]
    total = o["cost"]["total_usd"]
    per = o["cost"]["per_verified_success_usd"]
    if successes == 0 or total is None:
        assert per is None
    else:
        assert per is not None and math.isclose(per, total / successes)
    assert o["cost_per_verified_success_usd"] == per
    if successes == 0:
        assert o["tokens"]["per_verified_success"] is None


@SETTINGS
@given(run_lists)
def test_cost_total_and_lower_bound_semantics(runs: list[Any]) -> None:
    o = _s(runs)
    known = [r.metadata["cost_usd"] for r in runs if r.metadata["cost_usd"] is not None]
    c = o["cost"]
    if runs and len(known) == len(runs):
        assert (
            math.isclose(c["total_usd"], sum(known), abs_tol=1e-9) and c["lower_bound_usd"] is None
        )
    else:
        assert c["total_usd"] is None
        if known:
            assert math.isclose(c["lower_bound_usd"], sum(known), abs_tol=1e-9)
        else:
            assert c["lower_bound_usd"] is None


@SETTINGS
@given(run_lists, st.integers(1, 3))
def test_unreported_episodes_never_change_known_totals_into_numbers(
    runs: list[Any], extra: int
) -> None:
    unknown = [ep(task=f"u{i}", cost=None, usage=None) for i in range(extra)]
    o = _s(runs + unknown)
    assert o["cost"]["total_usd"] is None and o["tokens"]["total"] is None
    before = _s(runs)
    lb_before = (
        before["cost"]["total_usd"]
        if before["cost"]["total_usd"] is not None
        else (before["cost"]["lower_bound_usd"])
    )
    if lb_before is not None:
        assert math.isclose(o["cost"]["lower_bound_usd"], lb_before, abs_tol=1e-9)


@SETTINGS
@given(st.lists(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False), max_size=40))
def test_percentiles_are_monotone(values: list[float]) -> None:
    p = percentiles(values)
    assert p["n"] == len(values)
    if values:
        assert p["p50"] <= p["p90"] <= p["p95"] <= p["max"] == max(values)


@SETTINGS
@given(run_lists)
def test_summary_serialises_as_strict_json(runs: list[Any]) -> None:
    json.loads(dumps_finite(_s(runs)))  # never raises; NaN/Infinity cannot appear
