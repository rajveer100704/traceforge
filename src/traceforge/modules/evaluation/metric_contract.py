"""Versioned metric contract: what every benchmark metric means.

Every field that ``summarize()`` emits, at any nesting depth, is registered under its dotted
path (``*`` matches a dynamic key such as a failure kind). Typed value objects (Wilson
proportions, task-cluster intervals, percentile summaries, coverage counts) have documented
field sets that are checked exactly. Record schemas document the other metric-bearing outputs
(per-episode rows, task rows, threshold-sensitivity rows, the benchmark aggregate and report
block, Experiment G arm metrics, the benchmark document). ``docs/metrics-contract.md`` is
generated from this module (``render_markdown``); tests fail if any emitted field is
undocumented, a registered field is never emitted, or the document drifts.

General rules (schema 2):
- An empty denominator yields ``null`` (never 0).
- Unknown telemetry is excluded and reported as coverage; totals are ``null`` unless coverage
  is complete (a cost lower bound is published under an explicitly named key).
- Verdict counts use the verdict produced by the run's verifier profile. The default
  ``v3_full`` profile is authoritative; the weaker research profiles (R1/R2) are not, and their
  verdicts are counted the same way, so always read results together with ``verifier_profile``.
- Episode-level Wilson intervals are descriptive; population claims use the task-clustered
  bootstrap, whose statistical unit is the independent task (seeds are not independent tasks).
- Verifier-derived reward disagreement is never presented as an independently labelled
  false-positive rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

METRICS_SCHEMA_VERSION = "2"

# Statistical units and interval methods.
EPISODE = "episode"
TASK = "task_cluster"
REQUEST = "request"
LABELLED = "labelled_episode"
WILSON = "wilson (episode-level, descriptive)"
CLUSTER = "task-cluster percentile bootstrap (pooled ratio)"
EPISODE_BOOT = "episode-level percentile bootstrap (descriptive)"
NONE = "none"
# Missing-data rules.
NOT_APPLICABLE = "not_applicable"
EXCLUDED = "excluded_with_coverage"
NULL_IF_MISSING = "null_if_any_missing"
ALL = "all scheduled episodes"
SUCCESS_VERDICT = (
    "PASS verdict (authoritative under the default v3_full verifier profile; counted the same "
    "way under non-authoritative research profiles)"
)
PARTIAL_VERDICT_TEXT = "PARTIAL verdict (same verifier-profile caveat as PASS)"

# Typed value objects: a registered path of one of these kinds holds an object with exactly
# these fields (or null). ``note`` appears on a cluster interval only when it is undefined.
VALUE_OBJECT_FIELDS: dict[str, frozenset[str]] = {
    "proportion": frozenset(
        {"successes", "n", "rate", "ci_low", "ci_high", "confidence", "method"}
    ),
    "cluster_interval": frozenset(
        {
            "estimate",
            "ci_low",
            "ci_high",
            "n_clusters",
            "n_observations",
            "confidence",
            "samples",
            "method",
            "note",
        }
    ),
    "percentiles": frozenset({"n", "p50", "p90", "p95", "max", "method"}),
    "coverage": frozenset({"covered", "n", "fraction"}),
}
VALUE_OBJECT_DESCRIPTIONS = {
    "proportion": "Wilson proportion: successes / n, rate (null when n = 0), ci_low, ci_high, "
    "confidence, method.",
    "cluster_interval": "Task-cluster bootstrap: estimate (pooled ratio), ci_low/ci_high (null "
    "with fewer than 2 tasks, then `note` explains), n_clusters, n_observations, confidence, "
    "samples, method.",
    "percentiles": "Percentile summary: n, p50, p90, p95, max (all null when n = 0), method.",
    "coverage": "Coverage: covered, n, fraction (null when n = 0).",
}


@dataclass(frozen=True)
class MetricSpec:
    key: str  # dotted path in summarize() output; "*" matches any key at that level
    kind: str  # proportion | cluster_interval | percentiles | coverage | ratio | mean | total |
    # count | label | info | group
    numerator: str
    denominator: str
    unit: str
    statistical_unit: str
    interval: str
    missing: str
    interpretation: str
    secondary: bool = False


def _spec(
    key: str,
    kind: str,
    numerator: str,
    denominator: str,
    unit: str,
    interpretation: str,
    *,
    stat: str = EPISODE,
    interval: str = NONE,
    missing: str = NOT_APPLICABLE,
    secondary: bool = False,
) -> MetricSpec:
    return MetricSpec(
        key, kind, numerator, denominator, unit, stat, interval, missing, interpretation, secondary
    )


def _p(
    key: str,
    numerator: str,
    denominator: str,
    interpretation: str,
    *,
    stat: str = EPISODE,
    missing: str = NOT_APPLICABLE,
) -> MetricSpec:
    return _spec(
        key,
        "proportion",
        numerator,
        denominator,
        "fraction",
        interpretation,
        stat=stat,
        interval=WILSON,
        missing=missing,
    )


def _group(key: str, interpretation: str) -> MetricSpec:
    return _spec(key, "group", "n/a", "n/a", "n/a", interpretation, stat=NONE)


def _count(key: str, numerator: str, interpretation: str, unit: str = "episodes") -> MetricSpec:
    return _spec(key, "count", numerator, "n/a", unit, interpretation)


def _info(key: str, interpretation: str, unit: str = "n/a") -> MetricSpec:
    return _spec(key, "info", "n/a", "n/a", unit, interpretation, stat=NONE)


def _cluster(key: str, numerator: str, denominator: str, missing: str) -> MetricSpec:
    return _spec(
        key,
        "cluster_interval",
        numerator,
        denominator,
        "fraction or reward",
        "Interval width reflects independent tasks, not episodes.",
        stat=TASK,
        interval=CLUSTER,
        missing=missing,
    )


def _cov(key: str, numerator: str) -> MetricSpec:
    return _spec(
        f"telemetry_coverage.{key}",
        "coverage",
        numerator,
        ALL,
        "episodes",
        "covered, n, fraction (null with no episodes).",
    )


_SPECS: tuple[MetricSpec, ...] = (
    # ---- sample sizes and identity -------------------------------------------------------
    _info("metrics_schema_version", "Metric contract version of this output.", "version"),
    _count("n", "episodes", "Scheduled episodes (alias of n_episodes)."),
    _count("n_episodes", "episodes", "Scheduled episodes, including provider failures."),
    _spec(
        "n_tasks",
        "count",
        "distinct task ids",
        "n/a",
        "tasks",
        "Independent task clusters: the effective sample size for population claims.",
        stat=TASK,
    ),
    _group("episodes_per_task", "Min/max episodes per task; null when there are no episodes."),
    _count(
        "episodes_per_task.min",
        "fewest episodes of any task",
        "Unequal counts change the pooled-ratio weights.",
    ),
    _count(
        "episodes_per_task.max",
        "most episodes of any task",
        "Unequal counts change the pooled-ratio weights.",
    ),
    _count(
        "n_seeds", "distinct non-null seeds", "Repeated seeds are not independent tasks.", "seeds"
    ),
    _info("reward_threshold", "Reward at or above which an episode counts as rewarded.", "reward"),
    _count(
        "labelled_n",
        "episodes with an independent ground-truth label",
        "0 when no labels were supplied.",
    ),
    # ---- statistics block ----------------------------------------------------------------
    _group("statistics", "Methods and parameters used for the intervals in this output."),
    _info("statistics.confidence", "Confidence level of every interval.", "probability"),
    _info(
        "statistics.bootstrap_samples",
        "Bootstrap resamples (2,000 development default; >= 10,000 for a final report).",
        "resamples",
    ),
    _info("statistics.bootstrap_seed", "Seed of every bootstrap in this output.", "seed"),
    _info("statistics.proportion_interval", "Interval method for proportions."),
    _info("statistics.task_cluster_interval", "Interval method for task-clustered metrics."),
    _info(
        "statistics.task_cluster_estimand", "Pooled ratio: tasks weighted by their episode count."
    ),
    _info("statistics.mean_reward_interval", "Interval method for mean_reward_ci."),
    _info("statistics.percentile_method", "Percentile rule for n = 0, 1 and >= 2."),
    # ---- outcomes ------------------------------------------------------------------------
    _p(
        "verified_success",
        SUCCESS_VERDICT,
        ALL,
        "End-to-end verified success; provider failures count as non-successes.",
    ),
    _p("partial_success", PARTIAL_VERDICT_TEXT, ALL, "Not counted as success."),
    _p(
        "verifier_error_rate",
        "verdict ERROR or INCONCLUSIVE",
        ALL,
        "Verifier/infrastructure errors during verification; provider failures are reported "
        "separately.",
    ),
    _p(
        "reward_positive_rate",
        "reward >= threshold",
        ALL,
        "Share of episodes the reward policy rewarded.",
    ),
    _p(
        "false_positive_reward_rate",
        "rewarded and not truly successful",
        ALL,
        "Truly successful = the independent label when one is supplied, otherwise an "
        "authoritative PASS (a non-authoritative PASS is not ground truth). With only "
        "verifier-derived labels this is reward-verifier disagreement, not an independently "
        "labelled false-positive rate.",
    ),
    _spec(
        "false_positive_reward_label_source",
        "label",
        "n/a",
        "n/a",
        "label",
        "independent_labels | mixed | verifier_derived.",
        stat=NONE,
    ),
    _p(
        "reward_precision",
        "rewarded and truly successful (labelled)",
        "rewarded labelled episodes",
        "Null without independent labels.",
        stat=LABELLED,
        missing=EXCLUDED,
    ),
    _p(
        "reward_recall",
        "truly successful and rewarded (labelled)",
        "truly successful labelled episodes",
        "Null without independent labels.",
        stat=LABELLED,
        missing=EXCLUDED,
    ),
    _spec(
        "verifier_precision",
        "ratio",
        "PASS verdict and truly successful (labelled)",
        "PASS labelled episodes",
        "fraction",
        "Null without labels or with an empty denominator.",
        stat=LABELLED,
        missing=EXCLUDED,
    ),
    _spec(
        "verifier_recall",
        "ratio",
        "PASS verdict and truly successful (labelled)",
        "truly successful labelled episodes",
        "fraction",
        "Null without labels or with an empty denominator.",
        stat=LABELLED,
        missing=EXCLUDED,
    ),
    _p(
        "hidden_security_pass_rate",
        "hidden-security check PASS",
        "episodes where the check executed (not SKIPPED, present)",
        "ERROR and INCONCLUSIVE checks count as executed non-passes (conservative, v0.2). Coverage"
        " in telemetry_coverage.hidden_security_check.",
        missing=EXCLUDED,
    ),
    _p(
        "regression_failure_rate",
        "regression check FAIL",
        ALL,
        "v0.2 denominator: episodes without a FAIL (pass, error, inconclusive, skipped or missing "
        "check) count as not failed. See regression_failure_rate_executed.",
        missing=EXCLUDED,
    ),
    _p(
        "regression_failure_rate_executed",
        "regression check FAIL",
        "episodes with a usable regression result (PASS or FAIL)",
        "ERROR/INCONCLUSIVE checks ran without a usable result: they are excluded, never counted "
        "as passes, and reported in regression_check_outcomes and "
        "telemetry_coverage.regression_check_unusable.",
        missing=EXCLUDED,
    ),
    _group(
        "regression_check_outcomes", "Regression check result counts over all scheduled episodes."
    ),
    *(
        _count(f"regression_check_outcomes.{k}", f"episodes whose regression check is {k}", d)
        for k, d in (
            ("pass", "Usable result."),
            ("fail", "Usable result; numerator of both regression failure rates."),
            ("error", "Ran without a usable result."),
            ("inconclusive", "Ran without a usable result."),
            ("skipped", "Not executed."),
            ("missing", "No regression check recorded."),
        )
    ),
    _spec("mean_reward", "mean", "sum of final rewards", ALL, "reward", "Null with no episodes."),
    _spec(
        "mean_reward_ci",
        "info",
        "n/a",
        "n/a",
        "reward",
        "[low, high]: episode-level (i.i.d.) bootstrap of mean reward; see "
        "task_clustered.mean_reward for the population interval.",
        interval=EPISODE_BOOT,
    ),
    # ---- provider failures vs verifier outcomes ----------------------------------------
    _group(
        "provider_failures", "Model-access/infrastructure failures, distinct from verifier errors."
    ),
    _p(
        "provider_failures.rate",
        "episodes ending in a provider failure (provider_timeout / provider_error)",
        ALL,
        "Their cost is unknown (a failed call may have been billed), so any provider failure makes"
        " full cost/token totals unavailable.",
    ),
    _p(
        "provider_failures.timeout_rate",
        "provider failures of kind timeout",
        ALL,
        "Runner deadline and provider-raised timeouts (see by_source).",
    ),
    _group("provider_failures.by_kind", "Failure counts per kind."),
    _count(
        "provider_failures.by_kind.*",
        "provider failures of this kind",
        "timeout | rate_limited | server_error | http_error | transport_error | malformed_response"
        " | other | unknown (pre-v0.3 records).",
    ),
    _group("provider_failures.by_source", "Failure counts per source."),
    _count(
        "provider_failures.by_source.*",
        "provider failures from this source",
        "runner_deadline | provider | unknown.",
    ),
    _group(
        "verifier_outcomes_without_provider_failure", "Conditional, descriptive verifier outcomes."
    ),
    _count(
        "verifier_outcomes_without_provider_failure.n",
        "episodes without a provider failure",
        "Denominator of the conditional verifier outcomes.",
    ),
    _p(
        "verifier_outcomes_without_provider_failure.verified_success",
        SUCCESS_VERDICT,
        "episodes without a provider failure",
        "Conditional, descriptive; the end-to-end rate is verified_success.",
    ),
    _p(
        "verifier_outcomes_without_provider_failure.partial_success",
        PARTIAL_VERDICT_TEXT,
        "episodes without a provider failure",
        "Conditional, descriptive.",
    ),
    _p(
        "verifier_outcomes_without_provider_failure.verifier_error_rate",
        "verdict ERROR or INCONCLUSIVE",
        "episodes without a provider failure",
        "Conditional, descriptive.",
    ),
    # ---- task-clustered intervals --------------------------------------------------------
    _group("task_clustered", "Task-cluster bootstrap intervals (unit: independent task)."),
    _cluster(
        "task_clustered.verified_success",
        SUCCESS_VERDICT,
        "all episodes of the resampled tasks",
        NOT_APPLICABLE,
    ),
    _cluster(
        "task_clustered.false_positive_reward_rate",
        "rewarded and not truly successful",
        "all episodes of the resampled tasks",
        NOT_APPLICABLE,
    ),
    _cluster(
        "task_clustered.hidden_security_pass_rate",
        "hidden-security check PASS",
        "executed hidden-security checks of the resampled tasks",
        EXCLUDED,
    ),
    _cluster(
        "task_clustered.regression_failure_rate",
        "regression check FAIL",
        "all episodes of the resampled tasks (v0.2 denominator)",
        NOT_APPLICABLE,
    ),
    _cluster(
        "task_clustered.mean_reward",
        "final reward",
        "all episodes of the resampled tasks",
        NOT_APPLICABLE,
    ),
    _cluster(
        "task_clustered.provider_failure_rate",
        "provider failure",
        "all episodes of the resampled tasks",
        NOT_APPLICABLE,
    ),
    # ---- tool calls ------------------------------------------------------------------------
    _spec(
        "tool_calls_per_attempt",
        "mean",
        "tool calls",
        ALL,
        "calls",
        "v0.2 key; equals tool_calls.per_attempt.",
    ),
    _spec(
        "tool_calls_per_success",
        "ratio",
        "tool calls of PASS episodes",
        "PASS episodes",
        "calls",
        "v0.2 key; null with no successes; equals tool_calls.per_success.",
    ),
    _group("tool_calls", "Tool-call efficiency."),
    _spec("tool_calls.total", "total", "tool calls", "n/a", "calls", "All recorded tool calls."),
    _spec("tool_calls.per_attempt", "ratio", "tool calls", ALL, "calls", "Null with no episodes."),
    _spec(
        "tool_calls.per_success",
        "ratio",
        "tool calls of PASS episodes",
        "PASS episodes",
        "calls",
        "Null with no successes.",
    ),
    _spec(
        "action_rejection_rate",
        "ratio",
        "rejected tool calls",
        "tool calls",
        "fraction",
        "Null with no tool calls.",
    ),
    # ---- tokens ----------------------------------------------------------------------------
    _group(
        "token_usage",
        "v0.2 token block: null unless every episode reported complete usage and the total is > 0 "
        "(so null for records before v0.3 telemetry). See tokens.*.",
    ),
    _spec(
        "token_usage.prompt_tokens",
        "total",
        "prompt tokens",
        "n/a",
        "tokens",
        "As tokens.prompt_total.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "token_usage.completion_tokens",
        "total",
        "completion tokens",
        "n/a",
        "tokens",
        "As tokens.completion_total.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "token_usage.total_tokens",
        "total",
        "prompt + completion tokens",
        "n/a",
        "tokens",
        "As tokens.total.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "token_usage.tokens_per_episode",
        "ratio",
        "total tokens",
        ALL,
        "tokens",
        "As tokens.per_attempt.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "token_usage.tokens_per_verified_success",
        "ratio",
        "total tokens",
        "PASS episodes",
        "tokens",
        "As tokens.per_verified_success.",
        missing=NULL_IF_MISSING,
    ),
    _group(
        "tokens",
        "Token usage with coverage. Totals need every episode; any episode without complete usage "
        "(including provider failures) makes them null.",
    ),
    _spec(
        "tokens.prompt_total",
        "total",
        "prompt tokens",
        "n/a",
        "tokens",
        "Null unless every episode reported complete usage.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "tokens.completion_total",
        "total",
        "completion tokens",
        "n/a",
        "tokens",
        "Null unless every episode reported complete usage.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "tokens.total",
        "total",
        "prompt + completion tokens",
        "n/a",
        "tokens",
        "Null unless every episode reported complete usage.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "tokens.per_attempt",
        "ratio",
        "tokens.total",
        ALL,
        "tokens",
        "Null when tokens.total is null or there are no episodes.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "tokens.per_verified_success",
        "ratio",
        "tokens.total",
        "PASS episodes",
        "tokens",
        "Null when tokens.total is null or there are no successes.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "tokens.verified_successes_per_1000_tokens",
        "ratio",
        "PASS episodes x 1000",
        "tokens.total",
        "successes per 1K tokens",
        "Secondary efficiency measure.",
        missing=NULL_IF_MISSING,
        secondary=True,
    ),
    _count(
        "tokens.episodes_with_complete_usage",
        "episodes whose every request reported usage",
        "Coverage numerator.",
    ),
    _count("tokens.n_episodes", "episodes", "Coverage denominator."),
    # ---- cost ------------------------------------------------------------------------------
    _spec(
        "cost_per_verified_success_usd",
        "ratio",
        "cost.total_usd",
        "PASS episodes",
        "usd",
        "v0.2 key; equals cost.per_verified_success_usd. Never 0 for unknown cost.",
        missing=NULL_IF_MISSING,
    ),
    _group(
        "cost",
        "Cost with coverage. Any episode with unknown cost (including provider failures) makes the"
        " total unavailable; the known costs then form a lower bound.",
    ),
    _spec(
        "cost.total_usd",
        "total",
        "sum of episode costs",
        "n/a",
        "usd",
        "Null unless cost.status is complete. 0.0 only for explicitly free providers.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "cost.per_attempt_usd",
        "ratio",
        "cost.total_usd",
        ALL,
        "usd",
        "Null if cost.total_usd is null or there are no episodes.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "cost.per_verified_success_usd",
        "ratio",
        "cost.total_usd",
        "PASS episodes",
        "usd",
        "Null if cost.total_usd is null or there are no successes.",
        missing=NULL_IF_MISSING,
    ),
    _spec(
        "cost.lower_bound_usd",
        "total",
        "sum of known episode costs",
        "n/a",
        "usd",
        "Only when cost.status is partial: a LOWER BOUND on total spend, never the total.",
        missing=EXCLUDED,
    ),
    _spec(
        "cost.status",
        "label",
        "n/a",
        "n/a",
        "label",
        "complete | partial (lower bound published) | unknown (no known cost) | overflow (finite "
        "costs whose sum is not representable; no total or bound) | no_episodes.",
        stat=NONE,
    ),
    _count(
        "cost.episodes_with_known_cost",
        "episodes with a known cost",
        "Coverage numerator (unchanged by overflow).",
    ),
    _count("cost.n_episodes", "episodes", "Coverage denominator."),
    # ---- latency ---------------------------------------------------------------------------
    _group("latency", "Latency percentile summaries."),
    _spec(
        "latency.request_seconds",
        "percentiles",
        "per-request generation seconds (incl. retries)",
        "requests with a recorded duration",
        "seconds",
        "Pooled over requests.",
        stat=REQUEST,
        missing=EXCLUDED,
    ),
    _spec(
        "latency.episode_seconds",
        "percentiles",
        "episode wall time (rollout duration_seconds)",
        "episodes",
        "seconds",
        "One value per episode.",
    ),
    _spec(
        "latency.generation_seconds_per_episode",
        "percentiles",
        "per-episode generation seconds",
        "episodes that recorded it",
        "seconds",
        "One value per episode.",
        missing=EXCLUDED,
    ),
    _spec(
        "mean_duration_seconds",
        "mean",
        "episode wall time",
        ALL,
        "seconds",
        "v0.2 key; null with no episodes.",
    ),
    _spec(
        "mean_generation_seconds",
        "mean",
        "per-episode generation seconds",
        "episodes that recorded it",
        "seconds",
        "v0.2 key; see latency.* for percentiles and coverage.",
        missing=EXCLUDED,
    ),
    _spec(
        "environment_compute_seconds",
        "total",
        "sum of verification check durations",
        "n/a",
        "seconds",
        "Verifier compute time.",
    ),
    _group("finish_reasons", "Model finish reasons over successful generation requests."),
    _group("finish_reasons.counts", "Counts per finish reason."),
    _count(
        "finish_reasons.counts.*",
        "successful requests with this finish reason",
        "Includes not_reported.",
        "requests",
    ),
    _count(
        "finish_reasons.truncated_length",
        "successful requests with finish_reason 'length'",
        "Truncated generations.",
        "requests",
    ),
    # ---- coverage --------------------------------------------------------------------------
    _group("telemetry_coverage", "Coverage of each measurement over all scheduled episodes."),
    _cov("generation_telemetry", "episodes recorded with v0.3 generation telemetry"),
    _cov("usage", "episodes whose every request reported usage"),
    _cov("cost", "episodes with a known cost"),
    _cov("request_latency", "episodes with a duration for every request"),
    _cov("attempts", "episodes with known attempt counts for every request"),
    _cov("hidden_security_check", "episodes where the hidden-security check executed"),
    _cov("regression_check", "episodes with a usable (PASS or FAIL) regression result"),
    _cov(
        "regression_check_unusable",
        "episodes whose regression check ran but was ERROR or INCONCLUSIVE",
    ),
)

METRICS: dict[str, MetricSpec] = {s.key: s for s in _SPECS}

# Other metric-bearing outputs: field -> meaning. Each emitted record must have exactly these
# fields (``non_finite_fields`` is optional on documents).
RECORD_SCHEMAS: dict[str, dict[str, str]] = {
    "benchmark_aggregate": {
        "overall": "summarize() over all episodes (see Metrics).",
        "by_split": "summarize() per split.",
        "by_family": "summarize() per task family.",
        "by_difficulty": "summarize() per difficulty.",
        "by_seed": "summarize() per seed (descriptive; seeds are not independent tasks).",
        "per_task": "List of per-episode rows (record per_episode_row).",
        "task_level": "List of per-task rows (record task_level_row).",
        "reward_threshold_sensitivity": "List of threshold rows (record threshold_sensitivity_row).",
        "statistical_units": "Text: which units each interval treats as independent.",
        "evidence": "Evidence label: scripted pipeline validation vs model evaluation, n_tasks, seeds.",
        "metrics_schema_version": "Metric contract version.",
        "report": "Report tier block (record report_block).",
        "split_policy": "Task split policy id and version.",
    },
    "report_block": {
        "tier": "development | final.",
        "bootstrap_samples": "Resamples used for every bootstrap interval.",
        "final_min_bootstrap_samples": "Minimum resamples a final report requires (10,000).",
        "note": "Human-readable statement of the tier and its requirements.",
    },
    "per_episode_row": {
        "task_id": "Task.",
        "family": "Task family.",
        "split": "Split.",
        "difficulty": "Difficulty.",
        "policy": "Scripted demo policy (null for model providers).",
        "seed": "Seed (repeated seeds are not independent tasks).",
        "verdict": "Verifier verdict.",
        "reward": "Final reward.",
        "tool_calls": "Tool calls.",
        "rejected_actions": "Rejected tool calls.",
        "failure_reasons": "Verifier failure reasons.",
        "rollout_id": "Rollout id.",
        "verification_id": "Verification id.",
        "termination_reason": "Why the episode ended (provider_timeout/provider_error = provider failure).",
        "provider_failure": "Provider failure kind or null.",
        "duration_seconds": "Episode wall time.",
        "cost_usd": "Episode cost; null when unknown.",
        "tokens": "Episode tokens; null unless every request reported usage.",
    },
    "task_level_row": {
        "task_id": "Task.",
        "family": "Task family.",
        "template": "Task template.",
        "split": "Split.",
        "difficulty": "Difficulty.",
        "n_episodes": "Episodes of this task.",
        "seeds": "Seeds run.",
        "verified_success_rate": "PASS verdicts / episodes of this task.",
        "mean_reward": "Mean final reward of this task.",
        "verdicts": "Verdict counts.",
        "outcome_consistent_across_episodes": "True if every episode got the same verdict.",
        "provider_failures": "Episodes of this task that ended in a provider failure.",
    },
    "threshold_sensitivity_row": {
        "threshold": "Reward threshold.",
        "reward_positive_rate": "Proportion: reward >= threshold / all episodes.",
        "false_positive_reward_rate": "Proportion: rewarded and not truly successful / all episodes.",
        "false_positive_reward_rate_task_clustered": "Task-cluster interval of the same.",
        "false_positive_reward_label_source": "independent_labels | mixed | verifier_derived.",
    },
    "experiment_g_arm_metrics": {
        "n_tasks": "Independent tasks.",
        "n_episodes": "Episodes.",
        "verified_success": "Proportion (see verified_success).",
        "verified_success_task_clustered": "Task-cluster interval.",
        "hidden_security_pass_rate": "Proportion over executed checks.",
        "hidden_security_pass_rate_task_clustered": "Task-cluster interval.",
        "regression_failure_rate": "Proportion over all episodes.",
        "regression_failure_rate_task_clustered": "Task-cluster interval.",
        "false_positive_reward_rate": "Proportion (see label source).",
        "false_positive_reward_rate_task_clustered": "Task-cluster interval.",
        "tool_calls_per_verified_success": "Tool calls / PASS episodes.",
        "mean_episode_seconds": "Mean episode wall time.",
        "mean_generation_seconds": "Mean per-episode generation seconds.",
        "token_usage": "v0.2 token block.",
        "tokens": "Token block (see tokens.*).",
        "cost": "Cost block (see cost.*).",
        "cost_per_verified_success_usd": "Cost / PASS episodes or null.",
        "latency": "Latency block (see latency.*).",
        "provider_failure_rate": "Proportion of provider failures.",
        "false_positive_reward_label_source": "Label source.",
        "telemetry_coverage": "Coverage block.",
        "by_family": "Verified success per family.",
        "by_difficulty": "Verified success per difficulty.",
        "by_seed": "Verified-success rate per seed.",
        "evidence": "Evidence label.",
    },
    "benchmark_document": {
        "report_type": "Always 'benchmark'.",
        "traceforge_version": "TraceForge version.",
        "benchmark_id": "Evaluation id.",
        "generated_at": "UTC timestamp.",
        "config": "Benchmark configuration.",
        "config_hash": "Configuration hash.",
        "duration_seconds": "Benchmark wall time.",
        "cancelled": "Whether the run was cancelled.",
        "demo_provider": "Whether the scripted demo provider was used.",
        "disclaimer": "Demo-provider disclaimer or null.",
        "run_mode": "official | diagnostic.",
        "official": "Official and leakage checks passed.",
        "evidence": "Evidence label.",
        "metrics_schema_version": "Metric contract version.",
        "report_tier": "development | final.",
        "leakage": "Leakage / lineage report.",
        "metrics": "Benchmark aggregate (record benchmark_aggregate).",
        "artifacts": "Artifact references.",
        "extra": "Run extras.",
        "non_finite_fields": "Optional: paths whose non-finite values were written as null.",
    },
}
OPTIONAL_RECORD_FIELDS = {"benchmark_document": frozenset({"non_finite_fields"})}


def lookup(path: str) -> MetricSpec | None:
    """The spec registered for a dotted path, matching ``*`` segments."""
    if path in METRICS:
        return METRICS[path]
    parts = path.split(".")
    for key, spec in METRICS.items():
        kparts = key.split(".")
        if len(kparts) == len(parts) and all(
            k in ("*", p) for k, p in zip(kparts, parts, strict=True)
        ):
            return spec
    return None


def audit_summary(summary: dict[str, Any]) -> tuple[list[str], set[str]]:
    """(undocumented paths, registered keys that were emitted) for one summarize() output.

    Every leaf at any depth must match a registered path; a registered value object must hold
    exactly its documented fields (or be null); an empty dict needs a registered group.
    """
    undocumented: list[str] = []
    seen: set[str] = set()

    def walk(value: Any, path: str) -> None:
        spec = lookup(path) if path else None
        if spec is not None:
            seen.add(spec.key)
        if spec is not None and spec.kind in VALUE_OBJECT_FIELDS:
            if isinstance(value, dict):
                undocumented.extend(
                    f"{path}.{k}" for k in sorted(set(value) - VALUE_OBJECT_FIELDS[spec.kind])
                )
            return
        if isinstance(value, dict) and value:
            for k, v in value.items():
                walk(v, f"{path}.{k}" if path else str(k))
            return
        if spec is None:
            undocumented.append(path)

    walk(summary, "")
    return undocumented, seen


def record_problems(name: str, record: dict[str, Any]) -> list[str]:
    """Fields of one emitted record that are missing from, or undocumented in, its schema."""
    schema = set(RECORD_SCHEMAS[name])
    optional = OPTIONAL_RECORD_FIELDS.get(name, frozenset())
    fields = set(record)
    return [f"undocumented {name}.{f}" for f in sorted(fields - schema)] + [
        f"missing {name}.{f}" for f in sorted(schema - optional - fields)
    ]


def _cell(text: str) -> str:
    return text.replace("|", "\\|")  # a literal "|" would split the table column


def render_markdown() -> str:
    """docs/metrics-contract.md (generated; do not edit by hand)."""
    lines = [
        "# Metric contract",
        "",
        f"Schema version **{METRICS_SCHEMA_VERSION}**. Generated from "
        "`src/traceforge/modules/evaluation/metric_contract.py`; do not edit by hand "
        "(`tests/unit/test_metric_contract.py` fails if this file drifts from the registry or "
        "if any emitted field is undocumented).",
        "",
        "## Rules",
        "",
        "- An empty denominator yields `null`, never 0.",
        "- Unknown telemetry is excluded and reported as coverage (`telemetry_coverage.*`). "
        "Totals need every episode: any episode with unknown usage or cost, including every "
        "provider-failure episode, makes the full token/cost total `null`. When cost coverage "
        "is partial, `cost.lower_bound_usd` sums the known costs and is a lower bound, never "
        "the total; `cost.status` states why a total is missing (partial, unknown, overflow).",
        "- Verdict counts use the verdict of the run's verifier profile. The default `v3_full` "
        "profile is authoritative; non-authoritative research profiles (R1/R2) are counted the "
        "same way, so read results together with `verifier_profile`.",
        "- `verified_success` keeps the scheduled-episode denominator; provider failures are "
        "reported separately (`provider_failures.*`) and are not verifier errors.",
        "- Regression checks: only PASS and FAIL are usable results. ERROR and INCONCLUSIVE "
        "checks are counted separately and are never treated as passes.",
        "- Wilson intervals are episode-level and descriptive. Population claims use the "
        "task-clustered bootstrap (pooled-ratio estimand; tasks weighted by their episode "
        "count), whose unit is the independent task. Repeated seeds are not independent tasks.",
        "- Without independent labels, `false_positive_reward_rate` is reward-verifier "
        "disagreement (`false_positive_reward_label_source: verifier_derived`), not an "
        "independently labelled false-positive rate.",
        "- Percentiles: n=0 gives null; n=1 gives the single value; n>=2 uses "
        "`statistics.quantiles(n=100, method='inclusive')`, clamped to the sample range.",
        "- All metric JSON is finite: non-finite values are written as `null` and their paths "
        "listed under `non_finite_fields`; Markdown reports are rendered from the same finite "
        "document.",
        "- Compatibility: every v0.2 output key is still emitted. Records written before v0.3 "
        "telemetry carry no usage/cost coverage, so token and cost totals over them (or over a "
        "mix of old and new records) are `null`.",
        "",
        "## Value objects",
        "",
        "| Kind | Fields |",
        "|---|---|",
        *[f"| {kind} | {_cell(VALUE_OBJECT_DESCRIPTIONS[kind])} |" for kind in VALUE_OBJECT_FIELDS],
        "",
        "## Metrics (`summarize()` output)",
        "",
        "Paths are dotted; `*` matches a dynamic key. `group` rows document nested blocks.",
        "",
        "| Key | Kind | Numerator | Denominator | Unit | Statistical unit | Interval | Missing "
        "data | Notes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in _SPECS:
        note = s.interpretation + (" *(secondary)*" if s.secondary else "")
        cells: tuple[str, ...] = (
            s.kind,
            s.numerator,
            s.denominator,
            s.unit,
            s.statistical_unit,
            s.interval,
            s.missing,
            note,
        )
        lines.append(f"| `{s.key}` | " + " | ".join(_cell(c) for c in cells) + " |")
    lines += ["", "## Record schemas", ""]
    for name, fields in RECORD_SCHEMAS.items():
        lines += [f"### `{name}`", "", "| Field | Meaning |", "|---|---|"]
        lines += [f"| `{f}` | {_cell(desc)} |" for f, desc in fields.items()]
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
