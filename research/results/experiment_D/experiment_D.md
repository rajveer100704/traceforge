# Experiment D — generalization

> **SCRIPTED-AGENT PIPELINE VALIDATION: demo_provider policies are deterministic scripts, not a learned model.**

Evaluation `acc8d927-a001-4da8-9de8-259635f9ba70`; seeds [0, 1, 2]; full report: `.traceforge-exp\reports\acc8d927-a001-4da8-9de8-259635f9ba70\report.md`

| Split | Verified success | Independent tasks | Episodes | Task-clustered CI |
|---|---|---|---|---|
| private_test | 100.0% | 9 | 27 | [100.0%, 100.0%] (9 tasks) |
| public_test | 100.0% | 6 | 18 | [100.0%, 100.0%] (6 tasks) |
| validation | 100.0% | 6 | 18 | [100.0%, 100.0%] (6 tasks) |

> demo_provider is scripted; held-out results validate the protocol, not learned generalisation. Re-run with a model provider for real measurements.
