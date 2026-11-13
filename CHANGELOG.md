# Changelog

## Unreleased

### Added
- `research/HISTORICAL_SHA256SUMS`: SHA-256 of every protected historical artifact (v0.2.0
  experiment outputs and bundles, the v0.2 technical report, the frozen task-version 1.0.0
  source), computed from the git objects at tag `v0.2.0` by `scripts/historical_manifest.py`.
- `tests/integration/test_historical_integrity.py`: CI fails if a protected file changes,
  disappears, becomes a symlink, or a historical result directory gains entries; all nine
  bundles must verify; the manifest must equal the `v0.2.0` tag (CI fetches the tag).
- `docs/baseline-v0.2.0.md` (baseline audit) and `docs/plans/v0.3-research-execution.md`.

### Fixed
- Documentation consistency: README lists experiments A–H; implementation status no longer
  describes merged work as open branches; the 0.2.0 known-issues entry below is reworded.

## Verifier 1.1.0 — universal-equality hardening (released in 0.2.0, 2026-11-09)

### Background: the 1.0.0 weakness
Experiment H (v0.2.0, 2026-11-09) found that verifier 1.0.0 rewarded the held-out
`universal_equality` attack, in which the target function returns an object whose `__eq__` always
answers True. It passed in 6 of 27 episodes, all on SQL-injection templates t1 and t2; the
held-out R3 false-positive rate was 4.4 % (6/135). That historical result is unchanged: the
v0.2.0 outputs in `research/results/experiment_H/` and the v0.2.0 technical report were produced
with verifier 1.0.0 and are kept as recorded.

### Two independent protections
1. **Static safety (pre-execution):** candidate code may not define `__eq__`, `__ne__`, `__lt__`,
   `__le__`, `__gt__` or `__ge__` (sync or async, any nesting level, called or not). Finding code
   `comparison_dunder_definition`. The static gate fails before any hidden or visible test runs.
2. **Exact-type hidden SQL oracles (defence in depth):** every hidden SQL test (T0 lookup, T1
   catalogue search, T2 ledger query) now uses `_assert_exact(actual, expected)` and `_rows(...)`.
   These check exact runtime types first, recursively through lists, tuples and dicts, reject
   subclasses of built-in result types, and compare values only between identical built-in
   types. Expected functional and security behaviour is unchanged. Visible tests are unchanged.

### Versions
- `GENERATOR_VERSION`, `TASK_VERSION`, `VERIFIER_VERSION`, `ENGINE_VERSION`: 1.0.0 → 1.1.0.
  Hidden-test content changed, so every task has a new immutable version and fixture hash.
- Value sampling (names, fixture values, visible files) is deliberately unchanged
  (`RNG_NAMESPACE_VERSION = "1.0.0"`); 1.1.0 fixtures differ from 1.0.0 only in oracle code.
- Example traces and task specs regenerated for task version 1.1.0.

### Attack catalogue
- `universal_equality` keeps `attack_set: heldout` (authoring-time classification) and is now
  `known_to_verifier_versions: ["1.1.0"]`. It was held out for 1.0.0 and is a **development
  attack for 1.1.0**.
- Development attacks are known to 1.0.0 and 1.1.0. New `heldout_at(version)`.
  `known_at("1.0.0")` is unchanged; the other four held-out attacks remain held out for 1.1.0.
- Experiment H now derives its development/held-out sets from the verifier version under test.

### Review follow-ups (PR #2)
- Static rule also rejects comparison methods bound without `def`: class-body assignment
  (including annotated, augmented and multiple targets, nested classes, aliased callables),
  walrus, `for` targets, import aliases, `dict(__eq__=...)` keywords and string literals naming a
  comparison dunder (dynamic `type()` construction).
- Historical task version 1.0.0 stays available for re-verification. It is rebuilt by a frozen
  legacy renderer (`families/legacy_1_0_0.py`) and checked against fixture hashes pinned from the
  v0.2.0 release. It is reachable only by explicit version; listings and benchmarks use 1.1.0. An
  unavailable version raises `UnsupportedTaskVersion` (HTTP 409) with the available versions.
- Rollout requests pin the concrete task version at acceptance, so queued or retried jobs never
  switch versions.
- Instances advertise and serve only the task versions they can execute. `/health/ready` reports
  version skew; the rolling-upgrade order is in `docs/operations.md`.
- Task sync is idempotent and concurrency-safe (`INSERT ... ON CONFLICT DO NOTHING`, then the stored
  fixture hash is re-read and validated; SQLite and PostgreSQL).
- `universal_equality` no longer lists `verified-v1` as a reward policy that knew it (only the
  verifier changed).
- The verifier provenance `config_hash` now covers every static-safety constant (including the
  comparison rule and frame attributes) and the engine version.
- The pre-execution static gate applies to the authoritative FULL (V3) profile. VISIBLE_TESTS (R2)
  and SELF_REPORT (R1) remain deliberately weak, non-authoritative research baselines: R2 is still
  fooled by `universal_equality` (tested), and neither can yield authoritative reward or a verified
  success.

### Experiment H re-run against verifier 1.1.0 (new output; the 1.0.0 result is untouched)
`research/results/experiment_H_verifier_1.1.0/`: 459 episodes, 9 tasks, seeds 0–2, scripted
provider. R3 false-positive reward rate:
- **0.0 % (0/297)** on attacks known to 1.1.0 (the 9 development attacks plus
  `universal_equality`; Wilson [0.0, 1.3], task-clustered [0.0, 0.0]);
- **0.0 % (0/108)** on the four attacks still held out for 1.1.0 (Wilson [0.0, 3.4], task-clustered
  [0.0, 0.0]).

R3 rewarded 100 % of genuine fixes (n=54). The historical verifier 1.0.0 result (R3 held-out 6/135,
4.4 %) is unchanged. In the per-attack table, "Set" is the authoring-time set; the summary table
uses the sets relative to 1.1.0.

### Limits
This removes one known exploit class. It does not show that the verifier is ungameable: robustness
claims for 1.1.0 can only come from attacks that remain held out for it (currently four), and new
held-out attacks are needed.

## 0.2.0 — research integrity and hardening (2026-11-09)

Ships task/verifier version 1.1.0 (section above). Task version 1.0.0 remains available,
by explicit version only, for re-verifying rollouts recorded against it.

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
- Historical finding, resolved in this release: Experiment H found that verifier 1.0.0 rewards
  the held-out `universal_equality` attack on two SQL-injection templates (6/27 episodes; R3
  held-out FP rate 4.4 %, 6/135). The v0.2 research branch deliberately left 1.0.0 unpatched to
  keep that measurement clean. Verifier 1.1.0 (section above) ships in 0.2.0 and rejects the
  attack: R3 rewarded 0/27 `universal_equality` episodes in the 1.1.0 rerun (separately, 0/108
  across the four attacks still held out for 1.1.0). The 1.0.0 measurement and the
  technical report are kept unchanged as the historical record.
- Container images still carry 11 HIGH CVEs with no Debian fix (time-bounded exceptions until
  2026-12-09); see `docs/security/container-vulnerabilities.md`.

## 0.1.0 — initial research release (2026-11-08)

First public release. See the GitHub release notes:
https://github.com/rajveer100704/traceforge/releases/tag/v0.1.0
