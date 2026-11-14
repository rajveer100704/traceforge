"""v0.3 PR 1B end to end: a real (scripted demo) benchmark run produces schema-2 metrics, strict
finite JSON and a complete Markdown report. Demo results are infrastructure validation only."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from traceforge.contracts.experiments import BenchmarkConfig, ProviderConfig
from traceforge.contracts.tasks import Split
from traceforge.modules.evaluation.metric_contract import METRICS, emitted_keys


def _strict_json(path: str) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text
    return json.loads(text, parse_constant=lambda c: pytest.fail(f"non-finite JSON {c}"))  # type: ignore[no-any-return]


def test_demo_benchmark_report_is_schema_2_finite_and_complete(platform: Any) -> None:
    cfg = BenchmarkConfig(
        name="pr1b",
        splits=(Split.PUBLIC_TEST,),
        max_tasks_per_family=1,
        provider=ProviderConfig(policy="reference"),
        bootstrap_samples=200,
        seeds=(0, 1),
    )
    result, paths = asyncio.run(platform.evaluations.run(cfg))
    doc = _strict_json(paths["report_json"])
    m = doc["metrics"]
    o = m["overall"]
    assert doc["metrics_schema_version"] == "2" and doc["report_tier"] == "development"
    assert emitted_keys(o) <= set(METRICS)
    n = o["n_episodes"]
    # Scripted demo provider: explicitly free (cost 0.0, not unknown), no token usage reported.
    assert o["cost"]["total_usd"] == 0.0 and o["cost"]["lower_bound_usd"] is None
    assert o["telemetry_coverage"]["cost"]["covered"] == n
    assert o["telemetry_coverage"]["usage"]["covered"] == 0 and o["tokens"]["total"] is None
    assert o["telemetry_coverage"]["generation_telemetry"]["covered"] == n
    assert o["latency"]["request_seconds"]["n"] > 0 and o["latency"]["episode_seconds"]["n"] == n
    assert o["provider_failures"]["rate"]["successes"] == 0
    assert o["false_positive_reward_label_source"] == "verifier_derived"
    assert o["statistics"]["bootstrap_samples"] == 200
    for group in m["by_split"].values():
        assert group["metrics_schema_version"] == "2"
    md = Path(paths["report_md"]).read_text(encoding="utf-8")
    for section in (
        "## Overall",
        "## Provider failures and verifier outcomes",
        "## Cost and tokens",
        "## Latency",
        "## Telemetry coverage",
        "## Task-clustered uncertainty",
        "Development-grade intervals",
        "Reward-verifier disagreement",
        "| Task | Seed |",
    ):
        assert section in md, section
    assert result.metrics["evidence"]["learned_model_evidence"] is False


def test_experiment_g_cost_comes_from_shared_aggregation(platform: Any, tmp_path: Path) -> None:
    from traceforge.application.experiments import run_experiment

    cfg = tmp_path / "g.yaml"
    cfg.write_text(
        "benchmark: {splits: [public_test], seeds: [0]}\n"
        "pricing: {prompt_per_1k: 0.5, completion_per_1k: 1.5}\n"
        "arms:\n"
        "  pipeline_check: {provider: {kind: demo, policy: reference}}\n",
        encoding="utf-8",
    )
    out = run_experiment(platform, "G", tmp_path / "out", config_path=cfg)
    doc = _strict_json(out["files"]["json"])
    arm = doc["arms"]["pipeline_check"]
    # The scripted arm is explicitly free; its cost is the aggregated 0.0, not a token estimate.
    assert arm["inference_cost_usd"] == 0.0 and arm["inference_cost_lower_bound_usd"] is None
    assert arm["metrics"]["cost"]["total_usd"] == 0.0
    md = Path(out["files"]["markdown"]).read_text(encoding="utf-8")
    assert "Cost (USD)" in md and "$0.000000" in md


def test_experiment_g_partial_pricing_fails_before_any_arm_runs(
    platform: Any, tmp_path: Path
) -> None:
    from traceforge.application.experiments import run_experiment

    cfg = tmp_path / "g.yaml"
    cfg.write_text(
        "benchmark: {splits: [public_test], seeds: [0]}\n"
        "pricing: {prompt_per_1k: 0.5, completion_per_1k: null}\n"
        "arms:\n"
        "  pipeline_check: {provider: {kind: demo, policy: reference}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="set both"):
        run_experiment(platform, "G", tmp_path / "out", config_path=cfg)
