# Changelog

## 0.2.0 — research integrity and hardening (unreleased)

### Added
- Explicit training device/precision selection (`device`, `precision`) with fail-closed CUDA and
  recorded hardware/software metadata; GRPO device flags derived from the resolved device.
- Fail-closed benchmark integrity: task-lineage and training-overlap checks, `run_mode:
  official|diagnostic`, refusal artifacts with the full leakage report, SFT/GRPO leakage guards.
- Task-clustered bootstrap intervals, task-level summaries, `n_tasks`/`n_episodes`, repeated-seed
  execution (`seeds`), reward-threshold sensitivity, evidence labels on every report.
- Experiments F (curation ablation), G (base vs SFT vs SFT+GRPO arms), H (held-out attacks) and
  an attack catalogue tracking which verifier versions knew each attack.
- Reproducibility bundles for every experiment run (`bundle/`), with `verify_bundle`.
- Container vulnerability gate (`scripts/trivy_gate.py`), time-bounded exceptions, scans of both
  images with JSON/SARIF artifacts, weekly re-scan.
- Token usage, generation latency and seed recorded on rollout summaries.

### Changed
- Both images apply Debian security updates at build time and strip setuid/setgid bits.
- `traceforge experiment run` accepts `F|G|H`, `--seeds` and `--config`.

### Known issues
- Experiment H found that verifier 1.0.0 rewards the held-out `universal_equality` attack on two
  SQL-injection templates (6/27 episodes; R3 held-out FP rate 4.4 %, 6/135). Not fixed in this
  release, to keep the held-out measurement clean; proposed fix for verifier 1.1.0 in the report.
- Container images still carry 11 HIGH CVEs with no Debian fix (time-bounded exceptions until
  2026-12-09); see `docs/security/container-vulnerabilities.md`.
