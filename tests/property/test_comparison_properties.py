"""PR 2 paired comparison invariants (hypothesis)."""

from __future__ import annotations

import json
import random
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.comparison_fakes import StaticResolver, cep, config, request, run
from traceforge.contracts.verification import Verdict
from traceforge.modules.evaluation.comparison import ComparisonRefused, compare
from traceforge.modules.evaluation.comparison_reports import comparison_document
from traceforge.modules.evaluation.serialization import dumps_finite

VERDICTS = [Verdict.PASS, Verdict.FAIL, Verdict.PARTIAL]


@st.composite
def designs(draw: st.DrawFn) -> dict[str, Any]:
    n_scen = draw(st.integers(2, 5))
    per = draw(st.lists(st.integers(1, 3), min_size=n_scen, max_size=n_scen))
    seeds = draw(st.integers(1, 2))
    table = {
        f"s{i}i{j}": (f"S{i}", f"f{i % 2}", "t", "private_test", "hard")
        for i in range(n_scen)
        for j in range(per[i])
    }
    n = len(table) * seeds
    vb = draw(st.lists(st.sampled_from(VERDICTS), min_size=n, max_size=n))
    vc = draw(st.lists(st.sampled_from(VERDICTS), min_size=n, max_size=n))
    dur = draw(st.lists(st.floats(0.5, 5.0), min_size=n, max_size=n))
    return {"table": table, "seeds": seeds, "vb": vb, "vc": vc, "dur": dur}


def build(d: dict[str, Any], shift: float = 0.0, copies: int = 1) -> tuple[Any, Any]:
    b, c = [], []
    i = 0
    for task in d["table"]:
        for s in range(d["seeds"]):
            for k in range(copies):
                seed = s + d["seeds"] * k
                b.append(cep(task, d["vb"][i], seed=seed, duration=d["dur"][i]))
                c.append(cep(task, d["vc"][i], seed=seed, duration=d["dur"][i] + shift))
            i += 1
    seeds = list(range(d["seeds"] * copies))
    return run(b, config(seeds=seeds)), run(c, config(seeds=seeds))


METRICS = ["verified_success", "mean_duration_seconds", "hidden_security_pass_rate"]


def go(b: Any, c: Any, d: dict[str, Any]) -> dict[str, Any]:
    return compare(
        b, c, request(metrics=METRICS, bootstrap_samples=200), StaticResolver(d["table"])
    )


def diff(doc: dict[str, Any], key: str = "verified_success") -> dict[str, Any]:
    return doc["groups"]["overall"]["metrics"][key]["difference"]  # type: ignore[no-any-return]


PROPS = settings(max_examples=25, deadline=None)


@PROPS
@given(designs())
def test_p1_swapping_arms_negates_estimate_and_mirrors_interval(d: dict[str, Any]) -> None:
    b, c = build(d)
    fwd, rev = diff(go(b, c, d)), diff(go(c, b, d))
    assert rev["estimate"] == pytest.approx(-fwd["estimate"], abs=1e-12)
    if fwd["ci_low"] is not None:
        assert rev["ci_low"] == pytest.approx(-fwd["ci_high"], abs=1e-12)
        assert rev["ci_high"] == pytest.approx(-fwd["ci_low"], abs=1e-12)


@PROPS
@given(designs())
def test_p2_an_arm_compared_with_itself_has_zero_difference(d: dict[str, Any]) -> None:
    b, _ = build(d)
    out = diff(go(b, b, d))
    assert out["estimate"] == 0.0 and out["ci_low"] == 0.0 and out["ci_high"] == 0.0


@PROPS
@given(designs(), st.floats(-2.0, 2.0))
def test_p3_a_constant_shift_moves_estimate_and_interval(d: dict[str, Any], c0: float) -> None:
    b, c = build(d)
    b2, c2 = build(d, shift=c0)
    one, two = (
        diff(go(b, c, d), "mean_duration_seconds"),
        diff(go(b2, c2, d), "mean_duration_seconds"),
    )
    assert two["estimate"] == pytest.approx(one["estimate"] + c0, abs=1e-9)
    assert two["ci_low"] == pytest.approx(one["ci_low"] + c0, abs=1e-9)


@PROPS
@given(designs(), st.randoms(use_true_random=False))
def test_p4_record_order_does_not_matter_and_results_are_deterministic(
    d: dict[str, Any], rnd: random.Random
) -> None:
    b, c = build(d)
    one = go(b, c, d)
    rnd.shuffle(b.records)
    rnd.shuffle(c.records)
    two = go(b, c, d)
    assert one["groups"] == two["groups"] and one["per_task"] == two["per_task"]


@PROPS
@given(designs(), st.integers(2, 3))
def test_p5_uniform_duplication_is_invariant(d: dict[str, Any], m: int) -> None:
    one, many = diff(go(*build(d), d)), diff(go(*build(d, copies=m), d))
    for k in ("estimate", "ci_low", "ci_high", "n_clusters"):
        assert (
            many[k] == pytest.approx(one[k], abs=1e-12) if one[k] is not None else many[k] is None
        )


@PROPS
@given(designs())
def test_p6_scenarios_never_exceed_instances_or_pairs(d: dict[str, Any]) -> None:
    doc = go(*build(d), d)
    m = doc["matching"]
    assert m["n_structural_scenarios"] <= m["n_task_instances"] <= m["n_pairs"]
    assert m["n_structural_scenarios"] == len({v[0] for v in d["table"].values()})
    assert diff(doc)["n_clusters"] == m["n_structural_scenarios"]


@PROPS
@given(designs(), st.data())
def test_p7_strict_mode_never_compares_unequal_key_sets(
    d: dict[str, Any], data: st.DataObject
) -> None:
    b, c = build(d)
    victim = data.draw(st.integers(0, len(c.records) - 1))
    del c.records[victim]
    with pytest.raises(ComparisonRefused):
        go(b, c, d)


@PROPS
@given(designs())
def test_p8_documents_are_strict_finite_json(d: dict[str, Any]) -> None:
    text = dumps_finite(comparison_document(go(*build(d), d)))
    json.loads(text, parse_constant=lambda x: pytest.fail(f"non-finite {x}"))
