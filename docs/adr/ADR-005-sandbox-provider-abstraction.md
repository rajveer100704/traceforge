# ADR-005: Sandbox provider abstraction

**Status:** accepted

**Decision.** A `SandboxProvider` port with two implementations: `LocalTestSandboxProvider` (process,
fast, *not* an isolation boundary, restricted to trusted demo policies) and `DockerSandboxProvider`
(per-execution containers, network none, read-only, non-root, caps dropped, resource limits). The host
controls the provider; agents never receive it.

**Consequences.** Core tests run without Docker. Untrusted model output requires the Docker provider
(or an explicit, prod-forbidden override). Hostile multi-tenant workloads need stronger isolation
(microVMs) — out of scope for v0.1.
