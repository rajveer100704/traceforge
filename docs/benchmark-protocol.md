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
