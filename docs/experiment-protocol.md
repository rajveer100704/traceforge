# Experiment protocol

Run any experiment with
`uv run traceforge experiment run <A|B|C|D|E|F|G|H> [--out-dir DIR] [--seeds 0,1,2] [--config FILE]`.
Each writes `experiment_<X>.json` and `.md`, a reproducibility bundle under `bundle/`, and
registers an `experiments` row. Default seeds are `0,1,2` (repeated-seed execution).

| Id | Question | Procedure | Requires |
|---|---|---|---|
| A | Does trajectory curation change what we would train on? | Collect verified rollouts on train/validation tasks with reference + adversarial demo policies; assess quality; build D1 (raw), D2 (schema-valid), D3 (verified successes), D4 (D3 + informative failures); report size, verified-success fraction and **misleading claimed successes** per level. | nothing |
| B | Does the reward design resist *known* hacking? | For one task per (family, template) and every seed, run every development demo policy (and the tamper policy again under a permissive harness); score each episode with R1/R2/R3; ground truth = policy label. Report false-/true-positive reward rates with Wilson **and task-clustered** CIs, threshold sensitivity, and R3 rejection reasons. All attacks were known to the R3 designers. | nothing |
| C | Base vs SFT vs SFT+GRPO (baseline only) | Scripted baseline on public+private test; SFT/GRPO reported NOT EXECUTED with the reason. Superseded by G for model arms. | model weights |
| D | Generalisation | Evaluate on validation, public test, private test (unseen templates); report by split, family, difficulty with independent-task counts. | nothing (meaningful only with a learned model) |
| E | Verifier robustness | Constructed episode states and injected faults per task (timeouts, crashes, tampering, fabricated evidence, idempotency). | nothing |
| F | Does curation level change the trained model? | Build D1–D4 from one verified corpus; train each with **identical** base model + revision, tokenizer, budget, optimiser settings and seeds (`control_hash` must match across arms); evaluate every arm on the same private task-family-held-out tasks with fail-closed lineage checks. | pretrained base model + an inference endpoint per arm |
| G | Base vs SFT vs SFT+GRPO | One benchmark config for every arm (splits, seeds ≥ 3, reward policy, verifier profile); only the provider and its declared training lineage differ. Records verified success, hidden-security pass rate, regression failure rate, false-positive reward rate, tool calls per verified success, generation latency, tokens/cost, training duration/hardware, Wilson + task-clustered CIs, per-family/difficulty/seed results. Arms without a provider are NOT EXECUTED. | inference endpoints for each model |
| H | Does R3 resist *unseen* attacks? | Run development attacks and **held-out** attacks (written after verifier 1.0.0 froze, never used to modify it) on identical tasks/seeds; report FP reward rates per set and per attack, with the attack catalogue recording which verifier versions knew each attack. | nothing |

## Known vs unseen attacks

`modules/evaluation/attack_catalog.py` lists every scripted attack with its set (`development` /
`heldout`), authoring date, and `known_to_verifier_versions`. Rule: if a verifier version is
changed in response to an attack, add that version to the attack's list; claims about that
version on that attack are development-set claims from then on. Held-out attacks live in
`modules/agents/providers/heldout_attacks.py`, separate from `demo.POLICIES`, so Experiment B never
runs them.

## Reproducibility bundle (per experiment run)

`bundle/bundle.json` records: git commit, branch and dirty flag; TraceForge, task generator, task
and split-policy versions; task-catalogue hash; dataset transformation and split policy; verifier
id/version and a hash of its effective configuration (static-safety rules included); reward
policy versions and content hashes; attack-catalogue and leakage-check versions; every config with
its hash; seeds; Python/OS/CPU/package versions and the resolved device (GPU, CUDA, precision);
dataset manifests and hashes; model and tokenizer revisions (or why none); checkpoint SHA-256s;
evaluation ids; and a SHA-256 of every other bundle file. `bundle/metrics.json` holds the aggregate
metrics and intervals, `bundle/episodes.jsonl.gz` the raw episodes (rollout summary, host-recorded
episode facts, trajectory), and `bundle/REPRODUCE.md` the exact commands.
`traceforge.application.reproducibility.verify_bundle(path)` re-checks all file hashes.

## Training procedures

**Device selection** (`device: auto|cpu|cuda`, `precision: auto|fp32|bf16|fp16`): `auto` uses
CUDA when available; `cuda` fails closed when no CUDA device exists (never a silent CPU fallback);
`auto` precision is bf16 on CUDA devices that support it, fp16 on other CUDA devices, fp32 on CPU.
SFT moves the model and every batch (input ids, labels, attention mask) to the device and uses
autocast (+ grad scaling for fp16); GRPO derives `use_cpu`/`bf16`/`fp16` from the resolved device.
`cpu-test` defaults to CPU and stays a lightweight integration mode. Device, GPU names/memory, CUDA,
cuDNN, PyTorch and precision are recorded with every training run.

**SFT** (`traceforge train sft --config configs/training_sft.yaml [--dataset-version-id ID]`):
Parquet rows → chat messages → token ids with assistant-only labels. Dataset rows from held-out
tasks are refused (blocked run recorded). `cpu-test` uses a tiny randomly initialised GPT-2 and a
byte tokenizer for integration checks only. `sft` loads a local model (downloads require
`allow_download` **and** a pinned revision) with LoRA via PEFT. Training task ids are recorded so
later benchmarks can check lineage.

**GRPO** (`traceforge train grpo --config configs/training_grpo.yaml`): TRL `GRPOTrainer` with
`environment_factory` → `TraceForgeToolEnv`. Prompt rows are validated to be known training-split
tasks before training starts. Requires the Docker sandbox for model-generated code.

## Interpretation rules

- Demo-provider numbers validate the pipeline, never model capability; every report labels them.
- Report actual numbers with n (independent tasks *and* episodes) and intervals; mark unexecuted
  parts NOT EXECUTED with the reason.
- Use task-clustered intervals for claims about the task population.
- Compare models only on identical benchmark config hashes; official runs only.
- No model-improvement claim until a real pretrained model has been trained and evaluated.
