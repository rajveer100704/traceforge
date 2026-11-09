# Container vulnerability triage

Scanner: Trivy v0.70.0 (`--scanners vuln`), same version locally and in CI. Images:
`traceforge:ci` (platform: API + workers) and `traceforge-sandbox:0.1` (task/verifier sandbox),
both `FROM python:3.12-slim@sha256:5e2bdb1b…4fa5b` (Debian 13.7). That digest was still the
newest `python:3.12-slim` on 2026-11-09, so re-pinning alone could not remediate anything.

## Measured results (local scans, 2026-11-09)

| Image | Before v0.2 (CRIT / HIGH / MED / LOW) | After v0.2 hardening |
|---|---|---|
| `traceforge:ci` | 0 / 49 / 80 / 66 | 0 / 47 / 79 / 66 |
| `traceforge-sandbox:0.1` | 0 / 49 / 80 / 66 (never scanned in CI before) | 0 / 47 / 79 / 66 |

HIGH counts are per (package, CVE) pair: 13 distinct HIGH CVEs before, 11 after. Both images
run as uid 10001; after hardening neither contains a setuid/setgid binary (before: `mount`, `su`,
`umount`, `chsh`, `chage`, `newgrp`, `gpasswd`, `expiry`, `chfn`, `passwd`, `unix_chkpwd`).

## Changes applied

1. `apt-get upgrade` at build time in both images. Fixed **zlib1g** CVE-2026-85091 (heap overflow
   in `gz_vacate`) and CVE-2026-76844 (Trivy titles this one "webpack-dev-middleware", an upstream
   data quirk; the affected package is zlib1g). Fixed version `1:1.3.dfsg+really1.3.1-1+deb13u1`.
   Trade-off: the Debian package set may differ between builds of the same base digest; the
   weekly scheduled scan and the gate catch regressions.
2. All setuid/setgid bits removed (verified in CI). This removes the privilege-escalation path
   that the util-linux and shadow CVEs depend on.
3. CI scans **both** images. JSON, SARIF and table reports are uploaded as the `trivy-reports`
   artifact, and `scripts/trivy_gate.py` fails the job on any CRITICAL/HIGH finding that has no
   valid exception.

## Requested CVEs

### CVE-2026-76642 — util-linux (HIGH, CVSS 7.8, CWE-390)
- **Packages:** bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login,
  mount, util-linux (2.41.5-0+deb13u1), in both images.
- **Issue:** a failed external mount helper still runs privileged X-mount post-hooks
  (`X-mount.idmap` / `X-mount.owner`), which can lead to privilege escalation.
- **Fix:** none in Debian (status `affected`, no fixed version).
- **Exploitability here:** requires a privileged mount: setuid `mount` or CAP_SYS_ADMIN.
  - Both images now have no setuid binaries and run as non-root.
  - The sandbox adds `cap_drop: ALL` and `no-new-privileges`.
  - TraceForge never invokes `mount`.
  - Not reachable.
- **Decision:** time-bounded exception until 2026-12-09.

### CVE-2025-69720 — ncurses (HIGH, CVSS 7.8, CWE-121/120)
- **Packages:** libncursesw6, libtinfo6, ncurses-base, ncurses-bin (6.5+20250216-2), in both
  images.
- **Issue:** stack buffer overflow in `analyze_string` in the **infocmp** CLI
  (`progs/infocmp.c`), fixed upstream in 6.5-20251213.
- **Fix:** none in Debian.
- **Exploitability here:** the flaw is in the `infocmp` program, which TraceForge never runs. The
  libraries are present as dependencies of bash/readline. `ncurses-bin` is an Essential package
  and cannot safely be removed. Not reachable.
- **Decision:** time-bounded exception until 2026-12-09.

## Other HIGH findings (no fix available)

| CVE | Package(s) | Assessment |
|---|---|---|
| CVE-2026-78408 / 78409 / 78410 | util-linux family | Same precondition as CVE-2026-76642 (privileged mount/nsenter). Not reachable. |
| CVE-2026-119373 / 119374 | libsqlite3-0 | FTS5 heap overflow and corrupted-index code execution. **Platform:** never creates FTS5 tables, so not reachable. **Sandbox: reachable** by untrusted candidate code, because sqlite3 is allowed for the SQL-injection tasks. Native code execution there would bypass the in-process Python audit hook but stay inside the container (no network, read-only rootfs, non-root, no capabilities, resource limits). **Follow-up:** have the harness deny virtual tables with a SQLite authorizer. |
| CVE-2026-119379 | libsqlite3-0 | Session-changeset code execution. Python's `sqlite3` module does not expose the session extension. Not reachable. |
| CVE-2026-16742 | libsystemd0, libudev1 | Affects systemd-homed, which is not installed or running in either container. Not reachable. |
| CVE-2026-54369 | libacl1 | Needs a privileged process applying ACLs to attacker-controlled paths; none exists in either image. Not reachable. |
| CVE-2026-9538 | perl-base | Archive::Tar DoS (Debian: fix deferred). Perl is never executed. Not reachable. |

## Exception policy

`security/vulnerability-exceptions.yaml` is the single source of truth. Each entry has:

- the CVE id, scoped to named images and packages;
- a status, an owner, a reason, a review date, and an expiry at most 90 days after review (30
  days here).

The gate behaves as follows:

- It fails on expired or malformed entries.
- A `no-fix-available` exception stops applying the moment Trivy reports a fixed version, so the
  finding becomes actionable again.
- Exceptions that match no finding are listed for removal.
- Nothing is suppressed inside Trivy itself: the uploaded JSON reports always contain every
  finding.

Next review: on or before 2026-12-09, or earlier if the weekly scan reports a fix.
