# Container vulnerability triage

Scanner: Trivy v0.70.0 (`--scanners vuln`), same version locally and in CI. Images:
`traceforge:ci` (platform: API + workers) and `traceforge-sandbox:0.1` (task/verifier sandbox),
both `FROM python:3.12-slim@sha256:5e2bdb1b…4fa5b` (Debian 13.7). That digest was still the
newest `python:3.12-slim` on 2026-11-09, so re-pinning alone could not remediate anything.

## Re-triage 2026-11-14 (S1)

**Status sources.** Debian security tracker (2026-11-14). All 11 distinct HIGH CVEs are still
unfixed in Debian 13 (trixie). Each is marked `no-dsa` ("Minor issue") or `postponed`, and fixed
only in forky/sid:

| CVE | Package | Fixed in |
|---|---|---|
| CVE-2026-76642, -78408, -78409, -78410 | util-linux | 2.42.3-1 / 2.42.4-1 |
| CVE-2025-69720 | ncurses | 6.6+20251231-1 |
| CVE-2026-119373, -119374, -119379 | sqlite3 | 3.54.1-1; upstream 3.54.0 |
| CVE-2026-16742 | systemd | 261.2-1 |
| CVE-2026-54369 | acl | 2.4.0-1; "point release update" planned |
| CVE-2026-9538 | perl | 5.42.3-1 |

**Base image.** `python:3.12-slim` still resolves to the pinned digest `5e2bdb1b…` (Docker Hub
`last_updated` 2026-11-08), so re-pinning remediates nothing.

**Re-scan.** This PR's CI `Containers` job rebuilds both images and scans them with the current
Trivy database. The local Docker engine was not responding when this was written, so there is no
local re-scan. The last completed scan is the scheduled run 42629979720 (2026-11-09): per image,
0 CRITICAL, 47 HIGH, 79 MEDIUM, 66 LOW; gate PASS.

### SQLite in the sandbox: reachable, now remediated

Probing the v0.2 harness with the host's Python 3.12 / SQLite 3.50.4 showed that the "no
file-backed databases" rule could be bypassed from SQL alone, from a `:memory:` connection:

- `ATTACH DATABASE 'x.db'` and `VACUUM INTO 'x.db'` created files. SQLite does file I/O in C, so
  no Python `open` audit event fires. In Docker, the working directory is the writable `/tmp`
  tmpfs.
- `CREATE VIRTUAL TABLE … USING fts5`, direct writes to FTS5 shadow tables (the corrupted-index
  precondition of CVE-2026-119374), and `PRAGMA writable_schema=ON` were all allowed.

Remediation, in order of strength:

1. **Patched library (sandbox image).**
   - SQLite **3.54.1**, built from the upstream `sqlite-autoconf-3540100.tar.gz` and verified
     against the SHA3-256 published by sqlite.org (`b6ced585…d9c4`).
   - Compiled **without FTS3/4/5, R-Tree and session**, so none of the three CVEs' code is
     present.
   - Compile-time maxima that in-process code cannot raise: `MAX_ATTACHED=0` (no `ATTACH` or
     `VACUUM INTO` at all), SQL length 1 MB, expression depth 200, compound SELECT 100, LIKE
     pattern 10 kB, blob/string 100 MB.
   - Python loads this library from `/usr/local/lib`. Debian's `libsqlite3-0` 3.46.1 stays
     installed only because util-linux's `liblastlog2-2` depends on it: `dpkg --purge` refused,
     and forcing it would leave a broken package database. No sandbox process loads the
     Debian library, and loading other libraries is denied (ctypes, extension loading).
   - The image build and CI assert the version and the absent modules. They also read
     `/proc/self/maps` to check that the interpreter maps only
     `/usr/local/lib/libsqlite3.so.0`.
     Trivy cannot see a source-built library, so SQLite advisories for this component are
     tracked manually: re-check <https://sqlite.org/changes.html> and the CVE list at every
     review.
2. **Runtime policy (harness, local and Docker sandboxes).** Every connection is created through
   a hardened factory, and the audit hook refuses any connection of another type (direct
   `Connection(...)`, custom factories). The policy:
   - `ATTACH` (and so `VACUUM INTO`), virtual tables, extension loading and PRAGMAs outside a
     read-only allow-list are denied by an authorizer and recorded as violations;
   - defensive mode (read-only shadow tables and schema), untrusted schema;
   - limits on SQL length (100 kB), expression depth (100), compound SELECT (50), LIKE pattern,
     variables, trigger depth and value size (10 MB);
   - a 64 MiB process-wide heap limit (SQLite only allows lowering it);
   - a 50-million-step VM budget per connection, which stopped a runaway recursive CTE after
     about 1.3 s locally.
3. **Container boundary (unchanged).** No network, read-only root filesystem, non-root,
   `cap_drop ALL`, `no-new-privileges`, memory/pids/CPU limits, `noexec` tmpfs.

### Residual risk

The runtime policy runs in the same process as candidate code, so it is defence in depth and not
a boundary.

- Candidate code can call `set_authorizer(None)` or `setconfig(...)`, raise limits up to the
  compile-time maxima, or call base-class methods. All of these were demonstrated locally.
- In the Docker sandbox the compile-time layer still holds:
  - `ATTACH` is impossible (max 0);
  - FTS5, R-Tree and session do not exist;
  - parser limits cannot exceed the compiled maxima;
  - `PRAGMA writable_schema` can be re-enabled, but only corrupts an in-memory database in a
    current SQLite.
- In the local sandbox (trusted demo code only) the host Python's SQLite applies, which for the
  uv-managed Python 3.12 was 3.50.4 with FTS5.
- Removing this residual risk needs the out-of-process verifier and a stronger isolation
  boundary (plan PR 6 / Phase 6).

### Also fixed: untrusted code via re-verification

`reverify` re-executes a stored episode's code. It had no trust check, so a rollout from an
untrusted model could be re-verified in the local (trusted-code-only) sandbox. It is now refused
unless the provider is the trusted demo provider or the single-user override (forbidden in prod)
is set. The platform-image SQLite assessment depends on this: untrusted code never runs in the
platform image.

### Exceptions after re-triage

- All 11 are renewed: reviewed 2026-11-14, expiring 2026-12-14.
- The three SQLite CVEs still cover both images, because the Debian package is still installed
  in the sandbox. Their reasons now record that sandboxed code uses the source-built 3.54.1,
  and that the Debian library is never loaded there (asserted in CI).
- Expected HIGH count: unchanged at 47 package/CVE pairs per image, unless the re-scan finds
  something new.

### Options considered but not taken now

- **Debian forky/sid packages:** mixing suites is unsupported and pulls in a new glibc.
- **Removing util-linux, ncurses, perl-base or the systemd libraries:** they are Essential or
  required by apt and the base system.
- **A distroless or other minimal base:** a larger change, worth evaluating together with the
  out-of-process verifier.

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
| CVE-2026-119373 / 119374 | libsqlite3-0 | FTS5 heap overflows. **Sandbox:** remediated on 2026-11-14. Sandboxed code uses a source-built SQLite 3.54.1 without FTS5. The Debian package remains only as a dependency of `liblastlog2-2` and is never loaded (see Re-triage). **Platform:** no FTS5 tables, no external database files, no untrusted code. Not reachable. |
| CVE-2026-119379 | libsqlite3-0 | Session-changeset overflow. Python's `sqlite3` does not expose the session extension, and the SQLite the sandbox uses omits it. Not reachable. |
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

Next review: on or before 2026-12-14, or earlier if the weekly scan reports a fix or SQLite publishes an advisory affecting 3.54.1.
