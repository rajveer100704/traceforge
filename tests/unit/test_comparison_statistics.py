"""PR 2 paired comparison statistics: golden values, cluster invariants, estimand preservation."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from tests.unit.comparison_fakes import (
    GOLDEN_DIFFS,
    StaticResolver,
    cep,
    config,
    golden_compare,
    golden_runs,
    golden_table,
    request,
    run,
)
from traceforge.contracts.verification import CheckStatus, Verdict
from traceforge.modules.evaluation import comparison as comparison_mod
from traceforge.modules.evaluation.comparison import compare
from traceforge.modules.evaluation.comparison_contract import COMPARABLE_METRICS
from traceforge.modules.evaluation.metrics import summarize
from traceforge.modules.evaluation.statistics import (
    cluster_bootstrap,
    paired_cluster_ratio_bootstrap,
)

GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "golden_comparison_v1.json").read_text(encoding="utf-8")
)


def _vs(doc: dict[str, Any], group: str = "private_test") -> dict[str, Any]:
    return doc["groups"]["by_split"][group]["metrics"]["verified_success"]  # type: ignore[no-any-return]


def _reference_bootstrap(
    clusters: list[list[float]], samples: int, seed: int
) -> tuple[float, float]:
    """Independent re-implementation of the scenario-cluster percentile bootstrap."""
    rng = random.Random(seed)
    k = len(clusters)
    stats = []
    for _ in range(samples):
        draw = [clusters[rng.randrange(k)] for _ in range(k)]
        stats.append(sum(sum(c) for c in draw) / sum(len(c) for c in draw))
    stats.sort()
    return stats[int(0.025 * samples)], stats[min(samples - 1, int(0.975 * samples) - 1)]


# ------------------------------------------------------------------- S1 golden


def test_s1_golden_fixture_point_estimate_interval_and_counts() -> None:
    doc = golden_compare(bootstrap_samples=2000, bootstrap_seed=0)
    m = _vs(doc)
    d = m["difference"]
    assert d["estimate"] == 0.25 == GOLDEN["verified_success"]["estimate"]
    assert (d["n_clusters"], d["n_observations"]) == (4, 16)
    assert d["cluster_unit"] == "structural_scenario"
    assert (d["ci_low"], d["ci_high"]) == (
        GOLDEN["verified_success"]["ci_low"],
        GOLDEN["verified_success"]["ci_high"],
    )
    ref = _reference_bootstrap([GOLDEN_DIFFS[s] for s in sorted(GOLDEN_DIFFS)], 2000, 0)
    assert (d["ci_low"], d["ci_high"]) == pytest.approx(ref, abs=1e-12)
    counts = doc["groups"]["by_split"]["private_test"]["counts"]
    assert (counts["n_pairs"], counts["n_task_instances"], counts["n_structural_scenarios"]) == (
        16,
        8,
        4,
    )
    assert m["instance_sensitivity"]["n_clusters"] == 8
    assert m["instance_sensitivity"]["ci_low"] == GOLDEN["instance_sensitivity"]["ci_low"]


# ------------------------------------------------------- S2 / S3 cluster invariants


@pytest.mark.parametrize("m", [2, 5])
def test_s2_uniform_duplication_leaves_estimate_and_interval_unchanged(m: int) -> None:
    one, many = _vs(golden_compare()), _vs(golden_compare(copies=m))
    for k in ("estimate", "ci_low", "ci_high", "n_clusters"):
        assert many["difference"][k] == one["difference"][k], k
    assert many["difference"]["n_observations"] == m * one["difference"]["n_observations"]


def _with_extra(scenario: str, extra: list[int]) -> dict[str, Any]:
    """Golden fixture plus extra seeds of one instance of ``scenario`` with given differences."""
    b, c = golden_runs()
    task = f"{scenario.lower()}1"
    for i, d in enumerate(extra):
        seed = 10 + i
        vb, vc = {1: (Verdict.FAIL, Verdict.PASS), -1: (Verdict.PASS, Verdict.FAIL)}.get(
            d, (Verdict.FAIL, Verdict.FAIL)
        )
        b.records.append(cep(task, vb, seed=seed))
        c.records.append(cep(task, vc, seed=seed))
    seeds = sorted({r.metadata["seed"] for r in b.records})
    b.config = c.config = config(seeds=seeds)
    # seeds differ per task now; use intersection-free strict pairing (keys still match)
    return compare(b, c, request(), StaticResolver(golden_table()))


def test_s3a_adding_instances_or_seeds_never_adds_scenarios() -> None:
    base = golden_compare()
    more = _with_extra("A", [1, 1, 0])
    for doc in (base, more):
        assert doc["matching"]["n_structural_scenarios"] == 4
        assert _vs(doc)["difference"]["n_clusters"] == 4
    assert more["matching"]["n_pairs"] == base["matching"]["n_pairs"] + 3


def test_s3b_adding_to_one_scenario_follows_the_pooled_weighting() -> None:
    extra = [1, 1, 0]
    doc = _with_extra("A", extra)
    total = sum(sum(v) for v in GOLDEN_DIFFS.values()) + sum(extra)
    n = sum(len(v) for v in GOLDEN_DIFFS.values()) + len(extra)
    assert _vs(doc)["difference"]["estimate"] == pytest.approx(total / n, abs=1e-12)
    assert _vs(doc)["difference"]["estimate"] != 0.25  # weights legitimately changed


def test_s3c_cloned_instances_never_become_extra_clusters(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real = comparison_mod.cluster_bootstrap

    def spy(clusters: list[list[float]], **kw: Any) -> dict[str, Any]:
        seen.append(len(clusters))
        return real(clusters, **kw)

    monkeypatch.setattr(comparison_mod, "cluster_bootstrap", spy)
    golden_compare(copies=3, metrics=["verified_success"])
    # primary (scenario) calls see 4 clusters; sensitivity (instance) calls see 8; never 24.
    assert set(seen) <= {4, 8, 1, 2} and 4 in seen and max(seen) == 8


def test_s3d_every_group_reports_pairs_instances_and_scenarios_separately() -> None:
    doc = golden_compare(copies=2)
    groups = [doc["groups"]["overall"], *doc["groups"]["by_split"].values()]
    groups += [*doc["groups"]["by_family"].values(), *doc["groups"]["by_difficulty"].values()]
    for g in groups:
        c = g["counts"]
        assert c["n_structural_scenarios"] <= c["n_task_instances"] <= c["n_pairs"]
        assert {"instances_per_scenario", "seeds_per_instance", "scenario_weights"} <= set(c)
    assert doc["matching"]["n_pairs"] == 32 and doc["matching"]["n_structural_scenarios"] == 4


def test_s4_instance_sensitivity_never_drives_interpretation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _vs(golden_compare())["interpretation"]
    real = comparison_mod._mean_boot

    def fake(rows: Any, key: Any, req: Any) -> dict[str, Any]:
        out = real(rows, key, req)
        if key is comparison_mod._instance_key:
            out = {**out, "ci_low": 0.9, "ci_high": 0.95}
        return out

    monkeypatch.setattr(comparison_mod, "_mean_boot", fake)
    after = _vs(golden_compare())
    assert after["instance_sensitivity"]["ci_low"] == 0.9
    assert after["interpretation"] == before


# --------------------------------------------------------------- S5 Class A estimands


def _telemetry_runs() -> tuple[Any, Any, StaticResolver]:
    table = {
        f"k{i}": (
            f"S{i % 5}",
            f"fam{i % 3}",
            "t",
            "private_test" if i % 2 else "public_test",
            "hard" if i % 4 else "easy",
        )
        for i in range(12)
    }
    base, cand = [], []
    for i, task in enumerate(table):
        for seed in (0, 1):
            vb = [Verdict.PASS, Verdict.FAIL, Verdict.PARTIAL, Verdict.ERROR][(i + seed) % 4]
            vc = [Verdict.PASS, Verdict.PASS, Verdict.FAIL, Verdict.PARTIAL][(i * seed + 1) % 4]
            common = {"seed": seed, "calls": 1 + i % 3, "duration": 1.0 + i / 7}
            base.append(
                cep(
                    task,
                    vb,
                    reward=(i % 3) / 2,
                    cost=0.01 * (i + 1),
                    usage=(50 + i, 5),
                    checks={
                        "regression": [CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.ERROR][
                            i % 3
                        ],
                        "hidden_security": [
                            CheckStatus.PASS,
                            CheckStatus.FAIL,
                            CheckStatus.SKIPPED,
                        ][(i + seed) % 3],
                    },
                    **common,
                )
            )
            cand.append(
                cep(
                    task,
                    vc,
                    reward=((i + 1) % 3) / 2,
                    cost=0.02 * (i + 1),
                    usage=(40 + i, 9),
                    attempts=1 + seed,
                    checks={
                        "regression": [
                            CheckStatus.FAIL,
                            CheckStatus.PASS,
                            CheckStatus.INCONCLUSIVE,
                        ][i % 3],
                        "hidden_security": [CheckStatus.FAIL, CheckStatus.PASS, CheckStatus.ERROR][
                            (i + seed) % 3
                        ],
                    },
                    **common,
                )
            )
    return run(base), run(cand), StaticResolver(table)


def _pr1b(summary: dict[str, Any], key: str) -> float | None:
    v: Any = summary
    for part in key.split("."):
        v = v[part]
    if isinstance(v, dict):
        v = v["rate"]
    return v  # type: ignore[no-any-return]


def test_s5_class_a_differences_equal_pr1b_differences_overall_and_per_group() -> None:
    b, c, resolver = _telemetry_runs()
    doc = compare(b, c, request(), resolver)
    class_a = [s for s in COMPARABLE_METRICS if s.cls == "A"]
    selectors = {
        ("overall", None): lambda ref: True,
        **{
            ("by_split", s): (lambda ref, s=s: ref[3] == s) for s in ("private_test", "public_test")
        },
        **{("by_family", f): (lambda ref, f=f: ref[1] == f) for f in ("fam0", "fam1", "fam2")},
        **{("by_difficulty", d): (lambda ref, d=d: ref[4] == d) for d in ("easy", "hard")},
    }
    checked = 0
    for (kind, name), keep in selectors.items():
        group = doc["groups"][kind] if name is None else doc["groups"][kind][name]
        rb = [r for r in b.records if keep(resolver.table[r.task_id])]
        rc = [r for r in c.records if keep(resolver.table[r.task_id])]
        sb, sc = summarize(rb, bootstrap_samples=100), summarize(rc, bootstrap_samples=100)
        for spec in class_a:
            m = group["metrics"][spec.key]
            vb, vc = _pr1b(sb, spec.pr1b_key or spec.key), _pr1b(sc, spec.pr1b_key or spec.key)
            if m["not_comparable_reason"] is not None or vb is None or vc is None:
                continue
            assert m["difference"]["estimate"] == pytest.approx(vc - vb, abs=1e-12), (
                kind,
                name,
                spec.key,
            )
            assert m["baseline"]["estimate"] == pytest.approx(vb, abs=1e-12)
            checked += 1
    assert checked > 80


def test_s5_excludes_class_b_and_c_metrics() -> None:
    classes = {s.key: s.cls for s in COMPARABLE_METRICS}
    assert classes["paired_conditional.verified_success"] == "B"
    assert classes["provider_retries_per_episode"] == "B"
    assert all(s.pr1b_key is None for s in COMPARABLE_METRICS if s.cls != "A")


# ------------------------------------------------------------- S7 / S8 / bootstrap


def test_s7_ratio_bootstrap_with_undefined_replicates_is_null_with_count() -> None:
    # Cluster 2 is the only one with baseline denominator: many replicates miss it.
    clusters = [(1.0, 1.0, 0.0, 0.0), (0.0, 1.0, 1.0, 1.0), (1.0, 1.0, 0.0, 0.0)]
    out = paired_cluster_ratio_bootstrap(clusters, samples=500, seed=0)
    assert out["estimate"] == pytest.approx(2 / 3 - 1.0)
    assert out["ci_low"] is None and out["ci_high"] is None
    assert out["undefined_replicates"] > 0 and str(out["undefined_replicates"]) in out["note"]


def test_paired_ratio_bootstrap_shares_one_draw_and_is_deterministic() -> None:
    clusters = [(1.0, 2.0, 1.0, 2.0), (2.0, 2.0, 0.0, 2.0), (0.0, 1.0, 1.0, 1.0)]
    a = paired_cluster_ratio_bootstrap(clusters, samples=300, seed=7)
    assert a == paired_cluster_ratio_bootstrap(clusters, samples=300, seed=7)
    assert a["undefined_replicates"] == 0 and a["ci_low"] <= a["estimate"] <= a["ci_high"]
    same = [(n, d, n, d) for n, d, _, _ in clusters]  # identical arms: every replicate is 0
    z = paired_cluster_ratio_bootstrap(same, samples=200, seed=1)
    assert (z["estimate"], z["ci_low"], z["ci_high"]) == (0.0, 0.0, 0.0)


def test_s8_fewer_than_two_clusters_gives_null_interval_and_undefined() -> None:
    b = run([cep("x1", Verdict.FAIL, seed=0), cep("x1", Verdict.FAIL, seed=1)])
    c = run([cep("x1", Verdict.PASS, seed=0), cep("x1", Verdict.PASS, seed=1)])
    doc = compare(b, c, request(), StaticResolver({"x1": ("S", "f", "t", "private_test", "hard")}))
    m = _vs(doc)
    assert m["difference"]["estimate"] == 1.0
    assert m["difference"]["ci_low"] is None and m["interpretation"] == "undefined"
    assert cluster_bootstrap([[1.0]], samples=10)["ci_low"] is None
