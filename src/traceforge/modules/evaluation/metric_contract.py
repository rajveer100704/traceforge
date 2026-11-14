"""Versioned metric contract: what every benchmark metric means.

Each emitted key of ``summarize()`` is registered with its numerator, denominator, unit,
statistical unit, interval method and missing-data rule. ``docs/metrics-contract.md`` is
generated from this registry (``render_markdown``); a test fails if the two drift or if
``summarize()`` emits an unregistered key.

General rules (schema 2):
- An empty denominator yields ``null`` (never 0).
- Unknown telemetry is excluded and reported as coverage; totals are ``null`` unless coverage
  is complete (a cost lower bound is published under an explicitly named key).
- Episode-level Wilson intervals are descriptive; population claims use the task-clustered
  bootstrap, whose statistical unit is the independent task (seeds are not independent tasks).
- Verifier-derived reward disagreement is never presented as an independently labelled
  false-positive rate.
"""

from __future__ import annotations

from dataclasses import dataclass

METRICS_SCHEMA_VERSION = "2"

# Statistical units and interval methods used below.
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


@dataclass(frozen=True)
class MetricSpec:
    key: str  # dotted path in summarize() output
    kind: str  # proportion | ratio | mean | total | count | percentiles | coverage | label | info
    numerator: str
    denominator: str
    unit: str
    statistical_unit: str
    interval: str
    missing: str
    interpretation: str
    secondary: bool = False


def _p(
    key: str,
    numerator: str,
    denominator: str,
    interpretation: str,
    *,
    interval: str = WILSON,
    unit_of_analysis: str = EPISODE,
    missing: str = NOT_APPLICABLE,
) -> MetricSpec:
    return MetricSpec(
        key, "proportion", numerator, denominator, "fraction", unit_of_analysis, interval,
        missing, interpretation,
    )  # fmt: skip


_SPECS: tuple[MetricSpec, ...] = (
    # ---- sample sizes and identity -------------------------------------------------------
    MetricSpec(
        "metrics_schema_version",
        "info",
        "n/a",
        "n/a",
        "version",
        NONE,
        NONE,
        NOT_APPLICABLE,
        "Metric contract version of this output.",
    ),
    MetricSpec(
        "n",
        "count",
        "episodes",
        "n/a",
        "episodes",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Scheduled episodes (alias of n_episodes).",
    ),
    MetricSpec(
        "n_episodes",
        "count",
        "episodes",
        "n/a",
        "episodes",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Scheduled episodes, including provider failures.",
    ),
    MetricSpec(
        "n_tasks",
        "count",
        "distinct task ids",
        "n/a",
        "tasks",
        TASK,
        NONE,
        NOT_APPLICABLE,
        "Independent task clusters: the effective sample size for population claims.",
    ),
    MetricSpec(
        "episodes_per_task",
        "info",
        "min/max episodes per task",
        "n/a",
        "episodes",
        TASK,
        NONE,
        NOT_APPLICABLE,
        "Null when there are no episodes.",
    ),
    MetricSpec(
        "n_seeds",
        "count",
        "distinct non-null seeds",
        "n/a",
        "seeds",
        NONE,
        NONE,
        NOT_APPLICABLE,
        "Repeated seeds are not independent tasks.",
    ),
    MetricSpec(
        "reward_threshold",
        "info",
        "n/a",
        "n/a",
        "reward",
        NONE,
        NONE,
        NOT_APPLICABLE,
        "Reward at or above which an episode counts as rewarded.",
    ),
    MetricSpec(
        "statistics",
        "info",
        "n/a",
        "n/a",
        "n/a",
        NONE,
        NONE,
        NOT_APPLICABLE,
        "Confidence, bootstrap samples and seed, interval methods and estimand notes.",
    ),
    MetricSpec(
        "labelled_n",
        "count",
        "episodes with an independent ground-truth label",
        "n/a",
        "episodes",
        LABELLED,
        NONE,
        NOT_APPLICABLE,
        "0 when no labels were supplied.",
    ),
    # ---- outcomes ------------------------------------------------------------------------
    _p(
        "verified_success",
        "authoritative PASS",
        "all scheduled episodes",
        "End-to-end verified success; provider failures count as non-successes.",
    ),
    _p(
        "partial_success",
        "authoritative PARTIAL",
        "all scheduled episodes",
        "Not counted as success.",
    ),
    _p(
        "verifier_error_rate",
        "verdict ERROR or INCONCLUSIVE",
        "all scheduled episodes",
        "Verifier/infrastructure errors during verification; provider failures are reported separately.",
    ),
    _p(
        "reward_positive_rate",
        "reward >= threshold",
        "all scheduled episodes",
        "Share of episodes the reward policy rewarded.",
    ),
    _p(
        "false_positive_reward_rate",
        "rewarded and not truly successful",
        "all scheduled episodes",
        "Ground truth per false_positive_reward_label_source: with only verifier-derived "
        "labels this is reward-verifier disagreement, not an independently labelled "
        "false-positive rate.",
    ),
    MetricSpec(
        "false_positive_reward_label_source",
        "label",
        "n/a",
        "n/a",
        "label",
        NONE,
        NONE,
        NOT_APPLICABLE,
        "independent_labels | mixed | verifier_derived.",
    ),
    _p(
        "reward_precision",
        "rewarded and truly successful (labelled)",
        "rewarded labelled episodes",
        "Null without independent labels.",
        unit_of_analysis=LABELLED,
        missing=EXCLUDED,
    ),
    _p(
        "reward_recall",
        "truly successful and rewarded (labelled)",
        "truly successful labelled episodes",
        "Null without independent labels.",
        unit_of_analysis=LABELLED,
        missing=EXCLUDED,
    ),
    MetricSpec(
        "verifier_precision",
        "ratio",
        "PASS and truly successful (labelled)",
        "PASS labelled episodes",
        "fraction",
        LABELLED,
        NONE,
        EXCLUDED,
        "Null without labels or with an empty denominator.",
    ),
    MetricSpec(
        "verifier_recall",
        "ratio",
        "PASS and truly successful (labelled)",
        "truly successful labelled episodes",
        "fraction",
        LABELLED,
        NONE,
        EXCLUDED,
        "Null without labels or with an empty denominator.",
    ),
    _p(
        "hidden_security_pass_rate",
        "hidden-security check PASS",
        "episodes where the check executed",
        "Skipped or missing checks are excluded; coverage in telemetry_coverage.hidden_security_check.",
        missing=EXCLUDED,
    ),
    _p(
        "regression_failure_rate",
        "regression check FAIL",
        "all scheduled episodes",
        "v0.2 denominator: episodes without an executed check count as not failed; see "
        "regression_failure_rate_executed.",
        missing=EXCLUDED,
    ),
    _p(
        "regression_failure_rate_executed",
        "regression check FAIL",
        "episodes where the check executed",
        "Failure rate among executed regression checks.",
        missing=EXCLUDED,
    ),
    MetricSpec(
        "mean_reward",
        "mean",
        "sum of final rewards",
        "all scheduled episodes",
        "reward",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Null with no episodes.",
    ),
    MetricSpec(
        "mean_reward_ci",
        "info",
        "n/a",
        "n/a",
        "reward",
        EPISODE,
        EPISODE_BOOT,
        NOT_APPLICABLE,
        "Episode-level (i.i.d.) bootstrap of mean reward; see task_clustered.mean_reward for the population interval.",
    ),
    # ---- provider failures vs verifier outcomes ----------------------------------------
    _p(
        "provider_failures.rate",
        "episodes ending in a provider failure (provider_timeout / provider_error)",
        "all scheduled episodes",
        "Model-access/infrastructure failures, distinct from verifier errors.",
    ),
    _p(
        "provider_failures.timeout_rate",
        "provider failures of kind timeout",
        "all scheduled episodes",
        "Runner deadline and provider-raised timeouts (see by_source).",
    ),
    MetricSpec(
        "provider_failures.by_kind",
        "count",
        "provider failures per kind",
        "n/a",
        "episodes",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "timeout | rate_limited | server_error | http_error | transport_error | "
        "malformed_response | other | unknown (pre-v0.3 records).",
    ),
    MetricSpec(
        "provider_failures.by_source",
        "count",
        "provider failures per source",
        "n/a",
        "episodes",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "runner_deadline | provider | unknown.",
    ),
    MetricSpec(
        "verifier_outcomes_without_provider_failure.n",
        "count",
        "episodes without a provider failure",
        "n/a",
        "episodes",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Denominator of the conditional verifier outcomes.",
    ),
    _p(
        "verifier_outcomes_without_provider_failure.verified_success",
        "authoritative PASS",
        "episodes without a provider failure",
        "Conditional, descriptive; the end-to-end rate is verified_success.",
    ),
    _p(
        "verifier_outcomes_without_provider_failure.partial_success",
        "authoritative PARTIAL",
        "episodes without a provider failure",
        "Conditional, descriptive.",
    ),
    _p(
        "verifier_outcomes_without_provider_failure.verifier_error_rate",
        "ERROR or INCONCLUSIVE",
        "episodes without a provider failure",
        "Conditional, descriptive.",
    ),
    # ---- task-clustered intervals --------------------------------------------------------
    *(
        MetricSpec(
            f"task_clustered.{k}",
            "proportion" if k != "mean_reward" else "mean",
            d,
            "all episodes of resampled tasks",
            "fraction" if k != "mean_reward" else "reward",
            TASK,
            CLUSTER,
            m,
            "Interval width reflects independent tasks, not episodes.",
        )
        for k, d, m in (
            ("verified_success", "authoritative PASS", NOT_APPLICABLE),
            ("false_positive_reward_rate", "rewarded and not truly successful", NOT_APPLICABLE),
            ("hidden_security_pass_rate", "hidden-security PASS (executed checks only)", EXCLUDED),
            ("regression_failure_rate", "regression FAIL", NOT_APPLICABLE),
            ("mean_reward", "final reward", NOT_APPLICABLE),
            ("provider_failure_rate", "provider failure", NOT_APPLICABLE),
        )
    ),
    # ---- tool calls ------------------------------------------------------------------------
    MetricSpec(
        "tool_calls_per_attempt",
        "mean",
        "tool calls",
        "all scheduled episodes",
        "calls",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "v0.2 key; equals tool_calls.per_attempt.",
    ),
    MetricSpec(
        "tool_calls_per_success",
        "ratio",
        "tool calls of PASS episodes",
        "PASS episodes",
        "calls",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "v0.2 key; null with no successes; equals tool_calls.per_success.",
    ),
    MetricSpec(
        "tool_calls.total",
        "total",
        "tool calls",
        "n/a",
        "calls",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "All recorded tool calls.",
    ),
    MetricSpec(
        "tool_calls.per_attempt",
        "ratio",
        "tool calls",
        "all scheduled episodes",
        "calls",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Null with no episodes.",
    ),
    MetricSpec(
        "tool_calls.per_success",
        "ratio",
        "tool calls of PASS episodes",
        "PASS episodes",
        "calls",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Null with no successes.",
    ),
    MetricSpec(
        "action_rejection_rate",
        "ratio",
        "rejected tool calls",
        "tool calls",
        "fraction",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Null with no tool calls.",
    ),
    # ---- tokens ----------------------------------------------------------------------------
    MetricSpec(
        "token_usage",
        "info",
        "legacy token block",
        "n/a",
        "tokens",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "v0.2 shape; null unless every episode reported complete usage (and total > 0). See tokens.*.",
    ),
    MetricSpec(
        "tokens.prompt_total",
        "total",
        "prompt tokens",
        "n/a",
        "tokens",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "Null unless every episode reported complete usage.",
    ),
    MetricSpec(
        "tokens.completion_total",
        "total",
        "completion tokens",
        "n/a",
        "tokens",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "Null unless every episode reported complete usage.",
    ),
    MetricSpec(
        "tokens.total",
        "total",
        "prompt + completion tokens",
        "n/a",
        "tokens",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "Null unless every episode reported complete usage.",
    ),
    MetricSpec(
        "tokens.per_attempt",
        "ratio",
        "tokens.total",
        "all scheduled episodes",
        "tokens",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "Null when tokens.total is null or there are no episodes.",
    ),
    MetricSpec(
        "tokens.per_verified_success",
        "ratio",
        "tokens.total",
        "PASS episodes",
        "tokens",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "Null when tokens.total is null or there are no successes.",
    ),
    MetricSpec(
        "tokens.verified_successes_per_1000_tokens",
        "ratio",
        "PASS episodes x 1000",
        "tokens.total",
        "successes per 1K tokens",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "Secondary efficiency measure.",
        secondary=True,
    ),
    MetricSpec(
        "tokens.episodes_with_complete_usage",
        "count",
        "episodes whose every request reported usage",
        "n/a",
        "episodes",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Coverage numerator.",
    ),
    MetricSpec(
        "tokens.n_episodes",
        "count",
        "episodes",
        "n/a",
        "episodes",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Coverage denominator.",
    ),
    # ---- cost ------------------------------------------------------------------------------
    MetricSpec(
        "cost_per_verified_success_usd",
        "ratio",
        "cost.total_usd",
        "PASS episodes",
        "usd",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "v0.2 key; equals cost.per_verified_success_usd. Never 0 for unknown cost.",
    ),
    MetricSpec(
        "cost.total_usd",
        "total",
        "sum of episode costs",
        "n/a",
        "usd",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "Null if any episode's cost is unknown. 0.0 only for explicitly free providers.",
    ),
    MetricSpec(
        "cost.per_attempt_usd",
        "ratio",
        "cost.total_usd",
        "all scheduled episodes",
        "usd",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "Null if cost.total_usd is null or there are no episodes.",
    ),
    MetricSpec(
        "cost.per_verified_success_usd",
        "ratio",
        "cost.total_usd",
        "PASS episodes",
        "usd",
        EPISODE,
        NONE,
        NULL_IF_MISSING,
        "Null if cost.total_usd is null or there are no successes.",
    ),
    MetricSpec(
        "cost.lower_bound_usd",
        "total",
        "sum of known episode costs",
        "n/a",
        "usd",
        EPISODE,
        NONE,
        EXCLUDED,
        "Published only when cost coverage is partial: a LOWER BOUND on total spend, never the total.",
    ),
    MetricSpec(
        "cost.episodes_with_known_cost",
        "count",
        "episodes with a known cost",
        "n/a",
        "episodes",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Coverage numerator.",
    ),
    MetricSpec(
        "cost.n_episodes",
        "count",
        "episodes",
        "n/a",
        "episodes",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Coverage denominator.",
    ),
    # ---- latency ---------------------------------------------------------------------------
    MetricSpec(
        "latency.request_seconds",
        "percentiles",
        "per-request generation seconds (incl. retries)",
        "requests with a recorded duration",
        "seconds",
        REQUEST,
        NONE,
        EXCLUDED,
        "n, p50, p90, p95, max; pooled over requests.",
    ),
    MetricSpec(
        "latency.episode_seconds",
        "percentiles",
        "episode wall time (rollout duration_seconds)",
        "episodes",
        "seconds",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "n, p50, p90, p95, max.",
    ),
    MetricSpec(
        "latency.generation_seconds_per_episode",
        "percentiles",
        "per-episode generation seconds",
        "episodes that recorded it",
        "seconds",
        EPISODE,
        NONE,
        EXCLUDED,
        "n, p50, p90, p95, max.",
    ),
    MetricSpec(
        "mean_duration_seconds",
        "mean",
        "episode wall time",
        "all scheduled episodes",
        "seconds",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "v0.2 key; null with no episodes.",
    ),
    MetricSpec(
        "mean_generation_seconds",
        "mean",
        "per-episode generation seconds",
        "episodes that recorded it",
        "seconds",
        EPISODE,
        NONE,
        EXCLUDED,
        "v0.2 key; see latency.* for percentiles and coverage.",
    ),
    MetricSpec(
        "environment_compute_seconds",
        "total",
        "sum of verification check durations",
        "n/a",
        "seconds",
        EPISODE,
        NONE,
        NOT_APPLICABLE,
        "Verifier compute time.",
    ),
    MetricSpec(
        "finish_reasons",
        "count",
        "model finish reasons over successful requests",
        "n/a",
        "requests",
        REQUEST,
        NONE,
        EXCLUDED,
        "counts (incl. not_reported) and truncated_length (finish_reason 'length').",
    ),
    # ---- coverage --------------------------------------------------------------------------
    *(
        MetricSpec(
            f"telemetry_coverage.{k}",
            "coverage",
            num,
            "all scheduled episodes",
            "episodes",
            EPISODE,
            NONE,
            NOT_APPLICABLE,
            "covered, n, fraction (null with no episodes).",
        )
        for k, num in (
            ("generation_telemetry", "episodes recorded with v0.3 generation telemetry"),
            ("usage", "episodes whose every request reported usage"),
            ("cost", "episodes with a known cost"),
            ("request_latency", "episodes with a duration for every request"),
            ("attempts", "episodes with known attempt counts for every request"),
            ("hidden_security_check", "episodes where the hidden-security check executed"),
            ("regression_check", "episodes where the regression check executed"),
        )
    ),
)

METRICS: dict[str, MetricSpec] = {s.key: s for s in _SPECS}

# Top-level keys whose values are blocks of separately registered sub-metrics.
BLOCKS = frozenset(
    {
        "task_clustered",
        "tokens",
        "cost",
        "tool_calls",
        "latency",
        "telemetry_coverage",
        "provider_failures",
        "verifier_outcomes_without_provider_failure",
    }
)


def emitted_keys(summary: dict[str, object]) -> set[str]:
    """Registered-key view of a summarize() output: block sub-keys are dotted, others top-level."""
    keys: set[str] = set()
    for k, v in summary.items():
        if k in BLOCKS and isinstance(v, dict):
            keys.update(f"{k}.{sub}" for sub in v)
        else:
            keys.add(k)
    return keys


def render_markdown() -> str:
    """docs/metrics-contract.md (generated; do not edit by hand)."""
    lines = [
        "# Metric contract",
        "",
        f"Schema version **{METRICS_SCHEMA_VERSION}**. Generated from "
        "`src/traceforge/modules/evaluation/metric_contract.py`; do not edit by hand "
        "(`tests/unit/test_metric_contract.py` fails if this file drifts from the registry).",
        "",
        "## Rules",
        "",
        "- An empty denominator yields `null`, never 0.",
        "- Unknown telemetry is excluded and reported as coverage (`telemetry_coverage.*`). "
        "Totals are `null` unless coverage is complete; `cost.lower_bound_usd` is published "
        "only when cost coverage is partial and is a lower bound, never the total.",
        "- `verified_success` keeps the scheduled-episode denominator; provider failures are "
        "reported separately (`provider_failures.*`) and are not verifier errors.",
        "- Wilson intervals are episode-level and descriptive. Population claims use the "
        "task-clustered bootstrap (pooled-ratio estimand; tasks weighted by their episode count), "
        "whose unit is the independent task. Repeated seeds are not independent tasks.",
        "- Without independent labels, `false_positive_reward_rate` is reward-verifier "
        "disagreement (`false_positive_reward_label_source: verifier_derived`), not an "
        "independently labelled false-positive rate.",
        "- Percentiles: n=0 gives null; n=1 gives the single value; n>=2 uses "
        "`statistics.quantiles(n=100, method='inclusive')`.",
        "- All metric JSON is finite: non-finite values are written as `null` and their paths "
        "listed under `non_finite_fields`.",
        "",
        "## Metrics",
        "",
        "| Key | Kind | Numerator | Denominator | Unit | Statistical unit | Interval | Missing data | Notes |",
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
        # A literal "|" inside a cell would split the table column; escape it.
        lines.append(f"| `{s.key}` | " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(lines) + "\n"
