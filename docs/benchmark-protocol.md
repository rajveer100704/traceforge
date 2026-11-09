# Benchmark protocol

## Task families (v0.1, synthetic, offline)

| Family | Templates (difficulty) | Security property (hidden tests) | Behaviour preserved (hidden functional tests) |
|---|---|---|---|
| `sql_injection` | t0 f-string lookup (easy), t1 LIKE concat (medium), t2 two-param `.format` (hard, **private**) | tautologies, comments, UNION, stacked queries return nothing / don't mutate | exact match, missing rows, apostrophes in legitimate values |
| `access_control` | t0 IDOR read (easy), t1 update authorisation (medium), t2 client-supplied admin flag (hard, **private**) | cross-user, unknown and empty identities denied; denied actions don't mutate | owners and server-side admins keep access; missing → `KeyError` |
| `ssrf` | t0 scheme-only (easy), t1 string blocklist (medium), t2 redirect re-validation (hard, **private**) | loopback variants, metadata, private ranges, IPv6/mapped, encoded/unspecified, mixed resolution, unresolvable, redirect targets | public hostnames/IPs, relative redirects, redirect limits |

Variants are generated from a seeded template renderer (`generator_version 1.0.0`) that changes
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

Verified success, partial success (improved over the vulnerable baseline but incomplete), verifier
error rate, reward-positive and false-positive reward rates, regression failure rate, mean reward
(+ seeded bootstrap CI), tool calls per attempt/success, action rejection rate, task duration,
environment compute time, token usage/cost when reported by the provider, verifier precision/recall
when labelled ground truth exists. Breakdowns by split, family and difficulty plus per-task rows.

Proportions use **Wilson score intervals** (95 % default) with explicit `n`. With ≤ 39 tasks, intervals
are wide; reports state that no significance claims are made.

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
