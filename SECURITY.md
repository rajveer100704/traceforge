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

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub Security Advisories on this repository
("Report a vulnerability"). Include affected version/commit, reproduction steps and impact. Do not
open public issues for undisclosed vulnerabilities.
