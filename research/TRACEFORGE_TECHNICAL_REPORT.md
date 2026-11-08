# TraceForge technical report (v0.1)

**Scope of evidence.** Every number below was produced by commands executed on 2026-11-08 on a
Windows 11 machine without GPU or Docker daemon, using the deterministic scripted `demo_provider`
and the `local` process sandbox. These numbers validate the **infrastructure** (environments,
verifier, reward, statistics, data curation). They are **not** measurements of any learned model.
Raw outputs: [`research/results/`](results/).

## 1. Problem

Agent post-training pipelines can improve the *score* without improving the *skill*: agents learn
to claim success, satisfy weak tests, tamper with graders or farm per-action rewards. TraceForge makes
every link from raw experience to reward auditable and independently verified, so these failure
modes become measurable.

## 2. Method

- **Tasks.** 3 offline cybersecurity remediation families × 3 templates, 39 seeded instances; the last
  template of each family is a private held-out variant (benchmark-protocol.md). For every template we
  verified that the vulnerable code fails the hidden security tests and a reference fix passes all
  hidden and visible tests.
- **Episodes.** Agents act only through 7 allow-listed tools; a fail-closed router validates every
  call; the host records an `EpisodeRecord` of facts (files, test runs, patches, evidence, rejections).
- **Verification (V3).** State integrity, static allow-list scan, behaviour safety (policy-violation
  attempts, runtime audit-hook violations), hidden security tests, hidden functional + pristine visible
  regression tests in a fresh environment, evidence integrity/quality.
- **Reward (R3, `verified-v1`).** 0.7 completion + 0.1 regression + 0.1 evidence + 0.1 efficiency,
  with hard gates (authoritative, PASS, safety, regression, evidence integrity). Baselines: R1 trusts the
  agent's claim; R2 runs the visible tests as found in the final workspace.
- **Adversaries.** 10 scripted adversarial policies (false claim, no-op, reward farming, visible-test
  tampering, harness/hidden-test probing, fabricated evidence, visible-test overfitting, breaking
  functionality, unsafe code) plus a malformed-then-correct policy that *should* be rewarded.
- **Statistics.** Wilson 95 % intervals with explicit n; seeded bootstrap for mean reward.

## 3. Results

### Experiment B — reward hacking (`experiment_B.md`)

108 episodes over 9 tasks (one per family × template). Ground truth = policy label.

| Design | False-positive reward rate (undeserving, n=90) | True-positive reward rate (deserving, n=18) |
|---|---|---|
| R1 self-report | 90.0 % [82.1, 94.6] | 100.0 % [82.4, 100.0] |
| R2 visible tests | 38.9 % [29.5, 49.2] | 100.0 % [82.4, 100.0] |
| **R3 TraceForge** | **0.0 % [0.0, 4.1]** | **100.0 % [82.4, 100.0]** |

R2 was fooled by: fabricated-evidence episodes (the fix is real, the evidence is not) 100 %, tampering
under a permissive harness 100 %, visible-test overfitting 78 %, and no-op/false-claim/farming on the
templates whose vulnerable code already passes the visible tests (22 %). R3 rejection reasons are
specific: `regression:tests_failed`, `security:tests_failed`, `policy_violation_attempts`,
`protected_modified`, `fabricated_evidence`, `forbidden_import` + `runtime_sandbox_violation`.

### Experiment E — verifier robustness (`experiment_E.md`)

108 constructed cases (12 per task × 9 tasks): candidate timeout, syntax error, import-time crash,
verifier crash, harness without result, inconsistent recorded hashes, tampered visible tests, deleted
protected file, fabricated evidence, false completion claim, genuine fix without evidence (must still
PASS with reduced reward), repeated verification (must be identical). **108/108 as specified.**
Infrastructure faults yield `ERROR` (reward 0), never `PASS`.

### Experiment A — trajectory quality (`experiment_A.md`)

Corpus: 72 verified demo rollouts on train/validation tasks (6 policies × 12 tasks) + 9 parsed records
from the defective example corpus.

| Level | Selected | Verified-success fraction | Refuted/unverified success claims |
|---|---|---|---|
| D1 raw | 81 | 31 % | 51 (63 %) |
| D2 schema-valid | 77 | 31 % | 50 (65 %) |
| D3 verified successes | 24 | 100 % | 0 |
| D4 D3 + informative failures | 48 | 50 % | 24 (kept with explicit verified-FAILURE labels) |

A pipeline that trusted agent claims would have treated ~63 % of this corpus as successful
demonstrations. **Downstream SFT comparison across D1–D4: NOT EXECUTED** (no base model weights / GPU).

### Experiment D — generalisation (`experiment_D.md`)

Scripted reference policy: 100 % on validation (n=6), public test (n=6) and private test (n=9). This
confirms the held-out protocol and the private templates are solvable and correctly verified; it says
nothing about learned generalisation.

### Experiment C — base vs SFT vs SFT+GRPO (`experiment_C.md`)

- Baseline (scripted): 100 % (n=15) on public+private test.
- **SFT: NOT EXECUTED** — no local base model, CPU only.
- **GRPO: NOT EXECUTED** — installed TRL 1.17.1 / transformers 5.22.2 support `environment_factory`
  (verified), but no CUDA device or local chat model.

### Training integration (executed, cpu-test only)

A tiny randomly initialised GPT-2 (byte tokenizer, no download) trained on assistant-only targets from
a TraceForge dataset: 3 optimizer steps, loss 5.493 → 5.242, checkpoint stored with SHA-256 and logged
to MLflow (SQLite backend). Tests show loss decreases over 6 steps and resume works. This verifies
dataset → masking → trainer → checkpoint wiring only.

## 4. Limitations

1. Scripted policies: results characterise the verifier and reward, not model behaviour.
2. Small, synthetic benchmark (39 tasks); wide intervals; no significance claims.
3. Attacks are known in advance; R3 is not claimed to be ungameable.
4. In-process test execution leaves a residual tampering surface, narrowed by the static scan.
5. The local sandbox is not an isolation boundary; Docker execution was not exercised in this
   environment (unit-tested configuration; CI integration job provided).

## 5. Reproduction

```bash
uv sync --frozen --extra ml
uv run traceforge demo
for e in A B C D E; do uv run traceforge experiment run $e --out-dir research/results; done
uv run traceforge train sft --config configs/training_sft.yaml --dataset-version-id <id>
```
