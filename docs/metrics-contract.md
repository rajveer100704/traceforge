# Metric contract

Schema version **2**. Generated from `src/traceforge/modules/evaluation/metric_contract.py`; do not edit by hand (`tests/unit/test_metric_contract.py` fails if this file drifts from the registry).

## Rules

- An empty denominator yields `null`, never 0.
- Unknown telemetry is excluded and reported as coverage (`telemetry_coverage.*`). Totals are `null` unless coverage is complete; `cost.lower_bound_usd` is published only when cost coverage is partial and is a lower bound, never the total.
- `verified_success` keeps the scheduled-episode denominator; provider failures are reported separately (`provider_failures.*`) and are not verifier errors.
- Wilson intervals are episode-level and descriptive. Population claims use the task-clustered bootstrap (pooled-ratio estimand; tasks weighted by their episode count), whose unit is the independent task. Repeated seeds are not independent tasks.
- Without independent labels, `false_positive_reward_rate` is reward-verifier disagreement (`false_positive_reward_label_source: verifier_derived`), not an independently labelled false-positive rate.
- Percentiles: n=0 gives null; n=1 gives the single value; n>=2 uses `statistics.quantiles(n=100, method='inclusive')`.
- All metric JSON is finite: non-finite values are written as `null` and their paths listed under `non_finite_fields`.

## Metrics

| Key | Kind | Numerator | Denominator | Unit | Statistical unit | Interval | Missing data | Notes |
|---|---|---|---|---|---|---|---|---|
| `metrics_schema_version` | info | n/a | n/a | version | none | none | not_applicable | Metric contract version of this output. |
| `n` | count | episodes | n/a | episodes | episode | none | not_applicable | Scheduled episodes (alias of n_episodes). |
| `n_episodes` | count | episodes | n/a | episodes | episode | none | not_applicable | Scheduled episodes, including provider failures. |
| `n_tasks` | count | distinct task ids | n/a | tasks | task_cluster | none | not_applicable | Independent task clusters: the effective sample size for population claims. |
| `episodes_per_task` | info | min/max episodes per task | n/a | episodes | task_cluster | none | not_applicable | Null when there are no episodes. |
| `n_seeds` | count | distinct non-null seeds | n/a | seeds | none | none | not_applicable | Repeated seeds are not independent tasks. |
| `reward_threshold` | info | n/a | n/a | reward | none | none | not_applicable | Reward at or above which an episode counts as rewarded. |
| `statistics` | info | n/a | n/a | n/a | none | none | not_applicable | Confidence, bootstrap samples and seed, interval methods and estimand notes. |
| `labelled_n` | count | episodes with an independent ground-truth label | n/a | episodes | labelled_episode | none | not_applicable | 0 when no labels were supplied. |
| `verified_success` | proportion | authoritative PASS | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | End-to-end verified success; provider failures count as non-successes. |
| `partial_success` | proportion | authoritative PARTIAL | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Not counted as success. |
| `verifier_error_rate` | proportion | verdict ERROR or INCONCLUSIVE | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Verifier/infrastructure errors during verification; provider failures are reported separately. |
| `reward_positive_rate` | proportion | reward >= threshold | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Share of episodes the reward policy rewarded. |
| `false_positive_reward_rate` | proportion | rewarded and not truly successful | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Ground truth per false_positive_reward_label_source: with only verifier-derived labels this is reward-verifier disagreement, not an independently labelled false-positive rate. |
| `false_positive_reward_label_source` | label | n/a | n/a | label | none | none | not_applicable | independent_labels \| mixed \| verifier_derived. |
| `reward_precision` | proportion | rewarded and truly successful (labelled) | rewarded labelled episodes | fraction | labelled_episode | wilson (episode-level, descriptive) | excluded_with_coverage | Null without independent labels. |
| `reward_recall` | proportion | truly successful and rewarded (labelled) | truly successful labelled episodes | fraction | labelled_episode | wilson (episode-level, descriptive) | excluded_with_coverage | Null without independent labels. |
| `verifier_precision` | ratio | PASS and truly successful (labelled) | PASS labelled episodes | fraction | labelled_episode | none | excluded_with_coverage | Null without labels or with an empty denominator. |
| `verifier_recall` | ratio | PASS and truly successful (labelled) | truly successful labelled episodes | fraction | labelled_episode | none | excluded_with_coverage | Null without labels or with an empty denominator. |
| `hidden_security_pass_rate` | proportion | hidden-security check PASS | episodes where the check executed | fraction | episode | wilson (episode-level, descriptive) | excluded_with_coverage | Skipped or missing checks are excluded; coverage in telemetry_coverage.hidden_security_check. |
| `regression_failure_rate` | proportion | regression check FAIL | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | excluded_with_coverage | v0.2 denominator: episodes without an executed check count as not failed; see regression_failure_rate_executed. |
| `regression_failure_rate_executed` | proportion | regression check FAIL | episodes where the check executed | fraction | episode | wilson (episode-level, descriptive) | excluded_with_coverage | Failure rate among executed regression checks. |
| `mean_reward` | mean | sum of final rewards | all scheduled episodes | reward | episode | none | not_applicable | Null with no episodes. |
| `mean_reward_ci` | info | n/a | n/a | reward | episode | episode-level percentile bootstrap (descriptive) | not_applicable | Episode-level (i.i.d.) bootstrap of mean reward; see task_clustered.mean_reward for the population interval. |
| `provider_failures.rate` | proportion | episodes ending in a provider failure (provider_timeout / provider_error) | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Model-access/infrastructure failures, distinct from verifier errors. |
| `provider_failures.timeout_rate` | proportion | provider failures of kind timeout | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Runner deadline and provider-raised timeouts (see by_source). |
| `provider_failures.by_kind` | count | provider failures per kind | n/a | episodes | episode | none | not_applicable | timeout \| rate_limited \| server_error \| http_error \| transport_error \| malformed_response \| other \| unknown (pre-v0.3 records). |
| `provider_failures.by_source` | count | provider failures per source | n/a | episodes | episode | none | not_applicable | runner_deadline \| provider \| unknown. |
| `verifier_outcomes_without_provider_failure.n` | count | episodes without a provider failure | n/a | episodes | episode | none | not_applicable | Denominator of the conditional verifier outcomes. |
| `verifier_outcomes_without_provider_failure.verified_success` | proportion | authoritative PASS | episodes without a provider failure | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Conditional, descriptive; the end-to-end rate is verified_success. |
| `verifier_outcomes_without_provider_failure.partial_success` | proportion | authoritative PARTIAL | episodes without a provider failure | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Conditional, descriptive. |
| `verifier_outcomes_without_provider_failure.verifier_error_rate` | proportion | ERROR or INCONCLUSIVE | episodes without a provider failure | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Conditional, descriptive. |
| `task_clustered.verified_success` | proportion | authoritative PASS | all episodes of resampled tasks | fraction | task_cluster | task-cluster percentile bootstrap (pooled ratio) | not_applicable | Interval width reflects independent tasks, not episodes. |
| `task_clustered.false_positive_reward_rate` | proportion | rewarded and not truly successful | all episodes of resampled tasks | fraction | task_cluster | task-cluster percentile bootstrap (pooled ratio) | not_applicable | Interval width reflects independent tasks, not episodes. |
| `task_clustered.hidden_security_pass_rate` | proportion | hidden-security PASS (executed checks only) | all episodes of resampled tasks | fraction | task_cluster | task-cluster percentile bootstrap (pooled ratio) | excluded_with_coverage | Interval width reflects independent tasks, not episodes. |
| `task_clustered.regression_failure_rate` | proportion | regression FAIL | all episodes of resampled tasks | fraction | task_cluster | task-cluster percentile bootstrap (pooled ratio) | not_applicable | Interval width reflects independent tasks, not episodes. |
| `task_clustered.mean_reward` | mean | final reward | all episodes of resampled tasks | reward | task_cluster | task-cluster percentile bootstrap (pooled ratio) | not_applicable | Interval width reflects independent tasks, not episodes. |
| `task_clustered.provider_failure_rate` | proportion | provider failure | all episodes of resampled tasks | fraction | task_cluster | task-cluster percentile bootstrap (pooled ratio) | not_applicable | Interval width reflects independent tasks, not episodes. |
| `tool_calls_per_attempt` | mean | tool calls | all scheduled episodes | calls | episode | none | not_applicable | v0.2 key; equals tool_calls.per_attempt. |
| `tool_calls_per_success` | ratio | tool calls of PASS episodes | PASS episodes | calls | episode | none | not_applicable | v0.2 key; null with no successes; equals tool_calls.per_success. |
| `tool_calls.total` | total | tool calls | n/a | calls | episode | none | not_applicable | All recorded tool calls. |
| `tool_calls.per_attempt` | ratio | tool calls | all scheduled episodes | calls | episode | none | not_applicable | Null with no episodes. |
| `tool_calls.per_success` | ratio | tool calls of PASS episodes | PASS episodes | calls | episode | none | not_applicable | Null with no successes. |
| `action_rejection_rate` | ratio | rejected tool calls | tool calls | fraction | episode | none | not_applicable | Null with no tool calls. |
| `token_usage` | info | legacy token block | n/a | tokens | episode | none | null_if_any_missing | v0.2 shape; null unless every episode reported complete usage (and total > 0). See tokens.*. |
| `tokens.prompt_total` | total | prompt tokens | n/a | tokens | episode | none | null_if_any_missing | Null unless every episode reported complete usage. |
| `tokens.completion_total` | total | completion tokens | n/a | tokens | episode | none | null_if_any_missing | Null unless every episode reported complete usage. |
| `tokens.total` | total | prompt + completion tokens | n/a | tokens | episode | none | null_if_any_missing | Null unless every episode reported complete usage. |
| `tokens.per_attempt` | ratio | tokens.total | all scheduled episodes | tokens | episode | none | null_if_any_missing | Null when tokens.total is null or there are no episodes. |
| `tokens.per_verified_success` | ratio | tokens.total | PASS episodes | tokens | episode | none | null_if_any_missing | Null when tokens.total is null or there are no successes. |
| `tokens.verified_successes_per_1000_tokens` | ratio | PASS episodes x 1000 | tokens.total | successes per 1K tokens | episode | none | null_if_any_missing | Secondary efficiency measure. *(secondary)* |
| `tokens.episodes_with_complete_usage` | count | episodes whose every request reported usage | n/a | episodes | episode | none | not_applicable | Coverage numerator. |
| `tokens.n_episodes` | count | episodes | n/a | episodes | episode | none | not_applicable | Coverage denominator. |
| `cost_per_verified_success_usd` | ratio | cost.total_usd | PASS episodes | usd | episode | none | null_if_any_missing | v0.2 key; equals cost.per_verified_success_usd. Never 0 for unknown cost. |
| `cost.total_usd` | total | sum of episode costs | n/a | usd | episode | none | null_if_any_missing | Null if any episode's cost is unknown. 0.0 only for explicitly free providers. |
| `cost.per_attempt_usd` | ratio | cost.total_usd | all scheduled episodes | usd | episode | none | null_if_any_missing | Null if cost.total_usd is null or there are no episodes. |
| `cost.per_verified_success_usd` | ratio | cost.total_usd | PASS episodes | usd | episode | none | null_if_any_missing | Null if cost.total_usd is null or there are no successes. |
| `cost.lower_bound_usd` | total | sum of known episode costs | n/a | usd | episode | none | excluded_with_coverage | Published only when cost coverage is partial: a LOWER BOUND on total spend, never the total. |
| `cost.episodes_with_known_cost` | count | episodes with a known cost | n/a | episodes | episode | none | not_applicable | Coverage numerator. |
| `cost.n_episodes` | count | episodes | n/a | episodes | episode | none | not_applicable | Coverage denominator. |
| `latency.request_seconds` | percentiles | per-request generation seconds (incl. retries) | requests with a recorded duration | seconds | request | none | excluded_with_coverage | n, p50, p90, p95, max; pooled over requests. |
| `latency.episode_seconds` | percentiles | episode wall time (rollout duration_seconds) | episodes | seconds | episode | none | not_applicable | n, p50, p90, p95, max. |
| `latency.generation_seconds_per_episode` | percentiles | per-episode generation seconds | episodes that recorded it | seconds | episode | none | excluded_with_coverage | n, p50, p90, p95, max. |
| `mean_duration_seconds` | mean | episode wall time | all scheduled episodes | seconds | episode | none | not_applicable | v0.2 key; null with no episodes. |
| `mean_generation_seconds` | mean | per-episode generation seconds | episodes that recorded it | seconds | episode | none | excluded_with_coverage | v0.2 key; see latency.* for percentiles and coverage. |
| `environment_compute_seconds` | total | sum of verification check durations | n/a | seconds | episode | none | not_applicable | Verifier compute time. |
| `finish_reasons` | count | model finish reasons over successful requests | n/a | requests | request | none | excluded_with_coverage | counts (incl. not_reported) and truncated_length (finish_reason 'length'). |
| `telemetry_coverage.generation_telemetry` | coverage | episodes recorded with v0.3 generation telemetry | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.usage` | coverage | episodes whose every request reported usage | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.cost` | coverage | episodes with a known cost | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.request_latency` | coverage | episodes with a duration for every request | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.attempts` | coverage | episodes with known attempt counts for every request | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.hidden_security_check` | coverage | episodes where the hidden-security check executed | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.regression_check` | coverage | episodes where the regression check executed | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
