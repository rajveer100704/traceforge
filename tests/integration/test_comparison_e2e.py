"""PR 2 end to end: real (scripted demo) benchmark runs in the database, compared through the
service and the CLI; a frozen v0.2 bundle compared read-only. Scripted results are pipeline
validation, never learned-model evidence."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from traceforge.application.comparison import ComparisonRefusedError
from traceforge.contracts.comparison import ComparisonRequest
from traceforge.contracts.experiments import BenchmarkConfig, ProviderConfig
from traceforge.contracts.tasks import Split

ROOT = Path(__file__).resolve().parents[2]
G_BUNDLE = ROOT / "research" / "results" / "experiment_G" / "bundle"
G_EVAL = "782672bf-87a5-4dd3-b5e8-f0c51aa67d75"


def _run(platform: Any, policy: str, **kw: Any) -> str:
    cfg = BenchmarkConfig(
        name=f"cmp-{policy}",
        splits=(Split.PUBLIC_TEST, Split.PRIVATE_TEST),
        provider=ProviderConfig(policy=policy),
        bootstrap_samples=100,
        seeds=kw.pop("seeds", (0,)),
        **kw,
    )
    result, _ = asyncio.run(platform.evaluations.run(cfg))
    return str(result.benchmark_id)


def _req(b: str, c: str, **kw: Any) -> ComparisonRequest:
    return ComparisonRequest.model_validate(
        {"baseline": {"evaluation_id": b}, "candidate": {"evaluation_id": c}, **kw}
    )


def _strict(path: str) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text, parse_constant=lambda x: pytest.fail(f"non-finite {x}"))  # type: ignore[no-any-return]


def test_x1_database_runs_compare_end_to_end_service_and_cli(
    platform: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _run(platform, "noop")
    cand = _run(platform, "reference")
    doc, paths = platform.comparisons.compare(_req(base, cand, bootstrap_samples=500))
    m = doc["matching"]
    assert (m["status"], m["official"]) == ("matched", True)
    assert (m["n_pairs"], m["n_task_instances"], m["n_structural_scenarios"]) == (15, 15, 8)
    assert doc["evidence_source"] == "pipeline_validation"
    assert doc["learned_model_evidence"] is False
    assert doc["primary_endpoint"]["group"] == "by_split.private_test"
    assert doc["evidence_tier"] == "insufficient_scenarios"  # 3 private scenarios
    primary = doc["groups"]["by_split"]["private_test"]
    assert primary["counts"]["n_structural_scenarios"] == 3
    vs = primary["metrics"]["verified_success"]
    assert vs["difference"]["estimate"] > 0 and vs["interpretation"] == "insufficient_scenarios"
    assert doc["groups"]["by_split"]["public_test"]["counts"]["n_structural_scenarios"] == 5
    assert doc["groups"]["by_split"]["public_test"]["in_distribution_note"]
    on_disk = _strict(paths["comparison_json"])
    assert on_disk["comparison_id"] == doc["comparison_id"]
    md = Path(paths["comparison_md"]).read_text(encoding="utf-8")
    assert "8 structural scenarios (15 task instances, 15 episode pairs, 3 families)" in md
    assert "pipeline validation, not learned-model evidence" in md

    # CLI against the same database
    monkeypatch.setenv("TRACEFORGE_ENV", "test")
    monkeypatch.setenv("TRACEFORGE_DATA_DIR", str(platform.settings.data_dir))
    from traceforge.cli import app

    out = CliRunner().invoke(
        app,
        [
            "benchmark",
            "compare",
            "--baseline",
            base,
            "--candidate",
            cand,
            "--bootstrap-samples",
            "200",
        ],
    )
    assert out.exit_code == 0, out.output
    res = json.loads(out.output)
    assert res["n_structural_scenarios"] == 8 and res["evidence_tier"] == "insufficient_scenarios"
    assert Path(res["comparison_json"]).is_file()

    # X4: a final report on demo/legacy data is refused, with a persisted refusal record
    with pytest.raises(ComparisonRefusedError) as exc:
        platform.comparisons.compare(
            _req(base, cand, report_tier="final", bootstrap_samples=10_000)
        )
    codes = {p["code"] for p in exc.value.document["matching"]["problems"]}
    assert "final_tier_requirement" in codes
    assert Path(exc.value.paths["refusal_json"]).is_file()
    bad = CliRunner().invoke(
        app, ["benchmark", "compare", "--baseline", base, "--candidate", str(uuid.uuid4())]
    )
    assert bad.exit_code == 1 and "unknown_evaluation" in bad.output


def test_x2_episode_seed_mismatch_is_refused_in_both_modes(platform: Any) -> None:
    base = _run(platform, "reference", max_tasks_per_family=1, seeds=(0, 1))
    cand = _run(platform, "reference", max_tasks_per_family=1, seeds=(0,))
    with pytest.raises(ComparisonRefusedError) as exc:
        platform.comparisons.compare(_req(base, cand))
    assert "never_variable_config" in {
        p["code"] for p in exc.value.document["matching"]["problems"]
    }
    allowed = _req(base, cand, coverage_policy="intersection")
    with pytest.raises(ComparisonRefusedError):  # seeds are never variable, even diagnostically
        platform.comparisons.compare(allowed)


def test_x3_frozen_v02_bundle_is_compared_read_only_and_stays_unchanged(platform: Any) -> None:
    before = {p: p.read_bytes() for p in G_BUNDLE.iterdir()}
    ref = {
        "source": "bundle",
        "evaluation_id": G_EVAL,
        "bundle_dir": str(G_BUNDLE),
        "config_name": "exp-G-pipeline_check",
    }
    doc, _ = platform.comparisons.compare(
        ComparisonRequest.model_validate(
            {"baseline": ref, "candidate": ref, "bootstrap_samples": 200}
        )
    )
    assert doc["matching"]["status"] == "matched" and doc["matching"]["official"] is False
    assert doc["evidence_tier"] == "diagnostic"
    assert doc["protocol"]["task_versions"] == ["1.0.0"]
    assert doc["matching"]["n_structural_scenarios"] == 8
    vs = doc["groups"]["by_split"]["private_test"]["metrics"]["verified_success"]["difference"]
    assert (vs["estimate"], vs["ci_low"], vs["ci_high"]) == (0.0, 0.0, 0.0)
    assert {p: p.read_bytes() for p in G_BUNDLE.iterdir()} == before
    check = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "historical_manifest.py"), "check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
