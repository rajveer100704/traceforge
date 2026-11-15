"""PR 2 structural scenarios: oracle identity, pinned legacy registry, authoritative resolution."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pytest

from tests.unit.comparison_fakes import config, real, request, run
from traceforge.contracts.verification import Verdict
from traceforge.modules.evaluation.comparison import ComparisonRefused, compare
from traceforge.modules.evaluation.scenarios import (
    LEGACY_SCENARIOS,
    CompositeScenarioResolver,
    LegacyScenarioResolver,
    ScenarioError,
)
from traceforge.modules.tasks.oracle_identity import (
    normalise_source,
    oracle_fingerprint,
    oracle_identity,
    source_fingerprint,
)
from traceforge.modules.tasks.registry import VERSION_PROFILES, TaskRegistry


def _fp(b: Any) -> str:
    return oracle_fingerprint(b, VERSION_PROFILES[b.oracle.version]["oracle"])


def _by_template(registry: TaskRegistry) -> dict[tuple[str, str, str], set[str]]:
    out: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for b in registry.all_bundles():
        out[(b.oracle.version, b.oracle.family_id, b.oracle.template_id)].add(_fp(b))
    return out


# ------------------------------------------------------------------- O1 / O2


def test_o1_fingerprint_is_seed_invariant_pinned_and_merges_only_ssrf_t0_t1(
    registry: TaskRegistry,
) -> None:
    per = _by_template(registry)
    assert set(per) == set(LEGACY_SCENARIOS)
    for key, fps in per.items():
        assert len(fps) == 1, f"{key}: fingerprint varies across seeds"
        assert fps == {LEGACY_SCENARIOS[key].oracle_fingerprint}, key
    for version in ("1.0.0", "1.1.0"):
        fps = {k: next(iter(v)) for k, v in per.items() if k[0] == version}
        assert fps[(version, "ssrf", "t0")] == fps[(version, "ssrf", "t1")]
        others = [fp for k, fp in fps.items() if k[1:] not in (("ssrf", "t0"), ("ssrf", "t1"))]
        assert len(set(others)) == len(others), "distinct scenarios must have distinct oracles"
        assert fps[(version, "ssrf", "t0")] not in others


def test_o2_fingerprint_reacts_to_logic_not_to_names_or_literals(registry: TaskRegistry) -> None:
    b = registry.bundle("sqli-t0-s000")
    hidden = b.hidden_files["test_hidden.py"]
    base = source_fingerprint(hidden)
    renamed = hidden.replace("def test_", "def check_renamed_")
    assert source_fingerprint(renamed) == base  # identifiers abstracted
    assert normalise_source("x = 'a'\n") == normalise_source('z = "other value"\n')  # literals
    assert "==" in hidden
    assert source_fingerprint(hidden.replace("==", "!=", 1)) != base  # operator changed
    assert source_fingerprint(hidden + "\n\ndef test_x():\n    assert True\n") != base  # new test
    assert normalise_source("x = 1  # comment\n") == normalise_source("y = 2\n")
    ident = oracle_identity(b, "1.1.0")
    changed = b.oracle.model_copy(update={"security_test_prefix": "test_secure_"})
    assert oracle_fingerprint(b.model_copy(update={"oracle": changed}), "1.1.0") != _fp(b)
    assert oracle_fingerprint(b, "1.0.0") != oracle_fingerprint(b, "1.1.0")  # oracle version
    assert set(ident) == {
        "normalisation_version",
        "oracle_version",
        "hidden_tests",
        "visible_tests",
        "verifier",
    }


# ------------------------------------------------------------------- I1


def test_i1_legacy_registry_covers_every_template_and_documents_the_ssrf_merge(
    registry: TaskRegistry,
) -> None:
    keys = {
        (b.oracle.version, b.oracle.family_id, b.oracle.template_id) for b in registry.all_bundles()
    }
    assert set(LEGACY_SCENARIOS) == keys
    for v in ("1.0.0", "1.1.0"):
        assert LEGACY_SCENARIOS[(v, "ssrf", "t0")].scenario_id == "ssrf/t0+t1"
        assert LEGACY_SCENARIOS[(v, "ssrf", "t1")].scenario_id == "ssrf/t0+t1"
        assert "VALIDATE_HIDDEN" in LEGACY_SCENARIOS[(v, "ssrf", "t0")].reason
    counts: dict[str, set[str]] = defaultdict(set)
    inst: dict[str, int] = defaultdict(int)
    for b in registry.list():
        sid = LEGACY_SCENARIOS[("1.1.0", b.oracle.family_id, b.oracle.template_id)].scenario_id
        counts[b.oracle.split.value].add(sid)
        inst[b.oracle.split.value] += 1
    assert {k: len(v) for k, v in counts.items()} == {
        "private_test": 3,
        "public_test": 5,
        "train": 5,
        "validation": 5,
    }
    assert dict(inst) == {"private_test": 9, "public_test": 6, "train": 18, "validation": 6}


def test_i1_template_ids_and_task_identities_are_preserved(registry: TaskRegistry) -> None:
    r = LegacyScenarioResolver(registry)
    t0 = r.resolve(real(registry, "ssrf-t0-s004"))
    t1 = r.resolve(real(registry, "ssrf-t1-s004"))
    assert t0.scenario_id == t1.scenario_id == "ssrf/t0+t1"
    assert (t0.template_id, t1.template_id) == ("t0", "t1")
    assert t0.oracle_fingerprint == t1.oracle_fingerprint and t0.split == "public_test"


# ------------------------------------------------------------------- I2 / I3


def _refuse(registry: TaskRegistry, record: Any) -> str:
    with pytest.raises(ScenarioError) as exc:
        CompositeScenarioResolver(LegacyScenarioResolver(registry)).resolve(record)
    return exc.value.code


def test_i2_unverifiable_identity_is_refused(registry: TaskRegistry) -> None:
    rec = real(registry, "sqli-t2-s000")
    rec.task_version = "2.0.0"
    assert _refuse(registry, rec) == "scenario_unverifiable"
    unknown = real(registry, "sqli-t2-s000")
    unknown.task_id = "sqli-t9-s000"
    assert _refuse(registry, unknown) == "unknown_task"
    wrong_env = real(registry, "sqli-t2-s000")
    wrong_env.environment_ref = "local-fixture:sql_injection/t1@1.1.0"
    assert _refuse(registry, wrong_env) == "task_identity_mismatch"
    wrong_family = real(registry, "sqli-t2-s000")
    wrong_family.metadata["template"] = "t0"
    assert _refuse(registry, wrong_family) == "task_identity_mismatch"
    wrong_split = real(registry, "sqli-t2-s000")
    wrong_split.metadata["split"] = "train"
    assert _refuse(registry, wrong_split) == "task_identity_mismatch"


def test_i2_pin_mismatch_is_refused(
    registry: TaskRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    from traceforge.modules.evaluation import scenarios

    key = ("1.1.0", "sql_injection", "t2")
    bad = scenarios.LegacyScenario("sql_injection/t2", "x", "0" * 64)
    monkeypatch.setitem(scenarios.LEGACY_SCENARIOS, key, bad)
    assert _refuse(registry, real(registry, "sqli-t2-s000")) == "oracle_identity_mismatch"


def test_i3_forged_metadata_scenario_cannot_change_cluster_membership(
    registry: TaskRegistry,
) -> None:
    forged = real(registry, "sqli-t2-s000")
    forged.metadata["scenario_id"] = "sql_injection/t0"
    assert _refuse(registry, forged) == "scenario_mismatch"
    ok = real(registry, "sqli-t2-s000")
    ok.metadata["scenario_id"] = "sql_injection/t2"
    assert LegacyScenarioResolver(registry).resolve(ok).scenario_id == "sql_injection/t2"


def test_i2_comparison_refuses_in_both_modes_on_identity_problems(registry: TaskRegistry) -> None:
    resolver = CompositeScenarioResolver(LegacyScenarioResolver(registry))
    good = [real(registry, "sqli-t2-s000", seed=0)]
    forged = real(registry, "sqli-t2-s000", Verdict.FAIL, seed=0)
    forged.metadata["scenario_id"] = "ssrf/t2"
    for policy in ("strict", "intersection"):
        with pytest.raises(ComparisonRefused) as exc:
            compare(
                run(good, config(seeds=[0])),
                run([forged], config(seeds=[0])),
                request(coverage_policy=policy),
                resolver,
            )
        codes = {p["code"] for p in exc.value.document["matching"]["problems"]}
        assert (
            "scenario_mismatch" in codes and exc.value.document["matching"]["status"] == "refused"
        )
