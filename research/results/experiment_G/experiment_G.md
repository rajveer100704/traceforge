# Experiment G — base vs SFT vs SFT+GRPO

> **Only arms with learned_model=true are model evidence; pipeline_check is a scripted reference policy.**

Comparison: **NOT AVAILABLE — fewer than two learned-model arms executed**. Identical benchmark for every arm: `{"reward_policy": "verified-v1", "sandbox": "local", "seeds": [0, 1, 2], "splits": ["public_test", "private_test"], "verifier_profile": "v3_full"}`.

| Arm | Status | Tasks / episodes | Verified success | Task-clustered CI | Hidden security pass | Regression failure | FP reward rate | Calls / success | Gen. latency (s) | Tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| pipeline_check (scripted) | EXECUTED | 15 / 45 | 100.0% [92.1%, 100.0%] | [100.0%, 100.0%] (15 tasks) | 100.0% [92.1%, 100.0%] | 0.0% [0.0%, 7.9%] | 0.0% [0.0%, 7.9%] | 8.133333333333333 | 0.001178488888888889 | not reported |
| base | NOT EXECUTED: no provider configured (needs an inference endpoint for this model) | | | | | | | | | |
| sft | NOT EXECUTED: no provider configured (needs an inference endpoint for this model) | | | | | | | | | |
| sft_grpo | NOT EXECUTED: no provider configured (needs an inference endpoint for this model) | | | | | | | | | |

Per-family/difficulty results and training cost are in the JSON document.
