# Security policy

## Intended use

TraceForge executes agent-produced code inside sandboxes for **local, controlled research on
synthetic tasks**. It has not been independently security-assessed and is **not** a hardened
multi-tenant execution service. See [docs/sandbox-security.md](docs/sandbox-security.md) for the
threat model, controls and residual risks.

- The `local` sandbox provider is not an isolation boundary; it only runs trusted demo policies by default.
- Use the `docker` provider for model-generated code; use microVM-class isolation for hostile workloads.
- Task scenarios are offline. Do not adapt TraceForge to scan or attack real systems.
- Never commit `.env`, credentials, private traces or model weights.

## Container images

Both images (`traceforge:ci` platform, `traceforge-sandbox:0.1`) are pinned by base digest, run as
uid 10001, carry no setuid/setgid binaries, apply Debian security updates at build time, and are
scanned by Trivy on every push/PR and weekly. CRITICAL/HIGH findings fail CI unless covered by a
time-bounded exception in [security/vulnerability-exceptions.yaml](security/vulnerability-exceptions.yaml)
(owner, reason, ≤ 90-day expiry; analysis in
[docs/security/container-vulnerabilities.md](docs/security/container-vulnerabilities.md)).

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub Security Advisories on this repository
("Report a vulnerability"). Include affected version/commit, reproduction steps and impact. Do not
open public issues for undisclosed vulnerabilities.
