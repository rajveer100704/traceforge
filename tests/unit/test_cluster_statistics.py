from __future__ import annotations

from datetime import date

from traceforge.modules.evaluation.statistics import cluster_bootstrap, wilson_interval


def test_cluster_bootstrap_deterministic_and_reports_units() -> None:
    clusters = [[1.0, 1.0], [0.0], [1.0, 0.0, 1.0], [0.0, 0.0]]
    a = cluster_bootstrap(clusters, samples=500, seed=3)
    b = cluster_bootstrap(clusters, samples=500, seed=3)
    assert a == b
    assert a["n_clusters"] == 4 and a["n_observations"] == 8
    assert a["estimate"] == 4 / 8
    assert 0.0 <= a["ci_low"] <= a["estimate"] <= a["ci_high"] <= 1.0


def test_cluster_interval_undefined_below_two_clusters() -> None:
    one = cluster_bootstrap([[1.0, 0.0, 1.0]])
    assert one["ci_low"] is None and one["ci_high"] is None and "fewer than 2" in one["note"]
    assert cluster_bootstrap([])["estimate"] is None


def test_correlated_repeats_widen_cluster_interval_vs_wilson() -> None:
    # 10 tasks, 5 always solved and 5 never, each run with 3 identical seeds: Wilson treats the
    # 30 episodes as independent; the task-clustered bootstrap sees only 10 independent units.
    clusters = [[1.0] * 3 for _ in range(5)] + [[0.0] * 3 for _ in range(5)]
    lo, hi = wilson_interval(15, 30)
    c = cluster_bootstrap(clusters, samples=4000, seed=0)
    assert (c["ci_high"] - c["ci_low"]) > (hi - lo)
    # and it matches the width you get from the 10 independent tasks alone
    lo10, hi10 = wilson_interval(5, 10)
    assert abs((c["ci_high"] - c["ci_low"]) - (hi10 - lo10)) < 0.2


def test_trivy_gate_policy() -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "trivy_gate", Path(__file__).resolve().parents[2] / "scripts" / "trivy_gate.py"
    )
    assert spec and spec.loader
    gate = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["trivy_gate"] = gate  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(gate)

    def report(*vulns: dict[str, object]) -> dict[str, object]:
        return {"Results": [{"Target": "debian", "Vulnerabilities": list(vulns)}]}

    v_unfixed = {
        "VulnerabilityID": "CVE-1",
        "PkgName": "libx",
        "InstalledVersion": "1",
        "Severity": "HIGH",
        "Status": "affected",
    }
    v_fixed = {**v_unfixed, "VulnerabilityID": "CVE-2", "FixedVersion": "2"}
    v_medium = {**v_unfixed, "VulnerabilityID": "CVE-3", "Severity": "MEDIUM"}
    findings = gate.load_findings("img:1", report(v_unfixed, v_fixed, v_medium))
    today = date(2026, 11, 9)
    exc = {
        "id": "CVE-1",
        "images": ["img:1"],
        "packages": ["libx"],
        "owner": "maintainers",
        "reason": "not reachable",
        "reviewed": "2026-11-09",
        "expires": "2026-12-09",
        "status": "no-fix-available",
    }

    # no exceptions: both HIGH findings fail, MEDIUM is counted but not gated
    r = gate.evaluate(findings, {}, today)
    assert not r.passed and len(r.failures) == 2 and r.counts["img:1"]["MEDIUM"] == 1
    # valid exception covers the unfixed finding only; the fixable one stays actionable
    r = gate.evaluate(findings, {"exceptions": [exc]}, today)
    assert (
        not r.passed
        and r.failures == ["img:1: HIGH CVE-2 in libx 1 (fixed in 2)"]
        and r.excepted[0]["id"] == "CVE-1"
    )
    assert gate.evaluate(
        gate.load_findings("img:1", report(v_unfixed)), {"exceptions": [exc]}, today
    ).passed
    # expired, over-long, or malformed exceptions fail the gate
    assert not gate.evaluate([], {"exceptions": [{**exc, "expires": "2026-11-01"}]}, today).passed
    assert not gate.evaluate([], {"exceptions": [{**exc, "expires": "2027-06-01"}]}, today).passed
    assert not gate.evaluate([], {"exceptions": [{**exc, "owner": ""}]}, today).passed
    # a "no fix" exception stops applying once a fix exists
    fixed_now = gate.load_findings("img:1", report({**v_unfixed, "FixedVersion": "1.1"}))
    assert not gate.evaluate(fixed_now, {"exceptions": [exc]}, today).passed
    # scoped to images; unused exceptions are reported
    other = gate.load_findings("img:2", report(v_unfixed))
    r = gate.evaluate(other, {"exceptions": [exc]}, today)
    assert not r.passed and r.unused_exceptions == ["CVE-1 (img:1)"]
    assert "FAIL" in gate.summary_markdown(r)
