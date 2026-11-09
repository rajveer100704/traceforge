# Experiment F — curation ablation (D1–D4)

> **CONTROLLED-TRAINING PIPELINE CHECK ONLY — cpu-test trains a tiny randomly initialised model; losses are not evidence about curation quality.**

Model-improvement claim: **NONE — no pretrained model was trained and evaluated**.

Controlled across arms (hash `7da247f23ab8`): `{"base_model": "tiny-random-local", "base_model_revision": null, "batch_size": 1, "device": null, "epochs": 1, "learning_rate": 0.0005, "max_length": 4096, "max_steps": 4, "mode": "cpu-test", "precision": "auto", "use_lora": true}`; seeds [0, 1, 2]; evaluation splits ['private_test'].

| Arm | Dataset version | Train / val rows | Training runs | Mean last loss | Evaluation |
|---|---|---|---|---|---|
| d1_raw | `v-410266e263de` | 45 / 36 | 3/3 EXECUTED | 5.226 | NOT EXECUTED: training used cpu-test (tiny random model, not pretrained); evaluating it would measure nothing |
| d2_schema_valid | `v-6de0a285bdcc` | 41 / 36 | 3/3 EXECUTED | 5.255 | NOT EXECUTED: training used cpu-test (tiny random model, not pretrained); evaluating it would measure nothing |
| d3_verified_success | `v-999f7f7c9c6f` | 12 / 12 | 3/3 EXECUTED | 5.230 | NOT EXECUTED: training used cpu-test (tiny random model, not pretrained); evaluating it would measure nothing |
| d4_verified_plus_informative | `v-7e0c8388e04b` | 24 / 24 | 3/3 EXECUTED | 5.224 | NOT EXECUTED: training used cpu-test (tiny random model, not pretrained); evaluating it would measure nothing |

Losses from cpu-test runs are reported only to show that every arm trained under the same
controls; they are not comparable evidence of dataset quality (random tiny model, byte
tokenizer, a handful of steps).
