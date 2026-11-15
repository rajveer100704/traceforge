# Comparison contract

Schema version **1**. Generated from `src/traceforge/modules/evaluation/comparison_contract.py`; do not edit by hand (`tests/unit/test_comparison_contract.py` fails on drift).

## Units and seeds

- **Episode pair**: key `(task_id, task_version, episode_seed)`; one baseline and one candidate episode.
- **Task instance**: `task_id@task_version`; secondary, descriptive cluster.
- **Structural scenario**: templates with one authoritative oracle identity; the **primary resampling unit**. Seeds and instances never add clusters.
- **Family**: reporting group only; never resampled.
- Seeds: the benchmark **episode seed** is part of the pair key; the **provider generation seed** (`provider.seed`) must match for official comparisons; the **bootstrap seed** (`bootstrap_seed`) only drives resampling.

## Estimands and intervals

- Mean metrics: `sum of paired differences / usable pairs` (equals the difference of the arms' PR 1B pooled estimands; scenarios are weighted by their pairs).
- Ratio metrics: `sum(num_C)/sum(den_C) - sum(num_B)/sum(den_B)`.
- Primary interval: single-stage percentile bootstrap resampling structural scenarios (`cluster_bootstrap` on per-scenario paired differences; `paired_cluster_ratio_bootstrap` with one shared draw for both arms). A ratio replicate with a zero denominator makes the interval null, with the count reported.
- Secondary `instance_sensitivity`: the same with task-instance clusters; anti-conservative when instances share a scenario; never interpreted.
- No episode-level i.i.d. intervals, no family-cluster intervals, no p-values.
- Unknown values exclude the pair from that metric only (`coverage`). Cost and token differences need complete coverage in both arms; lower bounds are never differenced.

## Primary endpoint and evidence

- Primary endpoint: `by_split.private_test` / `verified_success`. Without private_test pairs the endpoint is unavailable and pooled overall is **not** substituted.
- 20 structural scenarios is an eligibility gate for a directional statement, not proof of statistical validity, power or generalisation. Fewer than 10 scenarios: insufficient_scenarios; 10-19: exploratory_only.
- `report_tier` (rigour), `evidence_tier` (what may be claimed) and `evidence_source` (model comparison or scripted pipeline validation) are separate fields. A final report may carry `exploratory_only`; it then states that no directional claim is supported.
- A directional `improvement`/`regression` requires: evidence tier `directional_eligible`, an official strictly matched comparison, the primary endpoint and metric, and a primary interval excluding 0.
- public_test: legacy public_test scenarios also appear in train; this measures in-distribution performance, not held-out structural generalisation.

Evidence tiers (first that applies): `diagnostic`, `no_primary_endpoint`, `insufficient_scenarios`, `exploratory_only`, `directional_eligible`.

Interpretation values (first that applies): `not_comparable`, `undefined`, `diagnostic_only`, `secondary`, `insufficient_scenarios`, `exploratory_only`, `improvement`, `regression`, `no_detectable_difference`.

## Metrics

| Key | Class | Kind | Direction | Population | Numerator | Denominator | Comparable when | PR 1B metric |
|---|---|---|---|---|---|---|---|---|
| `verified_success` | A | mean | higher | matched episode pairs with a known value in both arms | PASS verdicts | pairs | always | verified_success |
| `partial_success` | A | mean | neutral | matched episode pairs with a known value in both arms | PARTIAL verdicts | pairs | always | partial_success |
| `verifier_error_rate` | A | mean | lower | matched episode pairs with a known value in both arms | ERROR/INCONCLUSIVE | pairs | always | verifier_error_rate |
| `reward_positive_rate` | A | mean | neutral | matched episode pairs with a known value in both arms | reward >= 0.5 | pairs | reward policy hashes equal | reward_positive_rate |
| `false_positive_reward_rate` | A | mean | lower | matched episode pairs with a known value in both arms | rewarded and not an authoritative PASS (reward-verifier disagreement) | pairs | reward policy hashes equal | false_positive_reward_rate |
| `regression_failure_rate` | A | mean | lower | matched episode pairs with a known value in both arms | regression FAIL | pairs | always | regression_failure_rate |
| `regression_failure_rate_executed` | A | ratio | lower | matched episode pairs with a known value in both arms | regression FAIL | regression PASS or FAIL | always | regression_failure_rate_executed |
| `hidden_security_pass_rate` | A | ratio | higher | matched episode pairs with a known value in both arms | hidden-security PASS | executed hidden-security checks | always | hidden_security_pass_rate |
| `provider_failures.rate` | A | mean | lower | matched episode pairs with a known value in both arms | provider failures | pairs | always | provider_failures.rate |
| `mean_reward` | A | mean | higher | matched episode pairs with a known value in both arms | final reward | pairs | reward policy hashes equal | mean_reward |
| `tool_calls_per_attempt` | A | mean | lower | matched episode pairs with a known value in both arms | tool calls | pairs | always | tool_calls_per_attempt |
| `tool_calls_per_success` | A | ratio | lower | matched episode pairs with a known value in both arms | tool calls of PASS episodes | PASS episodes | each arm has >= 1 PASS | tool_calls_per_success |
| `mean_duration_seconds` | A | mean | lower | matched episode pairs with a known value in both arms | episode wall time | pairs | always | mean_duration_seconds |
| `cost.per_attempt_usd` | A | mean | lower | matched episode pairs with a known value in both arms | episode cost | pairs | cost complete in both arms | cost.per_attempt_usd |
| `cost.per_verified_success_usd` | A | ratio | lower | matched episode pairs with a known value in both arms | episode cost | PASS episodes | cost complete in both arms and >= 1 PASS each | cost.per_verified_success_usd |
| `tokens.per_attempt` | A | mean | lower | matched episode pairs with a known value in both arms | prompt + completion tokens | pairs | usage complete in both arms | tokens.per_attempt |
| `tokens.per_verified_success` | A | ratio | lower | matched episode pairs with a known value in both arms | prompt + completion tokens | PASS episodes | usage complete in both arms and >= 1 PASS each | tokens.per_verified_success |
| `paired_conditional.verified_success` | B | mean | higher | pairs where neither arm had a provider failure | PASS verdicts | those pairs | >= 1 such pair | none (comparison-only) |
| `provider_retries_per_episode` | B | mean | lower | matched pairs | provider retries | pairs | attempt counts known for every pair in both arms | none (comparison-only) |

Class C (descriptive): latency `request_seconds`, `episode_seconds` p50/p90/p95 per arm with a point difference; no interval.

## Matching problems

| Code | Severity | Meaning |
|---|---|---|
| `invalid_record` | hard | a record is malformed or lacks an episode seed |
| `duplicate_observation` | hard | the same (task_id, task_version, seed) occurs twice |
| `task_version_mismatch` | hard | one task_id appears with different task versions |
| `unknown_task` | hard | task_id@task_version is unknown to the task registry |
| `task_identity_mismatch` | hard | record metadata or environment disagrees with registry |
| `scenario_unresolved` | hard | no authoritative scenario for this task |
| `scenario_unverifiable` | hard | task version has no authoritative scenario source |
| `oracle_identity_mismatch` | hard | recomputed oracle fingerprint differs from the pin |
| `scenario_mismatch` | hard | the arms or the metadata disagree on a task's scenario |
| `scenario_identity_collision` | hard | distinct scenarios share one oracle fingerprint |
| `verifier_mismatch` | hard | verifier profile or version differs |
| `benchmark_identity_mismatch` | hard | benchmark identity differs between arms |
| `never_variable_config` | hard | a NEVER_VARIABLE configuration field differs |
| `empty_run` | hard | a run has no episodes |
| `invalid_bundle` | hard | bundle integrity, config or evaluation id cannot be verified |
| `unknown_evaluation` | hard | the evaluation run does not exist |
| `coverage_mismatch` | strict | episode keys differ between arms |
| `reward_policy_mismatch` | strict | reward policy hash differs |
| `provider_seed_mismatch` | strict | provider.seed differs |
| `config_mismatch` | strict | a must-match configuration field differs |
| `final_tier_requirement` | hard | a final report requirement is not met |
| `non_authoritative` | not_official | a record is not v3_full/authoritative |
| `run_not_completed` | not_official | a run did not complete |
| `run_not_official` | not_official | a run is diagnostic or failed its leakage checks |
| `bundle_source` | not_official | bundle-loaded runs are always diagnostic |

Severity: `hard` refuses in every mode; `strict` refuses under strict coverage and is a warning under intersection; `not_official` keeps the comparison non-official.

## Legacy structural scenarios (registry version 1)

| Task version | Family | Template | Scenario | Oracle fingerprint | Reason |
|---|---|---|---|---|---|
| 1.0.0 | sql_injection | t0 | `sql_injection/t0` | `1861dd791ec6686d…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.0.0 | sql_injection | t1 | `sql_injection/t1` | `6923989bb07facf5…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.0.0 | sql_injection | t2 | `sql_injection/t2` | `eb47f8bbaeae1c35…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.0.0 | access_control | t0 | `access_control/t0` | `35e875da7eb4990a…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.0.0 | access_control | t1 | `access_control/t1` | `4ab37ac5ddbed7ed…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.0.0 | access_control | t2 | `access_control/t2` | `137b14844f2f66cd…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.0.0 | ssrf | t0 | `ssrf/t0+t1` | `12d96a023f5ec283…` | t0 and t1 render the same visible and hidden oracle tests (VALIDATE_VISIBLE / VALIDATE_HIDDEN in families/ssrf.py) under identical verifier configuration, so their normalised oracle fingerprints are equal; only the vulnerable module differs. Template ids and task identities stay separate; only the structural scenario is shared. |
| 1.0.0 | ssrf | t1 | `ssrf/t0+t1` | `12d96a023f5ec283…` | t0 and t1 render the same visible and hidden oracle tests (VALIDATE_VISIBLE / VALIDATE_HIDDEN in families/ssrf.py) under identical verifier configuration, so their normalised oracle fingerprints are equal; only the vulnerable module differs. Template ids and task identities stay separate; only the structural scenario is shared. |
| 1.0.0 | ssrf | t2 | `ssrf/t2` | `0441abfecae4b242…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.1.0 | sql_injection | t0 | `sql_injection/t0` | `c112395acae120e7…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.1.0 | sql_injection | t1 | `sql_injection/t1` | `70c120aca463af93…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.1.0 | sql_injection | t2 | `sql_injection/t2` | `6f0245bd1d4c1ea1…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.1.0 | access_control | t0 | `access_control/t0` | `162e77cbd5d8ee9e…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.1.0 | access_control | t1 | `access_control/t1` | `3f2e1fc6282a2f5c…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.1.0 | access_control | t2 | `access_control/t2` | `572b2a28027ba5c5…` | own oracle: hidden tests, visible tests and verifier configuration not shared |
| 1.1.0 | ssrf | t0 | `ssrf/t0+t1` | `3b041f2ba368b865…` | t0 and t1 render the same visible and hidden oracle tests (VALIDATE_VISIBLE / VALIDATE_HIDDEN in families/ssrf.py) under identical verifier configuration, so their normalised oracle fingerprints are equal; only the vulnerable module differs. Template ids and task identities stay separate; only the structural scenario is shared. |
| 1.1.0 | ssrf | t1 | `ssrf/t0+t1` | `3b041f2ba368b865…` | t0 and t1 render the same visible and hidden oracle tests (VALIDATE_VISIBLE / VALIDATE_HIDDEN in families/ssrf.py) under identical verifier configuration, so their normalised oracle fingerprints are equal; only the vulnerable module differs. Template ids and task identities stay separate; only the structural scenario is shared. |
| 1.1.0 | ssrf | t2 | `ssrf/t2` | `6fff62e9551f7cdd…` | own oracle: hidden tests, visible tests and verifier configuration not shared |

## Limitations (stated in every report)

- Structural scenarios are assumed independent; they share task families, authors, the sandbox harness and the verifier.
- Episode seeds are crossed with tasks (seed s is used for every task); seed-level effects are not modelled.
- Paired episode seeds do not guarantee equivalent provider-generation randomness; providers may ignore requested seeds.
- In the legacy benchmark, public_test scenarios also appear in train: public results measure in-distribution performance; only private_test holds out structure.
- Pooled estimands weight scenarios by their number of episode pairs.
- PR 1B per-arm task_clustered intervals cluster by task instance and are not shown here.
- paired_conditional metrics select on both arms' outcomes and may be biased; diagnostic.
- The benchmark is synthetic and offline: results do not establish real-world security performance.

## Record schemas

### `comparison_document`

| Field | Meaning |
|---|---|
| `report_type` | Always 'benchmark_comparison'. |
| `comparison_schema_version` | Comparison contract version. |
| `comparison_id` | Random id of this comparison (uuid4). |
| `comparison_fingerprint` | sha256 of the request and both arms' record digests. |
| `generated_at` | UTC timestamp. |
| `traceforge_version` | TraceForge version. |
| `request` | The comparison request. |
| `report_tier` | development \| final (report rigour). |
| `evidence_tier` | What claims the comparison may support (see Evidence tiers). |
| `evidence_banner` | Human-readable statement of the evidence tier. |
| `evidence_source` | model_comparison \| pipeline_validation. |
| `learned_model_evidence` | False whenever a scripted provider is involved. |
| `seeds` | Episode seeds, provider seeds and the bootstrap seed (record seeds). |
| `baseline` | Baseline run (record run). |
| `candidate` | Candidate run (record run). |
| `matching` | Matching result (record matching). |
| `protocol` | Shared protocol identity (record protocol). |
| `primary_endpoint` | Primary endpoint (record primary_endpoint). |
| `groups` | overall, by_split, by_family, by_difficulty (record group). |
| `per_scenario` | One row per structural scenario (record per_scenario). |
| `per_task` | One row per task instance (record per_task). |
| `pairs` | Optional (include_pairs): one row per episode pair. |
| `latency` | Descriptive Class C latency comparison. |
| `cost_coverage` | Per-arm cost coverage and comparability. |
| `usage_coverage` | Per-arm token usage coverage and comparability. |
| `statistics` | Methods, estimands, thresholds. |
| `report` | Report tier block. |
| `warnings` | List of {code, detail}. |
| `limitations` | Statements that apply to every comparison. |
| `non_finite_fields` | Optional: paths of non-finite values written as null. |

### `seeds`

| Field | Meaning |
|---|---|
| `episode_seeds` | Benchmark episode seeds (pair-key component). |
| `provider_seed` | {baseline, candidate, matched, sent_to_provider}: configured BenchmarkConfig.provider.seed (must match for official comparisons) and which seed the runner actually sends to the provider. |
| `bootstrap_seed` | Statistical resampling seed (independent of both runs). |
| `provider_seed_note` | Providers may ignore requested seeds. |

### `run`

| Field | Meaning |
|---|---|
| `evaluation_id` | Evaluation run id. |
| `source` | evaluation \| bundle. |
| `bundle_dir` | Bundle directory (bundle source) or null. |
| `config_name` | Bundle config name or null. |
| `name` | Benchmark config name. |
| `config_hash` | BenchmarkConfig hash (not used for matching: it includes the name). |
| `run_mode` | official \| diagnostic. |
| `official` | Run official and leakage checks passed. |
| `status` | Run status. |
| `provider_kind` | Provider kind. |
| `model_id` | Model id. |
| `policy` | Scripted policy or null. |
| `verifier_profile` | Verifier profile of the records. |
| `verifier_version` | Verifier version of the records. |
| `reward_policy` | {id, version, hash} of the records. |
| `n_episodes` | Episodes loaded. |

### `matching`

| Field | Meaning |
|---|---|
| `status` | matched \| diagnostic \| refused. |
| `official` | True only for a strictly matched comparison with no not_official problem. |
| `coverage_policy` | strict \| intersection. |
| `problems` | List of {code, severity, detail, keys}. |
| `excluded` | List of {key, side, reason} (intersection only). |
| `config_differences` | List of {path, baseline, candidate, allowed}. |
| `n_pairs` | Matched episode pairs. |
| `n_task_instances` | Distinct task_id@task_version. |
| `n_structural_scenarios` | Distinct structural scenarios (independent units). |
| `n_families` | Distinct task families. |

### `protocol`

| Field | Meaning |
|---|---|
| `task_versions` | Task versions compared. |
| `scenario_source` | legacy_registry \| catalogue. |
| `scenario_registry_version` | Scenario registry version. |
| `oracle_normalisation_version` | Oracle fingerprint normalisation version. |
| `benchmark_identity` | Benchmark identity (catalogue runs) or null (legacy). |
| `split_policy` | Task split policy. |
| `verifier` | {profile, version, authoritative}. |
| `reward_policy` | {id, version, hash}. |
| `reward_threshold` | Reward-positive threshold. |

### `primary_endpoint`

| Field | Meaning |
|---|---|
| `status` | available \| unavailable. |
| `group` | by_split.private_test or null (never substituted). |
| `metric` | verified_success. |
| `reason` | Why unavailable, else null. |

### `group`

| Field | Meaning |
|---|---|
| `role` | primary \| secondary. |
| `in_distribution_note` | public_test overlap note, else null. |
| `counts` | {n_pairs, n_task_instances, n_structural_scenarios, n_families, instances_per_scenario, seeds_per_instance, scenario_weights}. |
| `scenario_tier` | insufficient_scenarios \| exploratory_only \| directional_eligible. |
| `metrics` | metric key -> record metric_comparison. |

### `metric_comparison`

| Field | Meaning |
|---|---|
| `metric` | Metric key. |
| `class` | A \| B (see Metrics). |
| `kind` | mean \| ratio. |
| `direction` | higher \| lower \| neutral (which way is favourable). |
| `category` | outcome \| safety \| reward \| reliability \| efficiency \| cost. |
| `population` | Pairs the metric is computed over. |
| `baseline` | Baseline arm estimate (record arm_estimate). |
| `candidate` | Candidate arm estimate (record arm_estimate). |
| `difference` | Primary scenario-clustered difference (record difference). |
| `instance_sensitivity` | Secondary task-instance-clustered interval (record sensitivity); never interpreted. |
| `relative_difference` | difference / baseline (non-negative metrics, baseline > 0). |
| `coverage` | {pairs_used, pairs_total}. |
| `scenario_tier` | From this metric's n_clusters. |
| `interpretation` | See Interpretation. |
| `estimate_direction` | favourable \| unfavourable \| none (sign only, never a claim). |
| `not_comparable_reason` | Why not comparable, else null. |

### `difference`

| Field | Meaning |
|---|---|
| `estimate` | Candidate minus baseline. |
| `ci_low` | Percentile interval low (null if undefined). |
| `ci_high` | Percentile interval high (null if undefined). |
| `confidence` | Confidence level. |
| `samples` | Bootstrap resamples. |
| `seed` | Bootstrap seed. |
| `method` | Bootstrap method. |
| `cluster_unit` | structural_scenario (primary) \| task_instance (sensitivity). |
| `n_clusters` | Clusters with data for this metric. |
| `n_observations` | Pairs used (mean metrics) or null (ratio metrics). |
| `undefined_replicates` | Ratio replicates with a zero denominator (0 for mean metrics). |
| `note` | Why the interval is undefined, else null. |

### `sensitivity`

| Field | Meaning |
|---|---|
| `estimate` | Same estimate as the primary difference. |
| `ci_low` | Instance-clustered interval low. |
| `ci_high` | Instance-clustered interval high. |
| `confidence` | Confidence level. |
| `samples` | Bootstrap resamples. |
| `seed` | Bootstrap seed. |
| `method` | Bootstrap method. |
| `cluster_unit` | task_instance. |
| `n_clusters` | Task instances with data. |
| `n_observations` | Pairs used (mean metrics) or null. |
| `undefined_replicates` | Ratio replicates with a zero denominator. |
| `note` | Why the interval is undefined, else null. |
| `width_ratio` | Instance-clustered width / scenario-clustered width. |
| `label` | Why this interval is never interpreted. |

### `arm_estimate`

| Field | Meaning |
|---|---|
| `estimate` | numerator / denominator, or null with an empty denominator. |
| `numerator` | Sum of per-episode numerators over used pairs. |
| `denominator` | Pairs used (mean metrics) or summed denominators (ratio metrics). |
| `n` | Denominator as an integer. |

### `latency_entry`

| Field | Meaning |
|---|---|
| `baseline` | {n, p50, p90, p95, max} (PR 1B percentiles). |
| `candidate` | {n, p50, p90, p95, max}. |
| `difference` | {p50, p90, p95}: candidate minus baseline; descriptive, no interval. |

### `coverage_block`

| Field | Meaning |
|---|---|
| `baseline` | Record coverage_arm. |
| `candidate` | Record coverage_arm. |
| `comparable` | True only when both arms are complete. |
| `reason` | Why not comparable, else null. |

### `coverage_arm`

| Field | Meaning |
|---|---|
| `status` | complete \| partial \| unknown \| overflow \| no_episodes. |
| `covered` | Episodes with known cost / complete usage. |
| `n` | Episodes. |
| `total` | Total (null unless complete). |
| `lower_bound` | Cost lower bound (partial coverage only; never differenced). |

### `statistics`

| Field | Meaning |
|---|---|
| `primary_cluster_unit` | structural_scenario. |
| `secondary_cluster_unit` | task_instance. |
| `estimands` | Mean, ratio and paired-conditional estimands. |
| `method` | Bootstrap methods. |
| `samples` | Bootstrap resamples. |
| `bootstrap_seed` | Bootstrap seed. |
| `confidence` | Confidence level. |
| `thresholds` | {insufficient_below, exploratory_below}. |
| `eligibility_note` | What the 20-scenario gate does and does not mean. |

### `report`

| Field | Meaning |
|---|---|
| `tier` | development \| final. |
| `bootstrap_samples` | Resamples used. |
| `final_min_bootstrap_samples` | 10,000. |
| `final_min_primary_scenarios` | 10. |
| `note` | Final-tier requirements. |

### `pair`

| Field | Meaning |
|---|---|
| `key` | [task_id, task_version, episode_seed]. |
| `scenario_id` | Structural scenario. |
| `family` | Task family. |
| `template` | Template. |
| `split` | Split. |
| `difficulty` | Difficulty. |
| `baseline_rollout_id` | Baseline rollout. |
| `candidate_rollout_id` | Candidate rollout. |
| `values` | Mean-metric values {baseline, candidate}. |

### `refusal_document`

| Field | Meaning |
|---|---|
| `report_type` | Always 'benchmark_comparison_refusal'. |
| `comparison_schema_version` | Comparison contract version. |
| `generated_at` | UTC timestamp. |
| `traceforge_version` | TraceForge version. |
| `request` | The comparison request. |
| `baseline` | Baseline run (record run) or null when it could not be loaded. |
| `candidate` | Candidate run (record run) or null when it could not be loaded. |
| `matching` | Matching block with status 'refused' and its problems. |
| `non_finite_fields` | Optional. |

### `per_scenario`

| Field | Meaning |
|---|---|
| `scenario_id` | Structural scenario. |
| `family` | Task family. |
| `templates` | Templates mapped to this scenario. |
| `oracle_fingerprint` | Authoritative oracle fingerprint. |
| `source` | legacy_registry \| catalogue. |
| `reason` | Why these templates form one scenario (legacy registry), else null. |
| `task_instances` | Task instances in this scenario. |
| `n_pairs` | Episode pairs. |
| `verdict_transitions` | 'BASELINE->CANDIDATE' verdict counts. |
| `metrics` | verified_success {baseline_mean, candidate_mean, difference}. |
| `change` | improved \| regressed \| unchanged \| mixed (descriptive). |

### `per_task`

| Field | Meaning |
|---|---|
| `task_id` | Task. |
| `task_version` | Task version. |
| `scenario_id` | Structural scenario. |
| `family` | Task family. |
| `template` | Template. |
| `split` | Split (registry). |
| `difficulty` | Difficulty (registry). |
| `n_pairs` | Episode pairs. |
| `seeds` | Episode seeds. |
| `verdict_transitions` | 'BASELINE->CANDIDATE' verdict counts. |
| `metrics` | verified_success {baseline_mean, candidate_mean, difference}. |
| `change` | improved \| regressed \| unchanged \| mixed (descriptive). |
