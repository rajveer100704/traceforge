# Experiment protocol

Run any experiment with `uv run traceforge experiment run <A|B|C|D|E> [--out-dir DIR]`.
Each writes `experiment_<X>.json` and `.md`, and registers an `experiments` row.

| Id | Question | Procedure | Requires |
|---|---|---|---|
| A | Does trajectory curation change what we would train on? | Collect verified rollouts on train/validation tasks with reference + adversarial demo policies; assess quality; build D1 (raw), D2 (schema-valid), D3 (verified successes), D4 (D3 + informative failures); report size, verified-success fraction and **misleading claimed successes** per level. Downstream SFT comparison runs only with a real base model. | nothing (SFT comparison: model weights) |
| B | Does the reward design resist hacking? | For one task per (family, template), run every demo policy (and the tamper policy again under a permissive harness); score each episode with R1/R2/R3; ground truth = policy label. Report false-/true-positive reward rates with Wilson CIs and R3 rejection reasons. | nothing |
| C | Base vs SFT vs SFT+GRPO | Same benchmark version and budgets for every model. Baseline runs now; SFT/GRPO run when capabilities exist and are otherwise reported as NOT EXECUTED with the reason. | model weights, GPU for practical sizes |
| D | Generalisation | Evaluate on validation (known templates, unseen seeds), public test, private test (unseen templates); report by split, family, difficulty. | nothing (meaningful only with a learned model) |
| E | Verifier robustness | Constructed episode states and injected faults per task: candidate timeout, syntax error, import-time crash, verifier crash, harness without result, inconsistent recorded hashes, tampered visible tests, deleted protected file, fabricated evidence, false completion, genuine fix without evidence, repeat-verification idempotency. | nothing |

## Reproducibility metadata (recorded per run)

Model id/revision and provider kind; prompt template version (`agent-v1`); decoding parameters and
seed; task id/version and fixture hash; split policy/version; verifier id/version + engine version +
config hash; reward policy id/version/hash; sandbox provider and isolation; environment reference
(image digest for Docker); tool budgets; benchmark config hash; dataset manifest content hash;
training config hash, seed, checkpoint SHA-256; code commit where available.

## Training procedures

**SFT** (`traceforge train sft --config configs/training_sft.yaml [--dataset-version-id ID]`):
Parquet rows → chat messages → token ids with assistant-only labels (prefix-difference over the
tokenizer chat template, or a plain template). `cpu-test` uses a tiny randomly initialised GPT-2
and a byte tokenizer (no download) for integration checks only. `sft` loads a local model
(safetensors; downloads require `allow_download` **and** a pinned revision) with LoRA via PEFT.
Checkpoints, loss curve and validation loss are stored; resume is supported; MLflow logging is used
when available.

**GRPO** (`traceforge train grpo --config configs/training_grpo.yaml`): TRL `GRPOTrainer` with
`environment_factory` → `TraceForgeToolEnv` (fresh sandbox per `reset`, router-mediated tools,
reward from `get_reward()` = independent verification). Prompt set = training tasks only. Requires
the Docker sandbox for model-generated code.

## Interpretation rules

- Demo-provider numbers validate the pipeline, never model capability.
- Report actual numbers with n and intervals; mark unexecuted parts NOT EXECUTED with the reason.
- Compare models only on identical benchmark config hashes.
