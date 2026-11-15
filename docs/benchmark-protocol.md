# Benchmark protocol

## Task families (v0.1, synthetic, offline)

| Family | Templates (difficulty) | Security property (hidden tests) | Behaviour preserved (hidden functional tests) |
|---|---|---|---|
| `sql_injection` | t0 f-string lookup (easy), t1 LIKE concat (medium), t2 two-param `.format` (hard, **private**) | tautologies, comments, UNION, stacked queries return nothing / don't mutate | exact match, missing rows, apostrophes in legitimate values |
| `access_control` | t0 IDOR read (easy), t1 update authorisation (medium), t2 client-supplied admin flag (hard, **private**) | cross-user, unknown and empty identities denied; denied actions don't mutate | owners and server-side admins keep access; missing → `KeyError` |
| `ssrf` | t0 scheme-only (easy), t1 string blocklist (medium), t2 redirect re-validation (hard, **private**) | loopback variants, metadata, private ranges, IPv6/mapped, encoded/unspecified, mixed resolution, unresolvable, redirect targets | public hostnames/IPs, relative redirects, redirect limits |

Variants are generated from a seeded template renderer (`generator_version 1.1.0`; value sampling unchanged since 1.0.0, hidden SQL oracles use exact-type assertions) that changes
function/table/entity names, fixture values and distractor helpers. Each instance records
family, template, seed and a fixture hash. Visible tests describe normal behaviour plus one basic
attack (exposed as `ATTACK_VALUE`, which enables a realistic visible-overfitting adversary).

## Splits — policy `template-holdout` v1

- private_test: the last template of each family (unseen structural variant) — 9 tasks
- for the other templates, by `seed % 5`: 0–2 train (18), 3 validation (6), 4 public_test (6)

Leakage checks: fixture hash unique across splits; private templates never appear elsewhere;
training datasets and GRPO prompt sets exclude held-out tasks (lineage check by task id).

## Execution

Fresh sandbox per instance; 20-action budget; 120 s wall clock; 20 s test timeout. Verification as
in `reward-design.md`. Each run persists the episode record so verification can be re-run later.

## Metrics

Every metric, with its numerator, denominator, unit, statistical unit, interval method and
missing-data rule, is defined in the versioned metric contract **`docs/metrics-contract.md`**
(schema 2, generated from `modules/evaluation/metric_contract.py`). In summary:

- **Outcomes:** verified success, partial success, verifier error rate, reward-positive rate,
  false-positive reward rate, hidden-security pass rate (executed checks only) and regression
  failure rate (over all episodes, as in v0.2, and over usable PASS/FAIL results; ERROR and
  INCONCLUSIVE checks are counted separately and never treated as passes), plus mean reward
  with a seeded bootstrap CI. End-to-end verified success keeps the scheduled-episode
  denominator. Verdict counts use the run's verifier profile: the default `v3_full` is
  authoritative, and research profiles are counted the same way.
- **Provider failures** (timeouts, rate limits, HTTP and transport errors, malformed
  responses) are reported separately from verifier errors, with verifier outcomes among
  episodes without a provider failure.
- **Ground truth:** without independent labels, the false-positive reward rate is labelled
  reward-verifier disagreement (`false_positive_reward_label_source: verifier_derived`).
- **Efficiency:** tokens, cost and tool calls per attempt and per verified success; latency
  p50/p90/p95/max for requests and episodes; telemetry coverage for every measurement.
- **Missing data:** totals need every episode. Any episode with unknown cost or usage,
  including every provider-failure episode, makes the total null, never zero. When cost
  coverage is partial, `cost.lower_bound_usd` sums the known costs: the true total is at least
  this much. `cost.status` says why a total is missing (`partial`, `unknown`, or `overflow`
  when finite costs sum beyond the float range).
- **Compatibility:** every v0.2 output key is still emitted, but old stored records aggregate
  differently: empty denominators are null, and records written before v0.3 telemetry have no
  usage or cost coverage, so token and cost totals over them are null.
- **Breakdowns:** by split, family, difficulty and seed, plus task-level and per-episode rows.

Proportions use **Wilson score intervals** (95 % default) with explicit `n`; an empty denominator
is `null` (rendered `n/a (n=0)`), never 0 %. With ≤ 39 tasks, intervals are wide; reports state
that no significance claims are made.

**Report tier** (`report_tier`, v0.3): `development` (default) keeps the configured bootstrap
resamples (2,000 by default) and labels the report development-grade. `final` requires
official run mode and at least 10,000 resamples. The default is excluded from `config_hash`,
so existing hashes are unchanged.

### Provider pricing and generation telemetry (v0.3)

- **Pricing:** `provider.price_per_1k_prompt` / `price_per_1k_completion`, in USD. `null` (the
  default) means unknown, and cost is then reported as unknown, not $0. `0.0` means explicitly
  free. Set both or neither; prices must be finite and non-negative (enforced in the config
  and in the provider constructor). A cost reported by the provider (`usage.cost`) takes
  precedence; an invalid reported cost makes the cost unknown rather than falling back to the
  price estimate. After any retry the call's total cost is unknown, because earlier attempts
  may have been billed and a response only reports its own cost.
- **Usage:** reported only when both token counts are present (a partial report is discarded
  entirely). Counts that are present but not integers in [0, 10^9] make the response
  `malformed_response`. Providers that predate v0.3 and set counts without `usage_reported`
  have usage inferred from non-zero counts.
- **Telemetry per generation step:** whether usage was reported, cost, finish reason, request
  seconds (including retries), HTTP attempts, and, for failed calls, the failure kind
  (`timeout`, `rate_limited`, `server_error`, `http_error`, `transport_error`,
  `malformed_response`, `other`) and whether the runner's deadline expired. Provider failures
  are infrastructure or model-access outcomes, not verifier outcomes.
- **Cost budgets** (`max_cost_usd > 0`) fail closed: the episode is refused without known
  pricing (and the provider is closed), and the budget is re-checked right after each
  generation, so a call that exceeds it (`budget:max_cost`) or has unknown cost
  (`budget:max_cost_unverifiable`) stops the episode before its tool calls run. Unknown cost
  takes precedence over the token limit. A timeout raised by the provider itself is recorded
  as a provider failure, distinct from the runner's own deadline.
- Benchmark reports aggregate this telemetry (cost, tokens, latency percentiles, coverage,
  provider failures) per the metric contract (v0.3 PR 1B).

## Contamination controls

Hidden tests are never exposed; oracle bundles are stored under a restricted prefix; the agent only
sees public specs; datasets carry lineage; held-out tasks are excluded from all training data.

## v0.2 — fail-closed integrity

Before any rollout, `BenchmarkRunner.validate` runs (report `checks_version` 2):

| Check | Fails when |
|---|---|
| `split_fixture_disjoint` | a fixture hash appears in more than one split |
| `private_template_holdout` | a private-test template is used by any other split |
| `task_lineage` | spec/oracle identity disagree; the fixture hash does not recompute from the bundle's files; `environment_ref` does not name the oracle's family/template/generator; split or task id violate the split policy |
| `training_overlap` | a task the evaluated model was trained on (declared `training_lineage`: dataset versions → task ids read from the Parquet partitions; training runs → recorded `training_task_ids`; or explicit task ids) is an evaluated task, shares its fixture, uses a private held-out template, or belongs to a held-out split; or a declared training run/dataset cannot be resolved |

`run_mode: official` (default) **refuses** the run when any check fails: no rollout executes, the
evaluation is stored with status `refused`, and `reports/<id>/refusal.json` holds the complete
leakage report. Only `run_mode: diagnostic` proceeds; its report is marked **NON-OFFICIAL** and
`official: false`. Every benchmark artifact (JSON report, DB metrics) contains the full leakage
report. Training-side guards: SFT refuses dataset rows from held-out tasks (blocked training run
recorded); GRPO refuses prompt rows that are not known training-split tasks.

Known limitation: task ids that are not in the registry (imported traces) cannot collide with a
benchmark task by id and are recorded as `unlinked_training_task_ids`; a held-out fixture
re-imported under a different id is not detected (no content-level matching yet).

## v0.2 — statistical units

- **Wilson intervals** (unchanged) are descriptive, episode-level statistics.
- **Task-clustered bootstrap** (`task_clustered.*`): resamples whole tasks with replacement
  (pooled-ratio estimator, seeded percentile bootstrap). Episodes of one task share a fixture and
  hidden tests and — for the deterministic demo provider — are identical across seeds, so the
  number of *independent* units is `n_tasks`, not `n_episodes`. Population claims should use the
  clustered interval. With fewer than two tasks the interval is reported as undefined.
- Every metric block reports `n_tasks`, `n_episodes`, `episodes_per_task` and `n_seeds`.
- `task_level` rows aggregate each task's episodes (rate, mean reward, verdict counts, whether the
  outcome was consistent across seeds).
- **Repeated seeds**: `seeds: [0, 1, 2]` runs every task once per seed (provider seed = that
  seed); `by_seed` breaks metrics down per seed. For the scripted provider this adds no variance
  information, and reports say so.
- **Reward-threshold sensitivity**: reward-positive and false-positive reward rates (Wilson and
  task-clustered) at thresholds 0.1/0.25/0.5/0.75/0.9 (`reward_thresholds`).
- **Evidence labelling**: `metrics.evidence.kind` is `scripted_agent_pipeline_validation` for the
  demo provider and `model_evaluation` otherwise; scripted results are never learned-model evidence.

## v0.3 — paired benchmark comparison

`traceforge benchmark compare --baseline <id> --candidate <id>` compares two benchmark runs
episode by episode. The full contract (fields, estimands, problem codes, legacy scenario
registry) is generated in **`docs/comparison-contract.md`**.

- **Pairing:** episodes pair on `(task_id, task_version, episode_seed)`. Strict coverage
  (default) refuses any difference in keys; `--coverage-policy intersection` is diagnostic only
  and lists every exclusion. Duplicates, task-version, verifier, scenario-identity and
  never-variable configuration differences are refused in every mode.
- **Independent unit:** the **structural scenario**: templates with one authoritative oracle
  identity (normalised hidden tests, visible tests and verifier configuration). Seeds repeat a
  task and seeded task instances vary only names and values, so neither becomes an independent
  bootstrap cluster. Legacy task versions 1.0.0/1.1.0 resolve through an explicit registry with
  pinned oracle fingerprints; SSRF t0 and t1 share one scenario because their oracle tests are
  identical. Record metadata never changes cluster membership.
- **Estimands:** mean metrics use `sum of paired differences / usable pairs`, ratio metrics the
  difference of pooled ratios: both equal the difference of the arms' PR 1B estimands.
  Intervals are single-stage percentile bootstraps over scenarios (ratio metrics share one draw
  for both arms; any zero-denominator replicate makes the interval null). A task-instance
  interval is shown as sensitivity only.
- **Primary endpoint:** `private_test` / `verified_success`. Without private_test pairs it is
  unavailable and pooled results are not substituted. Legacy public_test scenarios also appear in
  train, so public results measure in-distribution performance.
- **Evidence tiers:** fewer than 10 primary scenarios: `insufficient_scenarios`; 10–19:
  `exploratory_only`; 20 or more: `directional_eligible`, an eligibility gate, not proof of power
  or generalisation. A directional `improvement`/`regression` also needs an official, strictly
  matched comparison (both arms `v3_full`, completed, official, matching `provider.seed`) and a
  primary interval excluding 0. `report_tier`, `evidence_tier` and `evidence_source` are
  separate; a final report needs ≥ 10,000 resamples and ≥ 10 primary scenarios.
- **Cost and tokens:** differences only with complete coverage in both arms; lower bounds are
  never differenced.
- The legacy benchmark has 3 private structural scenarios (9 instances): comparisons on it are
  `insufficient_scenarios`. Scripted-provider comparisons are pipeline validation, not
  learned-model evidence.
