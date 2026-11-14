"""The metric contract registry is complete, accurate and in sync with its documentation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.unit.metric_fakes import ep
from traceforge.contracts.experiments import BenchmarkConfig
from traceforge.contracts.verification import CheckStatus, Verdict
from traceforge.modules.evaluation.metric_contract import (
    METRICS,
    METRICS_SCHEMA_VERSION,
    RECORD_SCHEMAS,
    VALUE_OBJECT_FIELDS,
    audit_summary,
    lookup,
    record_problems,
    render_markdown,
)
from traceforge.modules.evaluation.metrics import summarize
from traceforge.modules.evaluation.runner import BenchmarkRunner

DOC = Path(__file__).resolve().parents[2] / "docs" / "metrics-contract.md"


def _s(runs: list[Any], **kw: Any) -> dict[str, Any]:
    return summarize(runs, bootstrap_samples=100, **kw)


def _runs() -> list[Any]:
    return [
        ep(finish={"stop": 1, "length": 1}, requests=2),
        ep("t2", Verdict.FAIL, reward=0.0, cost=None, checks={"regression": CheckStatus.FAIL}),
        ep(
            "t3",
            termination="provider_error",
            provider_failure="timeout",
            failure_source="runner_deadline",
            checks={"regression": CheckStatus.ERROR, "hidden_security": CheckStatus.SKIPPED},
        ),
    ]


def _outputs() -> list[dict[str, Any]]:
    labels = {"t1": True, "t2": False, "t3": True}
    return [
        _s([]),
        _s([ep()]),  # single task: cluster intervals carry a note
        _s(_runs()),
        _s(_runs(), ground_truth=lambda r: labels[r.task_id]),
    ]


# ------------------------------------------------------------------- recursive coverage


def test_every_emitted_field_at_every_depth_is_documented() -> None:
    for out in _outputs():
        undocumented, _ = audit_summary(out)
        assert undocumented == [], f"undocumented fields: {undocumented}"


def test_every_registered_metric_is_emitted() -> None:
    seen: set[str] = set()
    for out in _outputs():
        seen |= audit_summary(out)[1]
    assert set(METRICS) - seen == set(), (
        f"registered but never emitted: {sorted(set(METRICS) - seen)}"
    )


def test_audit_detects_an_undocumented_nested_field() -> None:
    out = _s([ep()])
    out["cost"]["surprise"] = 1.0
    out["statistics"]["new_param"] = 2
    out["verified_success"]["extra"] = 0
    out["latency"]["episode_seconds"]["p99"] = 1.0
    out["new_block"] = {"x": 1}
    undocumented, _ = audit_summary(out)
    assert set(undocumented) == {
        "cost.surprise",
        "statistics.new_param",
        "verified_success.extra",
        "latency.episode_seconds.p99",
        "new_block.x",
    }


def test_wildcards_match_dynamic_keys_only_at_their_level() -> None:
    assert (
        lookup("provider_failures.by_kind.rate_limited") is METRICS["provider_failures.by_kind.*"]
    )
    assert lookup("finish_reasons.counts.length") is METRICS["finish_reasons.counts.*"]
    assert lookup("provider_failures.by_kind.x.y") is None
    assert lookup("cost.anything") is None


def test_value_objects_have_exactly_their_documented_fields() -> None:
    for out in _outputs():
        for key, spec in METRICS.items():
            if spec.kind not in VALUE_OBJECT_FIELDS or "*" in key:
                continue
            value: Any = out
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if value is None:
                continue
            required = VALUE_OBJECT_FIELDS[spec.kind] - {"note"}
            assert required <= set(value) <= VALUE_OBJECT_FIELDS[spec.kind], key


def test_output_declares_schema_version() -> None:
    assert all(o["metrics_schema_version"] == METRICS_SCHEMA_VERSION == "2" for o in _outputs())


# ------------------------------------------------------------------- record schemas


def _aggregate(runs: list[Any]) -> dict[str, Any]:
    agg = BenchmarkRunner.aggregate(runs, BenchmarkConfig(bootstrap_samples=100))
    agg["split_policy"] = {"id": "template-holdout", "version": "1"}  # added by runner.run
    return agg


def test_aggregate_rows_and_report_block_match_their_schemas() -> None:
    agg = _aggregate(_runs())
    assert record_problems("benchmark_aggregate", agg) == []
    assert record_problems("report_block", agg["report"]) == []
    for row in agg["per_task"]:
        assert record_problems("per_episode_row", row) == []
    for row in agg["task_level"]:
        assert record_problems("task_level_row", row) == []
    assert agg["reward_threshold_sensitivity"]
    for row in agg["reward_threshold_sensitivity"]:
        assert record_problems("threshold_sensitivity_row", row) == []
    for group in ("by_split", "by_family", "by_difficulty", "by_seed"):
        for summary in agg[group].values():
            assert audit_summary(summary)[0] == [], group


def test_experiment_g_arm_metrics_match_their_schema() -> None:
    from traceforge.application.experiments import _arm_metrics

    assert record_problems("experiment_g_arm_metrics", _arm_metrics(_aggregate(_runs()))) == []


def test_record_problems_reports_missing_and_undocumented_fields() -> None:
    row = {**_aggregate([ep()])["per_task"][0], "new_col": 1}
    del row["tokens"]
    assert record_problems("per_episode_row", row) == [
        "undocumented per_episode_row.new_col",
        "missing per_episode_row.tokens",
    ]


# ---------------------------------------------- M1: documented semantics match calculations


def test_verdict_metrics_count_pass_regardless_of_authority_as_documented() -> None:
    out = _s([ep(authoritative=False), ep("t2", Verdict.PARTIAL, authoritative=False)])
    assert out["verified_success"]["successes"] == 1
    assert out["partial_success"]["successes"] == 1
    assert out["task_clustered"]["verified_success"]["estimate"] == 0.5
    cond = out["verifier_outcomes_without_provider_failure"]
    assert cond["verified_success"]["successes"] == 1
    for key in (
        "verified_success",
        "task_clustered.verified_success",
        "verifier_outcomes_without_provider_failure.verified_success",
    ):
        assert METRICS[key].numerator.startswith("PASS verdict"), key
        assert "non-authoritative" in METRICS[key].numerator, key
    assert not METRICS["verified_success"].numerator.lower().startswith("authoritative")


def test_verifier_derived_ground_truth_requires_an_authoritative_pass() -> None:
    # Rewarded non-authoritative PASS: counted as verified success, but not as ground truth.
    out = _s([ep(authoritative=False, reward=1.0)])
    assert out["verified_success"]["successes"] == 1
    assert out["false_positive_reward_rate"]["successes"] == 1
    assert "authoritative PASS" in METRICS["false_positive_reward_rate"].interpretation


def test_hidden_security_denominators_are_executed_checks() -> None:
    runs = [
        ep(checks={"hidden_security": CheckStatus.PASS}),
        ep("t2", checks={"hidden_security": CheckStatus.ERROR}),
        ep("t3", checks={"hidden_security": CheckStatus.SKIPPED}),
        ep("t4", checks={"hidden_security": None}),
    ]
    out = _s(runs)
    assert (
        out["hidden_security_pass_rate"]["successes"],
        out["hidden_security_pass_rate"]["n"],
    ) == (1, 2)
    assert out["task_clustered"]["hidden_security_pass_rate"]["n_observations"] == 2
    for key in ("hidden_security_pass_rate", "task_clustered.hidden_security_pass_rate"):
        assert "executed" in METRICS[key].denominator, key
        assert "all episodes" not in METRICS[key].denominator, key


# ------------------------------------------------- M2: regression outcomes and denominators


def test_regression_outcomes_split_all_episode_and_executed_denominators() -> None:
    statuses = [CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.ERROR, CheckStatus.INCONCLUSIVE]
    runs = [ep(f"t{i}", checks={"regression": st}) for i, st in enumerate(statuses)]
    runs += [
        ep("t8", checks={"regression": CheckStatus.SKIPPED}),
        ep("t9", checks={"regression": None}),
    ]
    out = _s(runs)
    assert out["regression_check_outcomes"] == {
        "pass": 1,
        "fail": 1,
        "error": 1,
        "inconclusive": 1,
        "skipped": 1,
        "missing": 1,
    }
    allr, ex = out["regression_failure_rate"], out["regression_failure_rate_executed"]
    assert (allr["successes"], allr["n"]) == (1, 6)  # v0.2 semantics unchanged
    assert (ex["successes"], ex["n"]) == (1, 2)  # only PASS and FAIL are usable
    cov = out["telemetry_coverage"]
    assert (cov["regression_check"]["covered"], cov["regression_check_unusable"]["covered"]) == (
        2,
        2,
    )
    assert out["task_clustered"]["regression_failure_rate"]["n_observations"] == 6
    assert "PASS or FAIL" in METRICS["regression_failure_rate_executed"].denominator
    assert METRICS["regression_failure_rate"].denominator == "all scheduled episodes"


@pytest.mark.parametrize("status", [CheckStatus.ERROR, CheckStatus.INCONCLUSIVE])
def test_unusable_regression_results_are_never_counted_as_passes(status: CheckStatus) -> None:
    out = _s([ep(checks={"regression": status})])
    ex = out["regression_failure_rate_executed"]
    assert (ex["successes"], ex["n"], ex["rate"]) == (0, 0, None)
    assert out["regression_check_outcomes"][status.value.lower()] == 1


# ------------------------------------------------------------------- generated docs


def test_proportions_declare_a_real_denominator_and_every_field_is_set() -> None:
    for spec in METRICS.values():
        if spec.kind == "proportion":
            assert spec.denominator not in ("", "n/a"), spec.key
            assert spec.interval != "none", spec.key
        for field in (
            "numerator",
            "denominator",
            "unit",
            "statistical_unit",
            "interval",
            "missing",
        ):
            assert getattr(spec, field), (spec.key, field)


def test_docs_are_generated_from_the_registry() -> None:
    assert DOC.read_text(encoding="utf-8") == render_markdown(), (
        "docs/metrics-contract.md is out of date: regenerate it with metric_contract.render_markdown()"
    )


def test_docs_tables_have_consistent_columns_and_cover_every_record() -> None:
    md = render_markdown()
    metric_section = md.split("## Metrics")[1].split("## Record schemas")[0]
    rows = [ln for ln in metric_section.splitlines() if ln.startswith("| `")]
    assert len(rows) == len(METRICS)
    for row in rows:
        cells = re.split(r"(?<!\\)\|", row.strip())[1:-1]  # unescaped pipes delimit cells
        assert len(cells) == 9, row
    for name, fields in RECORD_SCHEMAS.items():
        assert f"### `{name}`" in md
        for field in fields:
            assert f"| `{field}` |" in md, (name, field)
    for kind in VALUE_OBJECT_FIELDS:
        assert f"| {kind} |" in md
