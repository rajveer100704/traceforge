# Experiment C — base vs SFT vs SFT+GRPO

> **SCRIPTED-AGENT PIPELINE VALIDATION: demo_provider policies are deterministic scripts, not a learned model.**

- Baseline (scripted demo provider): 100.0% (15 independent tasks, 45 episodes, seeds [0, 1, 2]) — report `.traceforge-exp\reports\83b5b012-b895-4f17-b351-2086442a6673\report.md`
- SFT: **NOT EXECUTED: available on CPU only (no CUDA); practical only for tiny models; no base model weights available locally**
- GRPO: **NOT EXECUTED: trl 1.17.1, transformers 5.22.2 support environment_factory; no CUDA device — real GRPO runs are impractical on this machine**

No trained-model comparison is reported because none was executed. See Experiment G.
