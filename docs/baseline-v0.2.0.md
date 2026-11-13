# v0.2.0 baseline audit

Phase 0 of `docs/plans/v0.3-research-execution.md`. Audited 2026-11-14 on Windows 11, Python 3.12,
uv 0.10.7. Every value below was read from git, the GitHub release, CI logs or the bundles
themselves. Nothing was re-run to produce it.

## Release state

| Item | Value |
|---|---|
| Release | [`v0.2.0`](https://github.com/rajveer100704/traceforge/releases/tag/v0.2.0), published 2026-11-09 |
| Tag | annotated `v0.2.0` → commit `e0de1d3` (merge of PR #3); identical to `main` when audited |
| Assets | `traceforge-0.2.0-py3-none-any.whl` (226,950 B, sha256 `29a9291f88ac6fcab1dfdebb8aa56985ee7007a51fa6b2eaf15631b7fe0b4357`) |
| | `traceforge-0.2.0.tar.gz` (3,962,118 B, sha256 `ea6ec6ccad00aef6603d10eabda0cd2416da2c1b54a6ac0fb758f84ee24e1d36`) |
| PyPI | not published |
| Task / generator / verifier / engine version | 1.1.0 / 1.1.0 / 1.1.0 / 1.1.0 (RNG namespace 1.0.0) |
| Historical task versions | 1.0.0, rebuilt by `families/legacy_1_0_0.py`, checked against 39 pinned fixture hashes |
| Reward policy | `verified-v1` 1.0.0 (`configs/reward_policies.yaml`) |
| CI on `e0de1d3` | CI, Security and Containers passed on push. A later **scheduled** Security run (42636252461) failed: gitleaks flags a synthetic placeholder in `tests/unit/test_jobs_statistics_safety.py:104` (commit `b81db0d`). PR scans only cover a PR's own commits, which is why this surfaced only on the full-history scheduled scan. Handled in the S1 security PR. |

## Container images (CI build of `e0de1d3`)

Images are built in CI and not pushed to a registry. The values below are local image IDs from the
scheduled Containers run 42629979720 (2026-11-09), not registry digests. Builds apply Debian
security updates at build time, so a rebuild can produce a different ID.

| Image | Image ID | Scan (Trivy) |
|---|---|---|
| `traceforge:ci` | `sha256:a063a733f313e116d77be2e65665f6e748b43950283ad3a31389b8434722d47b` | 0 CRITICAL, 47 HIGH (11 distinct CVEs), all under exceptions to 2026-12-09; gate PASS |
| `traceforge-sandbox:0.1` | `sha256:7b36a8bb4e9b4d0279f57cea64d58a43f4f2a9cb8065f88ea031c5376e76b345` | same |

## Historical artifacts

`research/HISTORICAL_SHA256SUMS` pins 55 files, hashed from the git objects at tag `v0.2.0` (not
the working tree):

- all 53 files under `research/results/` (experiments A–H and `experiment_H_verifier_1.1.0`);
- `research/TRACEFORGE_TECHNICAL_REPORT.md`;
- `src/traceforge/modules/tasks/families/legacy_1_0_0.py` (frozen task-version 1.0.0 source).

When audited, the working tree matched the tag for all 55 files, and `verify_bundle` reported no
problems for any of the nine bundles. `tests/integration/test_historical_integrity.py` enforces
this in CI:

- protected files must stay identical to the manifest;
- historical result directories may not gain files;
- every bundle must verify;
- each bundle's own `files_sha256` must agree with the manifest;
- the manifest must equal the tag's blobs (skipped when the tag is not fetched, e.g. in CI's
  shallow clone).

New results go in new directories.

| Bundle | Code commit recorded | Tree | Task version | Seeds | Episodes | Model |
|---|---|---|---|---|---|---|
| experiment_A | `c5d242b` | dirty | 1.0.0 | 0 | 72 | none (scripted) |
| experiment_B | `c5d242b` | dirty | 1.0.0 | 0–2 | 324 | none (scripted) |
| experiment_C | `c5d242b` | dirty | 1.0.0 | 0–2 | 45 | none (scripted) |
| experiment_D | `c5d242b` | dirty | 1.0.0 | 0–2 | 63 | none (scripted) |
| experiment_E | `c5d242b` | dirty | 1.0.0 | — | 0 (in-memory cases) | none |
| experiment_F | `c5d242b` | dirty | 1.0.0 | 0–2 | 72 | `tiny-random-local`, cpu-test, not pretrained |
| experiment_G | `c5d242b` | dirty | 1.0.0 | 0–2 | 45 | none (scripted `pipeline_check` arm only) |
| experiment_H | `c5d242b` | dirty | 1.0.0 | 0–2 | 459 | none (scripted) |
| experiment_H_verifier_1.1.0 | `137fccc` | dirty | 1.1.0 | 0–2 | 459 | none (scripted) |

Dataset versions recorded in the bundles:

- **Experiment A:** D1 `v-9e0b0c435561`, D2 `v-1fcc6488a51c`, D3 `v-a0fffb159d79`, D4
  `v-8ce75ca9058f`.
- **Experiment F** (content hashes, train/validation rows):

  | Level | Content hash | Train | Validation |
  |---|---|---|---|
  | D1 | `410266e2…f0841e` | 45 | 36 |
  | D2 | `6de0a285…fcbf1d` | 41 | 36 |
  | D3 | `999f7f7c…e3d09` | 12 | 12 |
  | D4 | `7e0c8388…c1109b` | 24 | 24 |

## Findings

1. **Bundles were produced from uncommitted working trees.** Every bundle records `dirty: true`.
   The v0.2 bundles name `c5d242b`, an ancestor of the v0.2 commit `39a842a`. The 1.1.0 rerun
   names `137fccc`, before the review fixes in `bb802bf`. The commit alone therefore does not
   identify the exact code that produced them. The outputs were first committed in `39a842a`
   (v0.2 results) and `bb802bf` (1.1.0 rerun). Those are the closest committed code, but this audit
   cannot prove the uncommitted trees were identical to them. This is recorded here rather than
   corrected, because the bundles are immutable. **For v0.3, official runs
   should refuse to start from a dirty tree.** That is proposed for the metrics PR, not done here.
2. **All published results are scripted pipeline validation, not learned-model evidence.** The
   bundles say so (`models[].reason`). F's only model is a randomly initialised cpu-test model.
3. **Documentation inconsistencies fixed in this PR:**
   - the README said "experiments A–E";
   - implementation status described merged work as open branches;
   - the CHANGELOG's 0.2.0 known issue said "Not fixed in this release" before saying it was fixed.
4. **The scheduled gitleaks failure on `main`** is above; it is fixed in the separate S1 PR.

## Must not change

- Everything in `research/HISTORICAL_SHA256SUMS`.
- The `v0.2.0` tag and release assets.
- `FIXTURE_HASHES_1_0_0` and the 1.0.0/1.1.0 entries of `VERSION_PROFILES`.
- For fixed seeds, the outputs of `wilson_interval` and `cluster_bootstrap`. v0.3 metric work
  must add golden tests before touching `statistics.py`.
