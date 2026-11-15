# Metric contract

Schema version **2**. Generated from `src/traceforge/modules/evaluation/metric_contract.py`; do not edit by hand (`tests/unit/test_metric_contract.py` fails if this file drifts from the registry or if any emitted field is undocumented).

## Rules

- An empty denominator yields `null`, never 0.
- Unknown telemetry is excluded and reported as coverage (`telemetry_coverage.*`). Totals need every episode: any episode with unknown usage or cost, including every provider-failure episode, makes the full token/cost total `null`. When cost coverage is partial, `cost.lower_bound_usd` sums the known costs and is a lower bound, never the total; `cost.status` states why a total is missing (partial, unknown, overflow).
- Verdict counts use the verdict of the run's verifier profile. The default `v3_full` profile is authoritative; non-authoritative research profiles (R1/R2) are counted the same way, so read results together with `verifier_profile`.
- `verified_success` keeps the scheduled-episode denominator; provider failures are reported separately (`provider_failures.*`) and are not verifier errors.
- Regression checks: only PASS and FAIL are usable results. ERROR and INCONCLUSIVE checks are counted separately and are never treated as passes.
- Wilson intervals are episode-level and descriptive. Population claims use the task-clustered bootstrap (pooled-ratio estimand; tasks weighted by their episode count), whose unit is the independent task. Repeated seeds are not independent tasks.
- Without independent labels, `false_positive_reward_rate` is reward-verifier disagreement (`false_positive_reward_label_source: verifier_derived`), not an independently labelled false-positive rate.
- Percentiles: n=0 gives null; n=1 gives the single value; n>=2 uses `statistics.quantiles(n=100, method='inclusive')`, clamped to the sample range.
- All metric JSON is finite: non-finite values are written as `null` and their paths listed under `non_finite_fields`; Markdown reports are rendered from the same finite document.
- Compatibility: every v0.2 output key is still emitted. Records written before v0.3 telemetry carry no usage/cost coverage, so token and cost totals over them (or over a mix of old and new records) are `null`.

## Value objects

| Kind | Fields |
|---|---|
| proportion | Wilson proportion: successes / n, rate (null when n = 0), ci_low, ci_high, confidence, method. |
| cluster_interval | Task-cluster bootstrap: estimate (pooled ratio), ci_low/ci_high (null with fewer than 2 tasks, then `note` explains), n_clusters, n_observations, confidence, samples, method. |
| percentiles | Percentile summary: n, p50, p90, p95, max (all null when n = 0), method. |
| coverage | Coverage: covered, n, fraction (null when n = 0). |

## Metrics (`summarize()` output)

Paths are dotted; `*` matches a dynamic key. `group` rows document nested blocks.

| Key | Kind | Numerator | Denominator | Unit | Statistical unit | Interval | Missing data | Notes |
|---|---|---|---|---|---|---|---|---|
| `metrics_schema_version` | info | n/a | n/a | version | none | none | not_applicable | Metric contract version of this output. |
| `n` | count | episodes | n/a | episodes | episode | none | not_applicable | Scheduled episodes (alias of n_episodes). |
| `n_episodes` | count | episodes | n/a | episodes | episode | none | not_applicable | Scheduled episodes, including provider failures. |
| `n_tasks` | count | distinct task ids | n/a | tasks | task_cluster | none | not_applicable | Independent task clusters: the effective sample size for population claims. |
| `episodes_per_task` | group | n/a | n/a | n/a | none | none | not_applicable | Min/max episodes per task; null when there are no episodes. |
| `episodes_per_task.min` | count | fewest episodes of any task | n/a | episodes | episode | none | not_applicable | Unequal counts change the pooled-ratio weights. |
| `episodes_per_task.max` | count | most episodes of any task | n/a | episodes | episode | none | not_applicable | Unequal counts change the pooled-ratio weights. |
| `n_seeds` | count | distinct non-null seeds | n/a | seeds | episode | none | not_applicable | Repeated seeds are not independent tasks. |
| `reward_threshold` | info | n/a | n/a | reward | none | none | not_applicable | Reward at or above which an episode counts as rewarded. |
| `labelled_n` | count | episodes with an independent ground-truth label | n/a | episodes | episode | none | not_applicable | 0 when no labels were supplied. |
| `statistics` | group | n/a | n/a | n/a | none | none | not_applicable | Methods and parameters used for the intervals in this output. |
| `statistics.confidence` | info | n/a | n/a | probability | none | none | not_applicable | Confidence level of every interval. |
| `statistics.bootstrap_samples` | info | n/a | n/a | resamples | none | none | not_applicable | Bootstrap resamples (2,000 development default; >= 10,000 for a final report). |
| `statistics.bootstrap_seed` | info | n/a | n/a | seed | none | none | not_applicable | Seed of every bootstrap in this output. |
| `statistics.proportion_interval` | info | n/a | n/a | n/a | none | none | not_applicable | Interval method for proportions. |
| `statistics.task_cluster_interval` | info | n/a | n/a | n/a | none | none | not_applicable | Interval method for task-clustered metrics. |
| `statistics.task_cluster_estimand` | info | n/a | n/a | n/a | none | none | not_applicable | Pooled ratio: tasks weighted by their episode count. |
| `statistics.mean_reward_interval` | info | n/a | n/a | n/a | none | none | not_applicable | Interval method for mean_reward_ci. |
| `statistics.percentile_method` | info | n/a | n/a | n/a | none | none | not_applicable | Percentile rule for n = 0, 1 and >= 2. |
| `verified_success` | proportion | PASS verdict (authoritative under the default v3_full verifier profile; counted the same way under non-authoritative research profiles) | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | End-to-end verified success; provider failures count as non-successes. |
| `partial_success` | proportion | PARTIAL verdict (same verifier-profile caveat as PASS) | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Not counted as success. |
| `verifier_error_rate` | proportion | verdict ERROR or INCONCLUSIVE | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Verifier/infrastructure errors during verification; provider failures are reported separately. |
| `reward_positive_rate` | proportion | reward >= threshold | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Share of episodes the reward policy rewarded. |
| `false_positive_reward_rate` | proportion | rewarded and not truly successful | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Truly successful = the independent label when one is supplied, otherwise an authoritative PASS (a non-authoritative PASS is not ground truth). With only verifier-derived labels this is reward-verifier disagreement, not an independently labelled false-positive rate. |
| `false_positive_reward_label_source` | label | n/a | n/a | label | none | none | not_applicable | independent_labels \| mixed \| verifier_derived. |
| `reward_precision` | proportion | rewarded and truly successful (labelled) | rewarded labelled episodes | fraction | labelled_episode | wilson (episode-level, descriptive) | excluded_with_coverage | Null without independent labels. |
| `reward_recall` | proportion | truly successful and rewarded (labelled) | truly successful labelled episodes | fraction | labelled_episode | wilson (episode-level, descriptive) | excluded_with_coverage | Null without independent labels. |
| `verifier_precision` | ratio | PASS verdict and truly successful (labelled) | PASS labelled episodes | fraction | labelled_episode | none | excluded_with_coverage | Null without labels or with an empty denominator. |
| `verifier_recall` | ratio | PASS verdict and truly successful (labelled) | truly successful labelled episodes | fraction | labelled_episode | none | excluded_with_coverage | Null without labels or with an empty denominator. |
| `hidden_security_pass_rate` | proportion | hidden-security check PASS | episodes where the check executed (not SKIPPED, present) | fraction | episode | wilson (episode-level, descriptive) | excluded_with_coverage | ERROR and INCONCLUSIVE checks count as executed non-passes (conservative, v0.2). Coverage in telemetry_coverage.hidden_security_check. |
| `regression_failure_rate` | proportion | regression check FAIL | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | excluded_with_coverage | v0.2 denominator: episodes without a FAIL (pass, error, inconclusive, skipped or missing check) count as not failed. See regression_failure_rate_executed. |
| `regression_failure_rate_executed` | proportion | regression check FAIL | episodes with a usable regression result (PASS or FAIL) | fraction | episode | wilson (episode-level, descriptive) | excluded_with_coverage | ERROR/INCONCLUSIVE checks ran without a usable result: they are excluded, never counted as passes, and reported in regression_check_outcomes and telemetry_coverage.regression_check_unusable. |
| `regression_check_outcomes` | group | n/a | n/a | n/a | none | none | not_applicable | Regression check result counts over all scheduled episodes. |
| `regression_check_outcomes.pass` | count | episodes whose regression check is pass | n/a | episodes | episode | none | not_applicable | Usable result. |
| `regression_check_outcomes.fail` | count | episodes whose regression check is fail | n/a | episodes | episode | none | not_applicable | Usable result; numerator of both regression failure rates. |
| `regression_check_outcomes.error` | count | episodes whose regression check is error | n/a | episodes | episode | none | not_applicable | Ran without a usable result. |
| `regression_check_outcomes.inconclusive` | count | episodes whose regression check is inconclusive | n/a | episodes | episode | none | not_applicable | Ran without a usable result. |
| `regression_check_outcomes.skipped` | count | episodes whose regression check is skipped | n/a | episodes | episode | none | not_applicable | Not executed. |
| `regression_check_outcomes.missing` | count | episodes whose regression check is missing | n/a | episodes | episode | none | not_applicable | No regression check recorded. |
| `mean_reward` | mean | sum of final rewards | all scheduled episodes | reward | episode | none | not_applicable | Null with no episodes. |
| `mean_reward_ci` | info | n/a | n/a | reward | episode | episode-level percentile bootstrap (descriptive) | not_applicable | [low, high]: episode-level (i.i.d.) bootstrap of mean reward; see task_clustered.mean_reward for the population interval. |
| `provider_failures` | group | n/a | n/a | n/a | none | none | not_applicable | Model-access/infrastructure failures, distinct from verifier errors. |
| `provider_failures.rate` | proportion | episodes ending in a provider failure (provider_timeout / provider_error) | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Their cost is unknown (a failed call may have been billed), so any provider failure makes full cost/token totals unavailable. |
| `provider_failures.timeout_rate` | proportion | provider failures of kind timeout | all scheduled episodes | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Runner deadline and provider-raised timeouts (see by_source). |
| `provider_failures.by_kind` | group | n/a | n/a | n/a | none | none | not_applicable | Failure counts per kind. |
| `provider_failures.by_kind.*` | count | provider failures of this kind | n/a | episodes | episode | none | not_applicable | timeout \| rate_limited \| server_error \| http_error \| transport_error \| malformed_response \| other \| unknown (pre-v0.3 records). |
| `provider_failures.by_source` | group | n/a | n/a | n/a | none | none | not_applicable | Failure counts per source. |
| `provider_failures.by_source.*` | count | provider failures from this source | n/a | episodes | episode | none | not_applicable | runner_deadline \| provider \| unknown. |
| `verifier_outcomes_without_provider_failure` | group | n/a | n/a | n/a | none | none | not_applicable | Conditional, descriptive verifier outcomes. |
| `verifier_outcomes_without_provider_failure.n` | count | episodes without a provider failure | n/a | episodes | episode | none | not_applicable | Denominator of the conditional verifier outcomes. |
| `verifier_outcomes_without_provider_failure.verified_success` | proportion | PASS verdict (authoritative under the default v3_full verifier profile; counted the same way under non-authoritative research profiles) | episodes without a provider failure | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Conditional, descriptive; the end-to-end rate is verified_success. |
| `verifier_outcomes_without_provider_failure.partial_success` | proportion | PARTIAL verdict (same verifier-profile caveat as PASS) | episodes without a provider failure | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Conditional, descriptive. |
| `verifier_outcomes_without_provider_failure.verifier_error_rate` | proportion | verdict ERROR or INCONCLUSIVE | episodes without a provider failure | fraction | episode | wilson (episode-level, descriptive) | not_applicable | Conditional, descriptive. |
| `task_clustered` | group | n/a | n/a | n/a | none | none | not_applicable | Task-cluster bootstrap intervals (unit: independent task). |
| `task_clustered.verified_success` | cluster_interval | PASS verdict (authoritative under the default v3_full verifier profile; counted the same way under non-authoritative research profiles) | all episodes of the resampled tasks | fraction or reward | task_cluster | task-cluster percentile bootstrap (pooled ratio) | not_applicable | Interval width reflects independent tasks, not episodes. |
| `task_clustered.false_positive_reward_rate` | cluster_interval | rewarded and not truly successful | all episodes of the resampled tasks | fraction or reward | task_cluster | task-cluster percentile bootstrap (pooled ratio) | not_applicable | Interval width reflects independent tasks, not episodes. |
| `task_clustered.hidden_security_pass_rate` | cluster_interval | hidden-security check PASS | executed hidden-security checks of the resampled tasks | fraction or reward | task_cluster | task-cluster percentile bootstrap (pooled ratio) | excluded_with_coverage | Interval width reflects independent tasks, not episodes. |
| `task_clustered.regression_failure_rate` | cluster_interval | regression check FAIL | all episodes of the resampled tasks (v0.2 denominator) | fraction or reward | task_cluster | task-cluster percentile bootstrap (pooled ratio) | not_applicable | Interval width reflects independent tasks, not episodes. |
| `task_clustered.mean_reward` | cluster_interval | final reward | all episodes of the resampled tasks | fraction or reward | task_cluster | task-cluster percentile bootstrap (pooled ratio) | not_applicable | Interval width reflects independent tasks, not episodes. |
| `task_clustered.provider_failure_rate` | cluster_interval | provider failure | all episodes of the resampled tasks | fraction or reward | task_cluster | task-cluster percentile bootstrap (pooled ratio) | not_applicable | Interval width reflects independent tasks, not episodes. |
| `tool_calls_per_attempt` | mean | tool calls | all scheduled episodes | calls | episode | none | not_applicable | v0.2 key; equals tool_calls.per_attempt. |
| `tool_calls_per_success` | ratio | tool calls of PASS episodes | PASS episodes | calls | episode | none | not_applicable | v0.2 key; null with no successes; equals tool_calls.per_success. |
| `tool_calls` | group | n/a | n/a | n/a | none | none | not_applicable | Tool-call efficiency. |
| `tool_calls.total` | total | tool calls | n/a | calls | episode | none | not_applicable | All recorded tool calls. |
| `tool_calls.per_attempt` | ratio | tool calls | all scheduled episodes | calls | episode | none | not_applicable | Null with no episodes. |
| `tool_calls.per_success` | ratio | tool calls of PASS episodes | PASS episodes | calls | episode | none | not_applicable | Null with no successes. |
| `action_rejection_rate` | ratio | rejected tool calls | tool calls | fraction | episode | none | not_applicable | Null with no tool calls. |
| `token_usage` | group | n/a | n/a | n/a | none | none | not_applicable | v0.2 token block: null unless every episode reported complete usage and the total is > 0 (so null for records before v0.3 telemetry). See tokens.*. |
| `token_usage.prompt_tokens` | total | prompt tokens | n/a | tokens | episode | none | null_if_any_missing | As tokens.prompt_total. |
| `token_usage.completion_tokens` | total | completion tokens | n/a | tokens | episode | none | null_if_any_missing | As tokens.completion_total. |
| `token_usage.total_tokens` | total | prompt + completion tokens | n/a | tokens | episode | none | null_if_any_missing | As tokens.total. |
| `token_usage.tokens_per_episode` | ratio | total tokens | all scheduled episodes | tokens | episode | none | null_if_any_missing | As tokens.per_attempt. |
| `token_usage.tokens_per_verified_success` | ratio | total tokens | PASS episodes | tokens | episode | none | null_if_any_missing | As tokens.per_verified_success. |
| `tokens` | group | n/a | n/a | n/a | none | none | not_applicable | Token usage with coverage. Totals need every episode; any episode without complete usage (including provider failures) makes them null. |
| `tokens.prompt_total` | total | prompt tokens | n/a | tokens | episode | none | null_if_any_missing | Null unless every episode reported complete usage. |
| `tokens.completion_total` | total | completion tokens | n/a | tokens | episode | none | null_if_any_missing | Null unless every episode reported complete usage. |
| `tokens.total` | total | prompt + completion tokens | n/a | tokens | episode | none | null_if_any_missing | Null unless every episode reported complete usage. |
| `tokens.per_attempt` | ratio | tokens.total | all scheduled episodes | tokens | episode | none | null_if_any_missing | Null when tokens.total is null or there are no episodes. |
| `tokens.per_verified_success` | ratio | tokens.total | PASS episodes | tokens | episode | none | null_if_any_missing | Null when tokens.total is null or there are no successes. |
| `tokens.verified_successes_per_1000_tokens` | ratio | PASS episodes x 1000 | tokens.total | successes per 1K tokens | episode | none | null_if_any_missing | Secondary efficiency measure. *(secondary)* |
| `tokens.episodes_with_complete_usage` | count | episodes whose every request reported usage | n/a | episodes | episode | none | not_applicable | Coverage numerator. |
| `tokens.n_episodes` | count | episodes | n/a | episodes | episode | none | not_applicable | Coverage denominator. |
| `cost_per_verified_success_usd` | ratio | cost.total_usd | PASS episodes | usd | episode | none | null_if_any_missing | v0.2 key; equals cost.per_verified_success_usd. Never 0 for unknown cost. |
| `cost` | group | n/a | n/a | n/a | none | none | not_applicable | Cost with coverage. Any episode with unknown cost (including provider failures) makes the total unavailable; the known costs then form a lower bound. |
| `cost.total_usd` | total | sum of episode costs | n/a | usd | episode | none | null_if_any_missing | Null unless cost.status is complete. 0.0 only for explicitly free providers. |
| `cost.per_attempt_usd` | ratio | cost.total_usd | all scheduled episodes | usd | episode | none | null_if_any_missing | Null if cost.total_usd is null or there are no episodes. |
| `cost.per_verified_success_usd` | ratio | cost.total_usd | PASS episodes | usd | episode | none | null_if_any_missing | Null if cost.total_usd is null or there are no successes. |
| `cost.lower_bound_usd` | total | sum of known episode costs | n/a | usd | episode | none | excluded_with_coverage | Only when cost.status is partial: a LOWER BOUND on total spend, never the total. |
| `cost.status` | label | n/a | n/a | label | none | none | not_applicable | complete \| partial (lower bound published) \| unknown (no known cost) \| overflow (finite costs whose sum is not representable; no total or bound) \| no_episodes. |
| `cost.episodes_with_known_cost` | count | episodes with a known cost | n/a | episodes | episode | none | not_applicable | Coverage numerator (unchanged by overflow). |
| `cost.n_episodes` | count | episodes | n/a | episodes | episode | none | not_applicable | Coverage denominator. |
| `latency` | group | n/a | n/a | n/a | none | none | not_applicable | Latency percentile summaries. |
| `latency.request_seconds` | percentiles | per-request generation seconds (incl. retries) | requests with a recorded duration | seconds | request | none | excluded_with_coverage | Pooled over requests. |
| `latency.episode_seconds` | percentiles | episode wall time (rollout duration_seconds) | episodes | seconds | episode | none | not_applicable | One value per episode. |
| `latency.generation_seconds_per_episode` | percentiles | per-episode generation seconds | episodes that recorded it | seconds | episode | none | excluded_with_coverage | One value per episode. |
| `mean_duration_seconds` | mean | episode wall time | all scheduled episodes | seconds | episode | none | not_applicable | v0.2 key; null with no episodes. |
| `mean_generation_seconds` | mean | per-episode generation seconds | episodes that recorded it | seconds | episode | none | excluded_with_coverage | v0.2 key; see latency.* for percentiles and coverage. |
| `environment_compute_seconds` | total | sum of verification check durations | n/a | seconds | episode | none | not_applicable | Verifier compute time. |
| `finish_reasons` | group | n/a | n/a | n/a | none | none | not_applicable | Model finish reasons over successful generation requests. |
| `finish_reasons.counts` | group | n/a | n/a | n/a | none | none | not_applicable | Counts per finish reason. |
| `finish_reasons.counts.*` | count | successful requests with this finish reason | n/a | requests | episode | none | not_applicable | Includes not_reported. |
| `finish_reasons.truncated_length` | count | successful requests with finish_reason 'length' | n/a | requests | episode | none | not_applicable | Truncated generations. |
| `telemetry_coverage` | group | n/a | n/a | n/a | none | none | not_applicable | Coverage of each measurement over all scheduled episodes. |
| `telemetry_coverage.generation_telemetry` | coverage | episodes recorded with v0.3 generation telemetry | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.usage` | coverage | episodes whose every request reported usage | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.cost` | coverage | episodes with a known cost | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.request_latency` | coverage | episodes with a duration for every request | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.attempts` | coverage | episodes with known attempt counts for every request | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.hidden_security_check` | coverage | episodes where the hidden-security check executed | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.regression_check` | coverage | episodes with a usable (PASS or FAIL) regression result | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |
| `telemetry_coverage.regression_check_unusable` | coverage | episodes whose regression check ran but was ERROR or INCONCLUSIVE | all scheduled episodes | episodes | episode | none | not_applicable | covered, n, fraction (null with no episodes). |

## Record schemas

### `benchmark_aggregate`

| Field | Meaning |
|---|---|
| `overall` | summarize() over all episodes (see Metrics). |
| `by_split` | summarize() per split. |
| `by_family` | summarize() per task family. |
| `by_difficulty` | summarize() per difficulty. |
| `by_seed` | summarize() per seed (descriptive; seeds are not independent tasks). |
| `per_task` | List of per-episode rows (record per_episode_row). |
| `task_level` | List of per-task rows (record task_level_row). |
| `reward_threshold_sensitivity` | List of threshold rows (record threshold_sensitivity_row). |
| `statistical_units` | Text: which units each interval treats as independent. |
| `evidence` | Evidence label: scripted pipeline validation vs model evaluation, n_tasks, seeds. |
| `metrics_schema_version` | Metric contract version. |
| `report` | Report tier block (record report_block). |
| `split_policy` | Task split policy id and version. |

### `report_block`

| Field | Meaning |
|---|---|
| `tier` | development \| final. |
| `bootstrap_samples` | Resamples used for every bootstrap interval. |
| `final_min_bootstrap_samples` | Minimum resamples a final report requires (10,000). |
| `note` | Human-readable statement of the tier and its requirements. |

### `per_episode_row`

| Field | Meaning |
|---|---|
| `task_id` | Task. |
| `family` | Task family. |
| `split` | Split. |
| `difficulty` | Difficulty. |
| `policy` | Scripted demo policy (null for model providers). |
| `seed` | Seed (repeated seeds are not independent tasks). |
| `verdict` | Verifier verdict. |
| `reward` | Final reward. |
| `tool_calls` | Tool calls. |
| `rejected_actions` | Rejected tool calls. |
| `failure_reasons` | Verifier failure reasons. |
| `rollout_id` | Rollout id. |
| `verification_id` | Verification id. |
| `termination_reason` | Why the episode ended (provider_timeout/provider_error = provider failure). |
| `provider_failure` | Provider failure kind or null. |
| `duration_seconds` | Episode wall time. |
| `cost_usd` | Episode cost; null when unknown. |
| `tokens` | Episode tokens; null unless every request reported usage. |

### `task_level_row`

| Field | Meaning |
|---|---|
| `task_id` | Task. |
| `family` | Task family. |
| `template` | Task template. |
| `split` | Split. |
| `difficulty` | Difficulty. |
| `n_episodes` | Episodes of this task. |
| `seeds` | Seeds run. |
| `verified_success_rate` | PASS verdicts / episodes of this task. |
| `mean_reward` | Mean final reward of this task. |
| `verdicts` | Verdict counts. |
| `outcome_consistent_across_episodes` | True if every episode got the same verdict. |
| `provider_failures` | Episodes of this task that ended in a provider failure. |

### `threshold_sensitivity_row`

| Field | Meaning |
|---|---|
| `threshold` | Reward threshold. |
| `reward_positive_rate` | Proportion: reward >= threshold / all episodes. |
| `false_positive_reward_rate` | Proportion: rewarded and not truly successful / all episodes. |
| `false_positive_reward_rate_task_clustered` | Task-cluster interval of the same. |
| `false_positive_reward_label_source` | independent_labels \| mixed \| verifier_derived. |

### `experiment_g_arm_metrics`

| Field | Meaning |
|---|---|
| `n_tasks` | Independent tasks. |
| `n_episodes` | Episodes. |
| `verified_success` | Proportion (see verified_success). |
| `verified_success_task_clustered` | Task-cluster interval. |
| `hidden_security_pass_rate` | Proportion over executed checks. |
| `hidden_security_pass_rate_task_clustered` | Task-cluster interval. |
| `regression_failure_rate` | Proportion over all episodes. |
| `regression_failure_rate_task_clustered` | Task-cluster interval. |
| `false_positive_reward_rate` | Proportion (see label source). |
| `false_positive_reward_rate_task_clustered` | Task-cluster interval. |
| `tool_calls_per_verified_success` | Tool calls / PASS episodes. |
| `mean_episode_seconds` | Mean episode wall time. |
| `mean_generation_seconds` | Mean per-episode generation seconds. |
| `token_usage` | v0.2 token block. |
| `tokens` | Token block (see tokens.*). |
| `cost` | Cost block (see cost.*). |
| `cost_per_verified_success_usd` | Cost / PASS episodes or null. |
| `latency` | Latency block (see latency.*). |
| `provider_failure_rate` | Proportion of provider failures. |
| `false_positive_reward_label_source` | Label source. |
| `telemetry_coverage` | Coverage block. |
| `by_family` | Verified success per family. |
| `by_difficulty` | Verified success per difficulty. |
| `by_seed` | Verified-success rate per seed. |
| `evidence` | Evidence label. |

### `benchmark_document`

| Field | Meaning |
|---|---|
| `report_type` | Always 'benchmark'. |
| `traceforge_version` | TraceForge version. |
| `benchmark_id` | Evaluation id. |
| `generated_at` | UTC timestamp. |
| `config` | Benchmark configuration. |
| `config_hash` | Configuration hash. |
| `duration_seconds` | Benchmark wall time. |
| `cancelled` | Whether the run was cancelled. |
| `demo_provider` | Whether the scripted demo provider was used. |
| `disclaimer` | Demo-provider disclaimer or null. |
| `run_mode` | official \| diagnostic. |
| `official` | Official and leakage checks passed. |
| `evidence` | Evidence label. |
| `metrics_schema_version` | Metric contract version. |
| `report_tier` | development \| final. |
| `leakage` | Leakage / lineage report. |
| `metrics` | Benchmark aggregate (record benchmark_aggregate). |
| `artifacts` | Artifact references. |
| `extra` | Run extras. |
| `non_finite_fields` | Optional: paths whose non-finite values were written as null. |
