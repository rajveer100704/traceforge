# Implementation plan (recorded after reconnaissance)

## Reconnaissance (2026-11-08)

| Item | Finding | Consequence |
|---|---|---|
| Working directory | `C:\Users\BIT` is a home-directory git repo whose `origin` is an unrelated project | TraceForge created as a **separate repository** at `C:\Users\BIT\traceforge`; the home repo was not modified |
| Existing TraceForge code | none | full build from the brief |
| OS / shell | Windows 11, Git Bash + PowerShell, no `make` | Makefile provided for CI/Linux; every target has a `uv run` equivalent |
| Python | system 3.14; uv-managed 3.12.12 available | project pinned to 3.12 (`.python-version`, `requires-python >=3.12,<3.14`) |
| uv | 0.10.7 | lockfile `uv.lock` |
| Docker | CLI installed, daemon **not running** | Docker provider implemented and unit-tested via configuration; live integration test gated and run in CI |
| GPU | none detected (`nvidia-smi` absent) | `cpu-test` training only; real SFT/GRPO marked NOT EXECUTED |
| Disk | ~15 GB free | ML extra (CPU torch) installed (~2 GB) to exercise cpu-test SFT and TRL API inspection |
| GitHub CLI | authenticated as `rajveer100111`, **not** `rajveer100704` | all remote GitHub writes blocked (brief §26.1) — local implementation only |
| Master plan file | `TRACEFORGE_CODING_AGENT_MASTER_PLAN.md` not present in repo or recovery backup | the brief provided in the session is the contract |

## Plan as executed

Phases 0–10 of the brief, in order: foundation → contracts → task families (validated: vulnerable
code fails hidden security tests, reference fix passes all) → sandbox/runtime → verification/rewards
→ persistence/jobs → trace platform → evaluation/experiments → training adapters → API/CLI/workers →
tests, static gates, docs, experiments.

Key decisions: modular monolith (ADR-001); SQLite/local-FS local mode with the same migrations as
PostgreSQL/S3 (ADR-002); hidden tests executed by a stdlib harness with an audit-hook guard in a fresh
environment with pristine protected files (ADR-006); deterministic scripted demo provider with
reference and adversarial policies so the lifecycle and reward-hacking experiments run without a
model; TRL adapter written against the *installed* TRL `environment_factory` protocol (ADR-007).
