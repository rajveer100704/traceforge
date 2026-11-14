"""v0.3 PR 1B: metric aggregation, missingness and denominators (schema 2)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.metric_fakes import ep
from traceforge.contracts.verification import CheckStatus, Verdict
from traceforge.modules.evaluation.metrics import per_task_rows, summarize, task_level_rows
from traceforge.modules.evaluation.statistics import percentiles, proportion


def s(runs: list[Any], **kw: Any) -> dict[str, Any]:
    return summarize(runs, bootstrap_samples=200, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------------ empty and zero cases


def test_empty_run_list_is_all_null_never_zero() -> None:
    o = s([])
    for key in (
        "verified_success",
        "partial_success",
        "verifier_error_rate",
        "reward_positive_rate",
        "false_positive_reward_rate",
        "regression_failure_rate",
        "regression_failure_rate_executed",
        "hidden_security_pass_rate",
    ):
        assert o[key]["rate"] is None and o[key]["n"] == 0, key
    assert o["provider_failures"]["rate"]["rate"] is None
    assert o["tokens"]["total"] is None and o["cost"]["total_usd"] is None
    assert o["cost_per_verified_success_usd"] is None and o["cost"]["lower_bound_usd"] is None
    assert (
        o["latency"]["request_seconds"]["n"] == 0 and o["latency"]["episode_seconds"]["p50"] is None
    )
    assert all(c["fraction"] is None for c in o["telemetry_coverage"].values())
    assert o["episodes_per_task"] is None and o["n_seeds"] == 0 and o["mean_reward"] is None
    assert o["tool_calls"]["per_attempt"] is None and o["action_rejection_rate"] is None


def test_zero_successes_make_per_success_metrics_null() -> None:
    o = s([ep(verdict=Verdict.FAIL, reward=0.0), ep("t2", Verdict.FAIL, reward=0.0)])
    assert o["verified_success"]["rate"] == 0.0  # a real 0 with n=2, not "unknown"
    assert o["cost"]["total_usd"] == pytest.approx(0.02)
    assert o["cost"]["per_verified_success_usd"] is None
    assert o["cost_per_verified_success_usd"] is None
    assert o["tokens"]["per_verified_success"] is None
    assert o["tokens"]["verified_successes_per_1000_tokens"] == 0.0
    assert o["tool_calls"]["per_success"] is None and o["tool_calls_per_success"] is None


def test_zero_tool_calls() -> None:
    o = s([ep(calls=0), ep("t2", calls=0)])
    assert o["tool_calls"]["total"] == 0 and o["tool_calls"]["per_attempt"] == 0.0
    assert o["action_rejection_rate"] is None  # no actions: undefined, not 0


# ------------------------------------------------------------------------ checks and denominators


def test_hidden_security_all_skipped_is_null_with_zero_coverage() -> None:
    o = s(
        [
            ep(checks={"hidden_security": CheckStatus.SKIPPED}),
            ep("t2", checks={"hidden_security": CheckStatus.SKIPPED}),
        ]
    )
    assert o["hidden_security_pass_rate"]["rate"] is None
    assert o["telemetry_coverage"]["hidden_security_check"] == {
        "covered": 0,
        "n": 2,
        "fraction": 0.0,
    }


def test_hidden_security_denominator_is_executed_checks_only() -> None:
    o = s(
        [
            ep(checks={"hidden_security": CheckStatus.PASS}),
            ep("t2", checks={"hidden_security": CheckStatus.FAIL}),
            ep("t3", checks={"hidden_security": CheckStatus.SKIPPED}),
        ]
    )
    assert (o["hidden_security_pass_rate"]["successes"], o["hidden_security_pass_rate"]["n"]) == (
        1,
        2,
    )


def test_regression_both_denominators_and_coverage() -> None:
    runs = [
        ep(checks={"regression": CheckStatus.FAIL}),
        ep("t2", checks={"regression": CheckStatus.PASS}),
        ep("t3", checks={"regression": None}),  # check not present
        ep("t4", checks={"regression": CheckStatus.SKIPPED}),
    ]
    o = s(runs)
    assert (o["regression_failure_rate"]["successes"], o["regression_failure_rate"]["n"]) == (1, 4)
    ex = o["regression_failure_rate_executed"]
    assert (ex["successes"], ex["n"]) == (1, 2)
    assert o["telemetry_coverage"]["regression_check"]["covered"] == 2


# ------------------------------------------------------------------------ tokens and cost


def test_partial_usage_coverage_makes_token_totals_null() -> None:
    o = s([ep(usage=(100, 20)), ep("t2", usage=None), ep("t3", usage=(50, 5))])
    t = o["tokens"]
    assert t["total"] is None and t["prompt_total"] is None and t["per_attempt"] is None
    assert t["episodes_with_complete_usage"] == 2 and t["n_episodes"] == 3
    assert o["token_usage"] is None  # v0.2 block no longer sums partial counts as if complete
    assert o["telemetry_coverage"]["usage"]["covered"] == 2


def test_complete_usage_totals_and_ratios() -> None:
    o = s([ep(usage=(100, 20)), ep("t2", Verdict.FAIL, usage=(50, 30), reward=0.0)])
    t = o["tokens"]
    assert (t["prompt_total"], t["completion_total"], t["total"]) == (150, 50, 200)
    assert t["per_attempt"] == 100.0 and t["per_verified_success"] == 200.0
    assert t["verified_successes_per_1000_tokens"] == pytest.approx(5.0)
    assert o["token_usage"]["total_tokens"] == 200  # v0.2 shape kept when complete


def test_one_unknown_cost_makes_totals_null_and_publishes_lower_bound() -> None:
    o = s([ep(cost=0.01), ep("t2", cost=None), ep("t3", cost=0.03)])
    c = o["cost"]
    assert c["total_usd"] is None and c["per_attempt_usd"] is None
    assert c["per_verified_success_usd"] is None and o["cost_per_verified_success_usd"] is None
    assert c["lower_bound_usd"] == pytest.approx(0.04)
    assert c["episodes_with_known_cost"] == 2 and c["n_episodes"] == 3


def test_all_costs_known() -> None:
    o = s([ep(cost=0.01), ep("t2", Verdict.FAIL, cost=0.03, reward=0.0)])
    c = o["cost"]
    assert c["total_usd"] == pytest.approx(0.04) and c["per_attempt_usd"] == pytest.approx(0.02)
    assert c["per_verified_success_usd"] == pytest.approx(0.04)
    assert o["cost_per_verified_success_usd"] == pytest.approx(0.04)
    assert c["lower_bound_usd"] is None  # only published when coverage is partial


def test_explicitly_free_cost_is_zero_not_unknown() -> None:
    o = s([ep(cost=0.0), ep("t2", cost=0.0)])
    assert o["cost"]["total_usd"] == 0.0 and o["cost_per_verified_success_usd"] == 0.0


def test_no_known_cost_gives_no_lower_bound() -> None:
    o = s([ep(cost=None), ep("t2", cost=None)])
    assert o["cost"]["total_usd"] is None and o["cost"]["lower_bound_usd"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, "0.01", True])
def test_invalid_episode_cost_is_unknown(bad: Any) -> None:
    o = s([ep(cost=bad), ep("t2", cost=0.01)])
    assert o["cost"]["total_usd"] is None and o["cost"]["episodes_with_known_cost"] == 1


# ------------------------------------------------------------------------ provider failures


def test_provider_failures_are_separate_from_verifier_errors() -> None:
    runs = [
        ep(verdict=Verdict.PASS),
        ep(
            "t2",
            Verdict.FAIL,
            reward=0.0,
            termination="provider_error",
            provider_failure="rate_limited",
            failure_source="provider",
        ),
        ep(
            "t3",
            Verdict.FAIL,
            reward=0.0,
            termination="provider_timeout",
            provider_failure="timeout",
            failure_source="runner_deadline",
        ),
        ep("t4", Verdict.ERROR, reward=0.0),
    ]
    o = s(runs)
    assert (o["verified_success"]["successes"], o["verified_success"]["n"]) == (1, 4)  # end-to-end
    pf = o["provider_failures"]
    assert (pf["rate"]["successes"], pf["rate"]["n"]) == (2, 4)
    assert pf["timeout_rate"]["successes"] == 1
    assert pf["by_kind"] == {"rate_limited": 1, "timeout": 1}
    assert pf["by_source"] == {"provider": 1, "runner_deadline": 1}
    assert o["verifier_error_rate"]["successes"] == 1  # only the real verifier ERROR
    vo = o["verifier_outcomes_without_provider_failure"]
    assert vo["n"] == 2 and vo["verified_success"]["successes"] == 1
    assert vo["verifier_error_rate"]["successes"] == 1
    assert o["task_clustered"]["provider_failure_rate"]["estimate"] == 0.5


def test_pre_v03_provider_failure_from_termination_reason() -> None:
    o = s(
        [
            ep(legacy=True, termination="provider_timeout"),
            ep("t2", legacy=True, termination="provider_error"),
        ]
    )
    assert o["provider_failures"]["by_kind"] == {"timeout": 1, "unknown": 1}
    assert o["provider_failures"]["by_source"] == {"unknown": 2}


# ------------------------------------------------------------------------ latency, finish reasons


def test_latency_percentiles_pool_requests() -> None:
    runs = [
        ep(requests=2, request_seconds=[1.0, 3.0], duration=10.0),
        ep("t2", requests=1, request_seconds=[2.0], duration=20.0),
    ]
    lat = s(runs)["latency"]
    rq = lat["request_seconds"]
    assert rq["n"] == 3 and rq["p50"] == 2.0 and rq["max"] == 3.0
    assert lat["episode_seconds"]["n"] == 2 and lat["episode_seconds"]["max"] == 20.0


def test_request_latency_coverage_counts_incomplete_episodes() -> None:
    o = s([ep(requests=2, request_seconds=[1.0]), ep("t2", requests=1, request_seconds=[2.0])])
    assert o["telemetry_coverage"]["request_latency"]["covered"] == 1


def test_percentile_rules_for_small_samples() -> None:
    assert percentiles([])["p50"] is None and percentiles([])["n"] == 0
    one = percentiles([4.0])
    assert one["p50"] == one["p90"] == one["p95"] == one["max"] == 4.0
    two = percentiles([1.0, 3.0])
    assert (two["p50"], two["p90"], two["p95"], two["max"]) == (2.0, 2.8, 2.9, 3.0)
    ten = percentiles([float(i) for i in range(1, 11)])
    assert (ten["p50"], ten["p90"], ten["p95"], ten["max"]) == (5.5, 9.1, 9.55, 10.0)
    assert percentiles([1.0, float("nan"), float("inf"), 3.0])["n"] == 2  # non-finite dropped
    # Regression (found by property test): interpolation must not overshoot the sample range.
    x = 93207.75543365395
    same = percentiles([x, x])
    assert same["p50"] == same["p90"] == same["p95"] == same["max"] == x


def test_finish_reasons_and_truncation() -> None:
    o = s([ep(finish={"stop": 1, "length": 2}), ep("t2", finish={"tool_calls": 3, "length": 1})])
    assert o["finish_reasons"]["counts"] == {"length": 3, "stop": 1, "tool_calls": 3}
    assert o["finish_reasons"]["truncated_length"] == 3


# ------------------------------------------------------------------------ labels, seeds, legacy


def test_label_source_and_reward_precision_recall() -> None:
    runs = [
        ep("t1", reward=1.0),  # rewarded, label: truly successful
        ep("t2", reward=1.0),  # rewarded, label: not successful (false positive)
        ep("t3", Verdict.FAIL, reward=0.0),  # not rewarded, label: truly successful (missed)
    ]
    labels = {"t1": True, "t2": False, "t3": True}
    o = s(runs, ground_truth=lambda r: labels[r.task_id])
    assert o["false_positive_reward_label_source"] == "independent_labels" and o["labelled_n"] == 3
    assert (o["reward_precision"]["successes"], o["reward_precision"]["n"]) == (1, 2)
    assert (o["reward_recall"]["successes"], o["reward_recall"]["n"]) == (1, 2)
    assert o["false_positive_reward_rate"]["successes"] == 1
    mixed = s(runs, ground_truth=lambda r: labels[r.task_id] if r.task_id != "t3" else None)
    assert mixed["false_positive_reward_label_source"] == "mixed" and mixed["labelled_n"] == 2
    none = s(runs)
    assert none["false_positive_reward_label_source"] == "verifier_derived"
    assert none["reward_precision"] is None and none["labelled_n"] == 0


def test_n_seeds_ignores_missing_seed() -> None:
    assert s([ep(seed=None), ep("t2", seed=None)])["n_seeds"] == 0
    assert s([ep(seed=0), ep("t2", seed=1), ep("t3", seed=None)])["n_seeds"] == 2


def test_pre_v03_records_have_zero_coverage_and_no_invented_tokens() -> None:
    o = s([ep(legacy=True, usage=(500, 0)), ep("t2", legacy=True, usage=(300, 0))])
    cov = o["telemetry_coverage"]
    assert cov["generation_telemetry"]["covered"] == 0 and cov["usage"]["covered"] == 0
    assert o["token_usage"] is None and o["tokens"]["total"] is None
    assert o["cost"]["total_usd"] is None and o["cost"]["lower_bound_usd"] is None


def test_v02_keys_unchanged_for_non_empty_inputs() -> None:
    runs = [ep(), ep("t2", Verdict.FAIL, reward=0.0), ep("t3", Verdict.PARTIAL, reward=0.4)]
    o = s(runs)
    assert o["verified_success"] == proportion(1, 3)
    assert o["partial_success"] == proportion(1, 3)
    assert o["n"] == o["n_episodes"] == 3 and o["n_tasks"] == 3
    assert o["mean_reward"] == pytest.approx(1.4 / 3)
    assert o["tool_calls_per_attempt"] == 2.0 and o["tool_calls_per_success"] == 2.0


def test_per_episode_and_task_rows_carry_new_fields() -> None:
    runs = [
        ep(),
        ep("t1", Verdict.FAIL, seed=1, termination="provider_error", provider_failure="other"),
    ]
    rows = per_task_rows(runs)  # type: ignore[arg-type]
    assert rows[1]["provider_failure"] == "other" and rows[1]["seed"] == 1
    assert rows[0]["cost_usd"] == 0.01 and rows[0]["tokens"] == 120
    assert task_level_rows(runs)[0]["provider_failures"] == 1  # type: ignore[arg-type]
