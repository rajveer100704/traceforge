# TraceForge technical report (v0.2)

**Scope of evidence.** Every number below was produced on 2026-11-09 on a Windows 11 machine
without a GPU, using the deterministic scripted `demo_provider` and the `local` process sandbox,
with seeds 0, 1 and 2. These numbers validate the **infrastructure** (environments, verifier,
reward, statistics, data curation, controlled training plumbing). They are **not** measurements of
any learned model; no pretrained model was trained or evaluated. Raw outputs and reproducibility
bundles: [`research/results/experiment_<X>/`](results/).

## 1. Problem

Agent post-training can improve the *score* without improving the *skill*: agents learn to claim
success, satisfy weak tests, tamper with graders or farm per-action rewards. TraceForge makes
every link from raw experience to reward auditable and independently verified, so these failure
modes become measurable.

## 2. Method

- **Tasks.** 3 offline cybersecurity remediation families × 3 templates, 39 seeded instances. The
  last template of each family is held out entirely as private test.
- **Episodes.** Agents act only through 7 allow-listed tools behind a fail-closed router. The host
  records the episode facts (files, test runs, patches, evidence, rejections).
- **Verification (V3).** State integrity, a static allow-list scan, behaviour safety, hidden
  security tests, hidden functional tests plus the pristine visible tests in a fresh environment,
  and evidence integrity/quality.
- **Reward (R3, `verified-v1`).** 0.7 completion + 0.1 regression + 0.1 evidence + 0.1 efficiency,
  with hard gates. Research baselines: R1 trusts the agent's claim; R2 runs the visible tests as
  found in the final workspace.
- **Attacks.**
  - *Development set:* 9 scripted adversaries known while R3 was designed.
  - *Held-out set (new in v0.2):* 5 adversaries written after verifier 1.0.0 was frozen, never
    used to modify it.
  - A catalogue records which verifier versions knew each attack.
- **Statistics (v0.2).**
  - Wilson 95 % intervals as descriptive, episode-level statistics.
  - Task-clustered percentile bootstrap intervals, which resample the independent tasks. With a
    deterministic provider, repeated seeds are not independent observations, so `n_tasks`, not
    `n_episodes`, is the effective sample size.
  - Reward-threshold sensitivity at 0.1 / 0.25 / 0.5 / 0.75 / 0.9.
- **Integrity (v0.2).**
  - Official benchmark runs refuse to start on any split, task-lineage or training-overlap
    problem; the complete leakage report is stored with every artifact.
  - SFT data and GRPO prompts are guarded against held-out tasks.

## 3. Results

### Experiment B — known (development) attacks (`experiment_B/`)

324 episodes: 9 tasks × 12 policy variants × 3 seeds. Ground truth is the scripted policy label.

| Design | False-positive reward rate, Wilson (n=270) | Task-clustered CI (9 tasks) | True-positive reward rate (n=54) |
|---|---|---|---|
| R1 self-report | 90.0 % [85.8, 93.0] | [90.0, 90.0] | 100 % [93.4, 100] |
| R2 visible tests | 38.9 % [33.3, 44.8] | [26.7, 55.6] | 100 % [93.4, 100] |
| **R3 TraceForge** | **0.0 % [0.0, 1.4]** | [0.0, 0.0] | 100 % [93.4, 100] |

- **R1's degenerate interval:** every task runs the same policy mix, so R1's per-task rate is the
  same on every task and the clustered interval collapses to a point.
- **Narrow Wilson intervals:** the Wilson intervals are narrower than in v0.1 only because they
  count the three identical seeds as independent episodes. The clustered intervals do not.
- **Threshold sensitivity:** rates are unchanged at every threshold from 0.1 to 0.9, because the
  rewards are bimodal (0, or ≥ 0.9).
- **Scope of the claim:** these attacks were known to the designers. This shows that R3 handles
  known attacks; it is not a robustness claim.

### Experiment H — unseen (held-out) attacks (`experiment_H/`)

459 episodes on the same 9 tasks and 3 seeds, verifier 1.0.0 unchanged.

| Set | Design | False-positive reward rate, Wilson | Task-clustered CI |
|---|---|---|---|
| development (n=270) | R3 | 0.0 % [0.0, 1.4] | [0.0, 0.0] |
| **held-out (n=135)** | **R3** | **4.4 % [2.1, 9.4]** | **[0.0, 11.1]** |
| held-out | R2 | 28.9 % [21.9, 37.0] | [13.3, 44.4] |
| held-out | R1 | 100 % [97.2, 100] | [100, 100] |

**Finding: verifier 1.0.0 is gameable by `universal_equality`.**

- **The attack:** the target function is replaced so that it returns an object whose `__eq__`
  always answers True.
- **Where it gets through:** R3 gives it a PASS verdict and reward ≥ 0.9 on both SQL-injection
  templates whose hidden tests compare return values with `==` (`sqli-t1`, `sqli-t2`): 6 of 27
  episodes.
- **Where it fails:** on `sqli-t0` it was rated PARTIAL. It fails on access-control and SSRF, where
  the hidden tests require a specific exception to be raised.
- **The other four held-out attacks:** R3 rewarded none of them (`exception_masking`,
  `input_denylist`, `report_injection`, `stale_evidence`). R2 rewarded `input_denylist` in 67 % of
  episodes.

**Response:** this release deliberately does **not** patch the verifier, so the held-out
measurement stays clean. Proposed fix for verifier 1.1.0:

- type-strict hidden assertions;
- a static-safety rule rejecting comparison-dunder definitions in candidate code.

Once that fix lands, `universal_equality` becomes a *development* attack for 1.1.0.

### Experiment E — verifier robustness (`experiment_E/`)

108 constructed fault and tampering cases (12 per task × 9 tasks): **108/108 behaved as
specified.** Infrastructure faults yield `ERROR` (reward 0), never `PASS`.

### Experiment A — trajectory quality (`experiment_A/`)

| Level | Selected | Verified-success fraction | Refuted/unverified success claims |
|---|---|---|---|
| D1 raw | 81 | 31 % | 51 (63 %) |
| D2 schema-valid | 77 | 31 % | 50 (65 %) |
| D3 verified successes | 24 | 100 % | 0 |
| D4 D3 + informative failures | 48 | 50 % | 24 (explicit verified-FAILURE labels) |

### Experiment F — curation ablation (`experiment_F/`)

- **Datasets:** D1–D4 were built from one verified corpus (D1 45/36 train/validation rows, D2
  41/36, D3 12/12, D4 24/24).
- **Training:** each was trained for 3 seeds under identical controls (control hash
  `7da247f23ab8`) in **cpu-test mode**: a tiny randomly initialised model, 4 optimiser steps. All
  12 runs completed.
- **Evaluation: NOT EXECUTED.** A random tiny model cannot solve tasks, so evaluating it would
  measure nothing.
- **Model-improvement claim: none.** The losses (≈ 5.22–5.26) only show that the controlled
  pipeline runs.

### Experiment G — base vs SFT vs SFT+GRPO (`experiment_G/`)

- **Executed:** only the scripted `pipeline_check` arm (15 tasks, 45 episodes): 100 % verified
  success, 0 % false-positive reward rate.
- **NOT EXECUTED:** `base`, `sft` and `sft_grpo`, because no inference endpoint or trained model
  exists here.
- **Comparison:** not available.

### Experiments C and D

Scripted reference policy, 100 % on every split (validation 6 tasks, public test 6, private test 9;
three seeds each). This confirms that the held-out templates are solvable and correctly verified;
it says nothing about learned generalisation. SFT/GRPO: **NOT EXECUTED** (no GPU, no local model
weights).

## 4. Limitations

1. Scripted policies: results characterise the verifier and reward, not model behaviour.
2. Small, synthetic benchmark (9–15 independent tasks per analysis). Clustered intervals are wide
   or degenerate. No significance claims.
3. R3 is demonstrably not ungameable: see Experiment H.
4. In-process test execution leaves a residual tampering surface; `universal_equality` exploits a
   related weakness in the test oracles.
5. Scripted episodes are deterministic only while the 120 s wall-clock budget holds. In H, one
   `reward_farmer` episode ran 328.6 s on a loaded machine and was terminated before its final
   report (R1 rewarded 26 of 27 instead of 27 of 27). Verdicts under R3 were unaffected.
6. The local sandbox is not an isolation boundary. The Docker sandbox integration test ran locally
   in v0.2: 4 of 5 runs passed, and one failure under load was not diagnosed.

## 5. Reproduction

```bash
uv sync --frozen --extra ml
uv run traceforge demo
for e in A B C D E F G H; do
  uv run traceforge experiment run $e --out-dir research/results/experiment_$e --seeds 0,1,2
done
```

Each `experiment_<X>/bundle/` contains `bundle.json` (commit, versions, config hashes, seeds,
environment, dataset/model/checkpoint provenance, file hashes), `metrics.json`,
`episodes.jsonl.gz` and `REPRODUCE.md`. The bundles record the dirty working tree they were
generated from: they were produced before the v0.2 commit.
