"""PR 2 matching rules, seeds, Class B estimands, cost coverage, tiers and interpretation."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tests.unit.comparison_fakes import StaticResolver, cep, config, request, run
from traceforge.contracts.comparison import DEFAULT_VARIED_FACTORS, ComparisonRequest, RunRef
from traceforge.contracts.verification import Verdict
from traceforge.modules.evaluation.comparison import ComparisonRefused, RunData, compare
from traceforge.modules.evaluation.comparison_contract import IN_DISTRIBUTION_NOTE, scenario_tier

P, F = Verdict.PASS, Verdict.FAIL


def table(n: int, split: str = "private_test", per: int = 1) -> dict[str, tuple[str, ...]]:
    """n scenarios with ``per`` instances each, one family per scenario."""
    return {
        f"s{i}i{j}": (f"S{i:02d}", f"fam{i % 3}", "t", split, "hard")
        for i in range(n)
        for j in range(per)
    }


def arms(
    tab: dict[str, tuple[str, ...]],
    vb: Any = F,
    vc: Any = P,
    seeds: tuple[int, ...] = (0,),
    **kw: Any,
) -> tuple[RunData, RunData]:
    b = [cep(t, vb(t, s) if callable(vb) else vb, seed=s, **kw) for t in tab for s in seeds]
    c = [cep(t, vc(t, s) if callable(vc) else vc, seed=s, **kw) for t in tab for s in seeds]
    return run(b, config(seeds=list(seeds))), run(c, config(seeds=list(seeds)))


def go(b: RunData, c: RunData, tab: dict[str, tuple[str, ...]], **req: Any) -> dict[str, Any]:
    return compare(b, c, request(**req), StaticResolver(tab))  # type: ignore[arg-type]


def refused(b: RunData, c: RunData, tab: dict[str, tuple[str, ...]], **req: Any) -> set[str]:
    with pytest.raises(ComparisonRefused) as exc:
        go(b, c, tab, **req)
    m = exc.value.document["matching"]
    assert m["status"] == "refused" and m["official"] is False
    return {p["code"] for p in m["problems"]}


def vs(doc: dict[str, Any], group: str = "private_test") -> dict[str, Any]:
    return doc["groups"]["by_split"][group]["metrics"]["verified_success"]  # type: ignore[no-any-return]


# ------------------------------------------------------------------- matching


def test_m1_coverage_mismatch_strict_refuses_intersection_is_diagnostic() -> None:
    tab = table(4)
    b, c = arms(tab)
    c.records = c.records[:-1]
    assert "coverage_mismatch" in refused(b, c, tab)
    doc = go(b, c, tab, coverage_policy="intersection")
    m = doc["matching"]
    assert m["status"] == "diagnostic" and m["official"] is False
    assert m["excluded"] == [
        {"key": ["s3i0", "1.1.0", 0], "side": "baseline_only", "reason": "no candidate episode"}
    ]
    assert doc["evidence_tier"] == "diagnostic" and m["n_pairs"] == 3
    assert vs(doc)["interpretation"] in ("diagnostic_only", "undefined")
    with pytest.raises(ValidationError, match="strict"):
        request(coverage_policy="intersection", report_tier="final", bootstrap_samples=10_000)


def test_m2_duplicate_observations_are_refused_in_both_modes() -> None:
    tab = table(3)
    for policy in ("strict", "intersection"):
        b, c = arms(tab)
        b.records.append(cep("s0i0", P, seed=0))
        assert "duplicate_observation" in refused(b, c, tab, coverage_policy=policy)


def test_m3_version_verifier_and_reward_policy_rules() -> None:
    tab = table(3)
    b, c = arms(tab)
    c.records[0].task_version = "1.0.0"
    assert "task_version_mismatch" in refused(b, c, tab, coverage_policy="intersection")
    b, c = arms(tab)
    c.records[0].verification.verifier_version = "1.0.0"
    assert "verifier_mismatch" in refused(b, c, tab, coverage_policy="intersection")
    b, c = arms(tab)
    for r in c.records:
        r.reward.policy_hash = "other"
    assert "reward_policy_mismatch" in refused(b, c, tab)
    doc = go(b, c, tab, coverage_policy="intersection")
    metrics = doc["groups"]["overall"]["metrics"]
    for key in ("reward_positive_rate", "false_positive_reward_rate", "mean_reward"):
        assert metrics[key]["interpretation"] == "not_comparable"
    assert metrics["verified_success"]["not_comparable_reason"] is None
    assert any(w["code"] == "reward_policy_mismatch" for w in doc["warnings"])


def test_m4_config_rules_varied_must_match_and_never_variable() -> None:
    tab = table(3)
    b, c = arms(tab)
    c.config = config(seeds=[0], **{"provider.temperature": 0.7})
    assert "config_mismatch" in refused(b, c, tab)
    assert go(b, c, tab, coverage_policy="intersection")["matching"]["status"] == "diagnostic"
    doc = go(b, c, tab, varied_factors=[*DEFAULT_VARIED_FACTORS, "provider.temperature"])
    assert doc["matching"]["status"] == "matched"
    diff = doc["matching"]["config_differences"]
    assert diff == [
        {"path": "provider.temperature", "baseline": None, "candidate": 0.7, "allowed": True}
    ]
    b, c = arms(tab)
    c.config = config(seeds=[0], **{"provider.model_id": "other-model", "name": "cand"})
    assert go(b, c, tab)["matching"]["official"] is True  # varied factors by default
    b, c = arms(tab)
    c.config = config(seeds=[0], verifier_profile="v2_visible_tests")
    assert "never_variable_config" in refused(b, c, tab, coverage_policy="intersection")
    with pytest.raises(ValidationError, match="can never differ"):
        request(varied_factors=["splits"])


def test_m5_non_authoritative_or_unofficial_runs_are_never_official() -> None:
    tab = table(3)
    b, c = arms(tab, profile="v2_visible_tests", authoritative=False)
    doc = go(b, c, tab)
    assert doc["matching"]["status"] == "matched" and doc["matching"]["official"] is False
    assert (
        doc["evidence_tier"] == "diagnostic"
        and doc["protocol"]["verifier"]["authoritative"] is False
    )
    for attr, value, code in (
        ("status", "cancelled", "run_not_completed"),
        ("run_mode", "diagnostic", "run_not_official"),
        ("leakage_ok", False, "run_not_official"),
    ):
        b, c = arms(tab)
        setattr(c, attr, value)
        doc = go(b, c, tab)
        assert doc["matching"]["official"] is False
        assert code in {p["code"] for p in doc["matching"]["problems"]}
    b, c = arms(tab)
    c.ref = RunRef(
        source="bundle", evaluation_id=c.ref.evaluation_id, bundle_dir="x", config_name="y"
    )
    assert go(b, c, tab)["matching"]["official"] is False


def test_m6_provider_seed_must_match_for_official_comparisons() -> None:
    assert "provider.seed" not in DEFAULT_VARIED_FACTORS
    with pytest.raises(ValidationError, match=r"provider.seed"):
        request(varied_factors=["provider.seed"])
    tab = table(3)
    b, c = arms(tab)
    c.config = config(seeds=[0], **{"provider.seed": 7})
    assert "provider_seed_mismatch" in refused(b, c, tab)
    doc = go(b, c, tab, coverage_policy="intersection")
    ps = doc["seeds"]["provider_seed"]
    assert (ps["baseline"], ps["candidate"], ps["matched"]) == (None, 7, False)
    assert any(w["code"] == "provider_seed_mismatch" for w in doc["warnings"])
    assert "ignore" in doc["seeds"]["provider_seed_note"]


def test_m6_bootstrap_seed_changes_intervals_never_pairing() -> None:
    tab = table(6)
    b, c = arms(tab, vb=lambda t, s: P if t in ("s0i0", "s3i0") else F)
    d0, d1 = go(b, c, tab, bootstrap_seed=0), go(b, c, tab, bootstrap_seed=99)
    assert d0["matching"] == d1["matching"] and d0["per_task"] == d1["per_task"]
    assert vs(d0)["difference"]["estimate"] == vs(d1)["difference"]["estimate"]
    assert d0["seeds"]["bootstrap_seed"] == 0 and d1["seeds"]["bootstrap_seed"] == 99


def test_empty_run_is_refused() -> None:
    tab = table(2)
    b, c = arms(tab)
    c.records = []
    assert "empty_run" in refused(b, c, tab, coverage_policy="intersection")


# ------------------------------------------------------------------- Class B


def _conditional(b_fail: set[str], c_fail: set[str]) -> dict[str, Any]:
    tab = table(4, per=1)
    base, cand = [], []
    for t in tab:
        base.append(
            cep(
                t,
                F,
                termination="provider_error" if t in b_fail else "final_report",
                provider_failure="other" if t in b_fail else None,
            )
        )
        cand.append(
            cep(
                t,
                P if t not in c_fail else F,
                termination="provider_error" if t in c_fail else "final_report",
                provider_failure="timeout" if t in c_fail else None,
            )
        )
    return go(run(base, config(seeds=[0])), run(cand, config(seeds=[0])), tab)


def test_b1_b2_conditional_population_excludes_pairs_with_any_provider_failure() -> None:
    doc = _conditional(b_fail={"s0i0"}, c_fail={"s1i0"})
    m = doc["groups"]["overall"]["metrics"]["paired_conditional.verified_success"]
    assert m["coverage"] == {"pairs_used": 2, "pairs_total": 4}  # s2, s3 only
    assert (m["baseline"]["estimate"], m["candidate"]["estimate"]) == (0.0, 1.0)
    assert m["difference"]["estimate"] == 1.0 and m["class"] == "B"
    # legacy termination-reason failures (no telemetry) are detected too
    legacy = _conditional(b_fail=set(), c_fail=set())
    assert (
        legacy["groups"]["overall"]["metrics"]["paired_conditional.verified_success"]["coverage"][
            "pairs_used"
        ]
        == 4
    )


def test_b3_empty_conditional_population_is_null() -> None:
    doc = _conditional(b_fail={"s0i0", "s1i0"}, c_fail={"s2i0", "s3i0"})
    m = doc["groups"]["overall"]["metrics"]["paired_conditional.verified_success"]
    assert m["difference"]["estimate"] is None and m["coverage"]["pairs_used"] == 0
    assert m["interpretation"] in ("not_comparable", "undefined")


def test_b4_class_b_is_never_directional_even_on_the_primary_endpoint() -> None:
    tab = table(25)
    b, c = arms(tab)
    doc = go(b, c, tab)
    pm = doc["groups"]["by_split"]["private_test"]["metrics"]
    assert vs(doc)["interpretation"] == "improvement"
    assert pm["paired_conditional.verified_success"]["interpretation"] == "secondary"
    assert pm["provider_retries_per_episode"]["interpretation"] in ("secondary", "undefined")


def test_b5_paired_conditional_differs_from_pr1b_per_arm_conditional() -> None:
    from traceforge.modules.evaluation.metrics import summarize

    doc = _conditional(b_fail={"s0i0"}, c_fail={"s1i0"})
    m = doc["groups"]["overall"]["metrics"]["paired_conditional.verified_success"]
    b_recs = [cep(t, F) for t in ("s1i0", "s2i0", "s3i0")]  # baseline non-failed episodes
    c_recs = [cep(t, P) for t in ("s0i0", "s2i0", "s3i0")]  # candidate non-failed episodes
    pr1b = (
        summarize(c_recs, bootstrap_samples=100)["verified_success"]["rate"]
        - summarize(b_recs, bootstrap_samples=100)["verified_success"]["rate"]
    )
    assert pr1b == 1.0 and m["difference"]["estimate"] == 1.0
    assert m["coverage"]["pairs_used"] == 2 != len(b_recs)  # different populations
    assert m["population"].startswith("pairs where neither arm")


def test_b6_retries_need_complete_attempt_coverage() -> None:
    tab = table(3)
    b, c = arms(tab)
    m = go(b, c, tab)["groups"]["overall"]["metrics"]["provider_retries_per_episode"]
    assert m["not_comparable_reason"] is None
    b, c = arms(tab)
    c.records[0].metadata["provider_attempts"] = None
    c.records[0].metadata["provider_retries"] = None
    m = go(b, c, tab)["groups"]["overall"]["metrics"]["provider_retries_per_episode"]
    assert m["interpretation"] == "not_comparable" and "attempt" in m["not_comparable_reason"]


# ------------------------------------------------------------------- cost


@pytest.mark.parametrize(
    ("cost_b", "cost_c", "comparable"),
    [
        (0.01, 0.02, True),
        (0.0, 0.0, True),  # explicitly free providers are known
        (None, 0.02, False),
        (1e308, 0.02, False),  # baseline total overflows
    ],
)
def test_c1_cost_differences_need_complete_coverage_in_both_arms(
    cost_b: float | None, cost_c: float, comparable: bool
) -> None:
    tab = table(3)
    b, c = arms(tab, cost=cost_c)
    for r in b.records:
        r.metadata["cost_usd"] = cost_b
        r.metadata["n_requests_cost_known"] = 0 if cost_b is None else 1
    doc = go(b, c, tab)
    m = doc["groups"]["overall"]["metrics"]["cost.per_attempt_usd"]
    assert (m["not_comparable_reason"] is None) is comparable
    assert doc["cost_coverage"]["comparable"] is (comparable and cost_b != 1e308)
    if not comparable:
        assert m["difference"]["estimate"] is None
    assert "lower_bound_usd" not in str(m)


def test_c1_partial_cost_lower_bounds_are_reported_never_differenced() -> None:
    tab = table(3)
    b, c = arms(tab, cost=0.01)
    b.records[0].metadata["cost_usd"] = None
    doc = go(b, c, tab)
    assert doc["cost_coverage"]["baseline"]["status"] == "partial"
    assert doc["cost_coverage"]["baseline"]["lower_bound"] == pytest.approx(0.02)
    assert doc["cost_coverage"]["comparable"] is False
    for key in ("cost.per_attempt_usd", "cost.per_verified_success_usd"):
        assert doc["groups"]["overall"]["metrics"][key]["difference"]["estimate"] is None


def test_c2_provider_failure_rates_that_differ_emit_a_confounding_warning() -> None:
    doc = _conditional(b_fail=set(), c_fail={"s1i0"})
    assert any(w["code"] == "provider_failure_confounding" for w in doc["warnings"])


# ------------------------------------------------------------------- tiers


@pytest.mark.parametrize(
    ("n", "tier"),
    [
        (9, "insufficient_scenarios"),
        (10, "exploratory_only"),
        (19, "exploratory_only"),
        (20, "directional_eligible"),
    ],
)
def test_t1_scenario_tier_boundaries(n: int, tier: str) -> None:
    assert scenario_tier(n) == tier
    tab = table(n)
    b, c = arms(tab)
    doc = go(b, c, tab, metrics=["verified_success"])
    assert doc["evidence_tier"] == tier
    expected = "improvement" if tier == "directional_eligible" else tier
    assert vs(doc)["interpretation"] == expected


def test_t1_instances_do_not_raise_the_scenario_tier() -> None:
    tab = table(9, per=4)  # 36 instances, 9 scenarios
    b, c = arms(tab, seeds=(0, 1))
    doc = go(b, c, tab, metrics=["verified_success"])
    assert (
        doc["matching"]["n_task_instances"] == 36
        and doc["evidence_tier"] == "insufficient_scenarios"
    )


def test_t2_directional_requires_interval_excluding_zero() -> None:
    tab = table(20)
    flip = {f"s{i}i0" for i in range(10)}
    b, c = arms(tab, vb=lambda t, s: F, vc=lambda t, s: P if t in flip else F)
    doc = go(b, c, tab, metrics=["verified_success"])
    assert vs(doc)["interpretation"] == "improvement"  # 10/20 scenarios improve, none regress
    b, c = arms(tab, vb=lambda t, s: P if t in flip else F, vc=lambda t, s: F if t in flip else P)
    doc = go(b, c, tab, metrics=["verified_success"])
    assert doc["evidence_tier"] == "directional_eligible"
    assert vs(doc)["interpretation"] == "no_detectable_difference"
    b, c = arms(tab, vb=P, vc=F)
    assert vs(go(b, c, tab, metrics=["verified_success"]))["interpretation"] == "regression"


def test_t3_secondary_groups_and_metrics_are_never_directional() -> None:
    tab = {
        **table(25),
        **{f"p{i}": (f"P{i}", "famp", "t", "public_test", "easy") for i in range(25)},
    }
    b, c = arms(tab)
    doc = go(b, c, tab, metrics=["verified_success", "mean_reward"])
    assert doc["groups"]["overall"]["metrics"]["verified_success"]["interpretation"] == "secondary"
    assert vs(doc, "public_test")["interpretation"] == "secondary"
    assert doc["groups"]["by_split"]["private_test"]["metrics"]["mean_reward"][
        "interpretation"
    ] in (
        "secondary",
        "undefined",
    )
    assert doc["groups"]["by_split"]["private_test"]["role"] == "primary"
    assert doc["groups"]["overall"]["role"] == "secondary"


def test_t4_without_private_test_there_is_no_primary_endpoint() -> None:
    tab = table(25, split="public_test")
    b, c = arms(tab)
    doc = go(b, c, tab, metrics=["verified_success"])
    assert doc["evidence_tier"] == "no_primary_endpoint"
    assert doc["primary_endpoint"]["status"] == "unavailable"
    assert doc["primary_endpoint"]["group"] is None
    interpretations = {
        m["interpretation"]
        for g in [doc["groups"]["overall"], *doc["groups"]["by_split"].values()]
        for m in g["metrics"].values()
    }
    assert not interpretations & {"improvement", "regression"}


def test_t5_public_test_carries_the_in_distribution_note() -> None:
    tab = {**table(3), **{f"p{i}": (f"P{i}", "famp", "t", "public_test", "easy") for i in range(3)}}
    b, c = arms(tab)
    doc = go(b, c, tab, metrics=["verified_success"])
    assert doc["groups"]["by_split"]["public_test"]["in_distribution_note"] == IN_DISTRIBUTION_NOTE
    assert doc["groups"]["by_split"]["private_test"]["in_distribution_note"] is None


def test_t6_final_tier_rules_and_exploratory_banner() -> None:
    final = {"report_tier": "final", "bootstrap_samples": 10_000, "metrics": ["verified_success"]}
    tab = table(12)
    b, c = arms(tab)
    doc = go(b, c, tab, **final)
    assert doc["report_tier"] == "final" and doc["evidence_tier"] == "exploratory_only"
    assert "does not support a directional" in doc["evidence_banner"]
    assert vs(doc)["interpretation"] == "exploratory_only"
    tab9 = table(9)
    b, c = arms(tab9)
    assert "final_tier_requirement" in refused(b, c, tab9, **final)
    b, c = arms(tab, profile="v2_visible_tests", authoritative=False)
    assert "final_tier_requirement" in refused(b, c, tab, **final)
    with pytest.raises(ValidationError, match="10000"):
        request(report_tier="final", bootstrap_samples=2000)
    with pytest.raises(ValidationError, match="database"):
        ComparisonRequest.model_validate(
            {
                "baseline": {
                    "source": "bundle",
                    "evaluation_id": c.ref.evaluation_id,
                    "bundle_dir": "d",
                    "config_name": "n",
                },
                "candidate": {"evaluation_id": c.ref.evaluation_id},
                "report_tier": "final",
                "bootstrap_samples": 10_000,
            }
        )


# ------------------------------------------------------------------- review regressions


def test_seed_order_does_not_matter() -> None:
    tab = table(3)
    b, c = arms(tab, seeds=(0, 1))
    c.config = config(seeds=[1, 0])
    doc = go(b, c, tab)
    assert doc["matching"]["status"] == "matched" and doc["matching"]["official"] is True


def test_primary_metric_is_always_computed_for_the_evidence_tier() -> None:
    tab = table(25)
    b, c = arms(tab)
    doc = go(b, c, tab, metrics=["mean_reward"])
    assert "verified_success" in doc["groups"]["by_split"]["private_test"]["metrics"]
    assert doc["evidence_tier"] == "directional_eligible"


def test_seeds_block_states_which_seed_reaches_the_provider() -> None:
    tab = table(2)
    b, c = arms(tab)
    sent = go(b, c, tab)["seeds"]["provider_seed"]["sent_to_provider"]
    assert "episode seed" in sent
