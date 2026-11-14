"""v0.3 PR 1B: finite JSON, report tier, Markdown reporting and Experiment G pricing."""

from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.unit.metric_fakes import ep
from traceforge.contracts.experiments import BenchmarkConfig, ProviderConfig
from traceforge.contracts.verification import Verdict
from traceforge.modules.evaluation.reports import benchmark_markdown
from traceforge.modules.evaluation.runner import BenchmarkRunner
from traceforge.modules.evaluation.serialization import dumps_finite, finite_json
from traceforge.modules.evaluation.statistics import format_rate

# ------------------------------------------------------------------------ finite JSON


def test_finite_json_replaces_non_finite_and_records_paths() -> None:
    doc = {"a": 1.0, "b": float("nan"), "c": {"d": [1.0, float("inf"), -math.inf]}, "e": "x"}
    clean, paths = finite_json(doc)
    assert clean == {"a": 1.0, "b": None, "c": {"d": [1.0, None, None]}, "e": "x"}
    assert paths == ["b", "c.d[1]", "c.d[2]"]
    text = dumps_finite(doc)
    loaded = json.loads(text)  # strict JSON: no NaN/Infinity tokens
    assert "NaN" not in text and "Infinity" not in text
    assert loaded["non_finite_fields"] == ["b", "c.d[1]", "c.d[2]"]


def test_dumps_finite_keeps_plain_documents_unchanged() -> None:
    doc = {"when": datetime(2026, 11, 14, tzinfo=UTC), "id": uuid.UUID(int=1), "n": 3, "x": 0.5}
    loaded = json.loads(dumps_finite(doc, sort_keys=True))
    assert "non_finite_fields" not in loaded
    assert (
        loaded["when"].startswith("2026-11-14")
        and loaded["id"].endswith("1")
        and loaded["x"] == 0.5
    )


def test_dumps_finite_never_writes_nan_even_without_record_key() -> None:
    text = dumps_finite([float("nan")], record_key=None)
    assert json.loads(text) == [None]


def test_format_rate() -> None:
    assert format_rate(None) == "n/a" and format_rate(0.5) == f"{0.5:.1%}"
    assert format_rate(0.25, ".0%") == f"{0.25:.0%}"


# ------------------------------------------------------------------------ report tier


def test_report_tier_validation_and_hash_neutral_default() -> None:
    base = BenchmarkConfig()
    assert base.report_tier == "development"
    final = BenchmarkConfig(report_tier="final", bootstrap_samples=10_000)
    assert final.config_hash != BenchmarkConfig(bootstrap_samples=10_000).config_hash
    with pytest.raises(ValueError, match="bootstrap_samples >= 10000"):
        BenchmarkConfig(report_tier="final")  # 2,000 default resamples
    with pytest.raises(ValueError, match="run_mode 'official'"):
        BenchmarkConfig(report_tier="final", bootstrap_samples=10_000, run_mode="diagnostic")
    with pytest.raises(ValueError):
        BenchmarkConfig(report_tier="preliminary")


def test_aggregate_records_report_tier_and_schema() -> None:
    dev = BenchmarkRunner.aggregate([ep()], BenchmarkConfig(bootstrap_samples=200))  # type: ignore[list-item]
    assert dev["metrics_schema_version"] == "2" and dev["report"]["tier"] == "development"
    assert "Development-grade" in dev["report"]["note"]
    cfg = BenchmarkConfig(report_tier="final", bootstrap_samples=10_000)
    assert BenchmarkRunner.aggregate([ep()], cfg)["report"]["tier"] == "final"  # type: ignore[list-item]


# ------------------------------------------------------------------------ Markdown


def _doc(runs: list[Any], cfg: BenchmarkConfig | None = None) -> dict[str, Any]:
    cfg = cfg or BenchmarkConfig(bootstrap_samples=200)
    metrics = BenchmarkRunner.aggregate(runs, cfg)
    metrics["split_policy"] = {"id": "template-holdout", "version": "1"}
    return {
        "config": cfg.model_dump(mode="json"),
        "benchmark_id": "b-1",
        "generated_at": "2026-11-14T00:00:00+00:00",
        "traceforge_version": "test",
        "config_hash": cfg.config_hash,
        "official": True,
        "run_mode": cfg.run_mode,
        "disclaimer": None,
        "evidence": metrics["evidence"],
        "metrics": metrics,
        "leakage": {
            "ok": True,
            "task_counts_by_split": {},
            "checks": [],
            "training_sources": [],
            "training_task_ids": [],
            "problems": [],
            "training_overlap": [],
        },
        "artifacts": {},
    }


def test_markdown_renders_empty_denominators_as_na_not_zero() -> None:
    runs = [ep(checks={"hidden_security": None, "regression": None})]
    md = benchmark_markdown(_doc(runs))  # type: ignore[arg-type]
    assert "| Hidden security pass rate | n/a (n=0) |" in md
    assert "| Regression failure rate (executed) | n/a (n=0) |" in md


def test_markdown_states_units_denominators_coverage_and_methods() -> None:
    runs = [ep(seed=0), ep("t2", Verdict.FAIL, reward=0.0, seed=1, cost=None)]
    md = benchmark_markdown(_doc(runs))  # type: ignore[arg-type]
    for needle in (
        "Numerator / denominator",
        "PASS / all scheduled episodes",
        "## Provider failures and verifier outcomes",
        "## Cost and tokens",
        "**Lower bound** on total cost (USD)",
        "## Latency",
        "| Measure | n | p50 | p90 | p95 | max |",
        "## Telemetry coverage",
        "Pooled-ratio estimand",
        "Development-grade intervals",
        "report tier: **development**",
        "independently labelled episodes: 0",
        "| Task | Seed |",
        "Reward-verifier disagreement (verifier-derived",
    ):
        assert needle in md, needle
    assert "| Total cost (USD) | not reported |" in md  # one unknown cost: total unknown


def test_markdown_lower_bound_only_when_coverage_is_partial() -> None:
    md = benchmark_markdown(_doc([ep(cost=0.01), ep("t2", cost=0.02)]))  # type: ignore[arg-type]
    assert "Lower bound" not in md and "| Total cost (USD) | $0.030000 |" in md


def test_markdown_independent_labels_wording() -> None:
    runs = [ep()]
    doc = _doc(runs)
    doc["metrics"]["overall"]["false_positive_reward_label_source"] = "independent_labels"
    md = benchmark_markdown(doc)
    assert "False-positive reward rate (independent labels)" in md
    assert "Reward-verifier disagreement" not in md.split("## Overall")[1].split("##")[0]


def test_markdown_final_tier_has_no_development_banner() -> None:
    cfg = BenchmarkConfig(report_tier="final", bootstrap_samples=10_000)
    md = benchmark_markdown(_doc([ep()], cfg))  # type: ignore[arg-type]
    assert "report tier: **final**" in md and "Development-grade intervals" not in md


# ------------------------------------------------------------------------ Experiment G pricing


def test_experiment_g_pricing_is_applied_to_model_arms_only() -> None:
    from traceforge.application.experiments import _apply_pricing

    pricing = {"prompt_per_1k": 0.5, "completion_per_1k": 1.5}
    model = {"kind": "openai_compatible", "base_url": "http://x/v1", "model_id": "m"}
    priced = _apply_pricing(model, pricing)
    assert (priced["price_per_1k_prompt"], priced["price_per_1k_completion"]) == (0.5, 1.5)
    assert ProviderConfig.model_validate(priced).pricing_known
    explicit = {**model, "price_per_1k_prompt": 0.0, "price_per_1k_completion": 0.0}
    assert _apply_pricing(explicit, pricing) == explicit  # arm's own price wins
    demo = {"kind": "demo", "policy": "reference"}
    assert _apply_pricing(demo, pricing) == demo
    assert _apply_pricing(model, {}) == model  # unknown pricing stays unknown
    assert _apply_pricing(model, {"prompt_per_1k": None, "completion_per_1k": None}) == model


@pytest.mark.parametrize(
    "pricing",
    [
        {"prompt_per_1k": 0.5},
        {"completion_per_1k": 0.5},
        {"prompt_per_1k": 0.5, "completion_per_1k": None},
    ],
)
def test_experiment_g_partial_pricing_is_rejected(pricing: dict[str, Any]) -> None:
    from traceforge.application.experiments import _apply_pricing

    with pytest.raises(ValueError, match="set both"):
        _apply_pricing({"kind": "openai_compatible"}, pricing)
