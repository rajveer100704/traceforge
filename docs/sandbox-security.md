# Sandbox security model

TraceForge is designed for **local, controlled research on synthetic tasks by a single user or
small team**. It is not a hardened multi-tenant execution service.

## Threat model

**Adversary:** the policy under evaluation (a model or scripted agent) that wants reward without
solving the task, or that emits code which tries to escape, probe the host, or tamper with grading.

**Assets:** hidden tests/oracle, the host filesystem and credentials, the verifier's integrity, the
reward signal, other runs' state.

**Out of scope:** malicious operators, compromised dependencies/base images, kernel/container-runtime
0-days, side channels, denial of service against the host by a determined attacker.

## Controls

| Layer | Control | Where |
|---|---|---|
| Tool surface | 7 allow-listed tools; no shell, Docker API, network or arbitrary execution | `modules/agents/tool_registry.py` |
| Router | unknown/unauthorised tools, malformed JSON, schema violations, budgets, terminal state → rejected and recorded before any side effect | `modules/agents/tool_router.py` |
| Paths | relative-only, no `..`, no drive letters/backslashes/NUL, symlinks rejected along the chain, resolution must stay under the run root | `modules/environments/workspace.py` |
| Writes | only pre-existing editable files (`app/*.py`); tests, `TASK.md`, `app/__init__.py` protected; 64 KB limits | same |
| Hidden material | hidden tests live only in the oracle bundle and the verifier's temporary directory; never in the workspace, prompts or observations; verifier feedback is non-leaking | `modules/verification/engine.py` |
| Static gate | AST allow-list scan of changed files (imports per task, no eval/exec/open/`__import__`, no dunder/frame access, no dynamic getattr); failing code is **never executed** by the verifier | `modules/verification/safety.py` |
| Runtime guard | audit hook in the harness denies process creation, sockets, ctypes, file writes, file-backed sqlite and dangerous imports; denied events are reported as violations (safety gate) | `modules/tasks/harness/runner.py` |
| Result integrity | result line framed with a per-run nonce removed from the environment before candidate import; forged lines are ignored (tested) | same + `infrastructure/sandbox/common.py` |
| Verification isolation | hidden tests run in a **fresh** environment with the candidate's editable files plus **pristine** copies of all protected files | `checks.candidate_files` |
| Docker provider | per-execution container; `network_mode=none`; read-only rootfs; user 65534; `cap_drop=ALL`; `no-new-privileges`; default seccomp; memory/CPU/PID limits; 16 MB noexec tmpfs; read-only bind mounts; bounded logs; forced removal; labels for reaping; image pinned by digest | `infrastructure/sandbox/docker_provider.py` |
| Local provider | minimal environment (no host secrets), POSIX rlimits, wall-clock timeout, bounded output; **restricted to trusted demo policies** unless `TRACEFORGE_ALLOW_UNTRUSTED_LOCAL_EXECUTION=true` (forbidden when `env=prod`) | `infrastructure/sandbox/local_provider.py`, `modules/agents/runner.py` |
| Lifecycle | sandbox destroyed in `finally`; destroy is idempotent; failures surfaced and reaped by age/label | runners, `workers/main.py` |
| Control plane | API key (mandatory in prod, constant-time compare), strict request schemas, size limits | `api/` |

## Residual risks (honest list)

1. **The local provider is not an isolation boundary.** Candidate code runs as a host subprocess.
   The audit hook and static scan are defence-in-depth; on Windows no memory/PID limits apply.
2. **In-process test tampering.** Tests and candidate code share a Python process. The static scan
   removes the obvious primitives (gc/inspect/sys/builtins/dunder access) but a sufficiently novel
   technique could still influence results. Running tests out-of-process with a narrow RPC would
   close this further.
3. **Docker alone is not sufficient for hostile multi-tenant workloads.** Use gVisor, Kata or
   Firecracker microVMs before running untrusted third-party code in production.
4. **Docker integration was not executed in the development environment** (daemon unavailable);
   configuration is unit-tested and an integration test runs in the `container` CI workflow.
5. Python audit hooks can be bypassed by native code; native loading (`ctypes`) is denied but this
   is not a substitute for OS-level isolation.

## Network rule

All tasks are offline and deterministic. SSRF tasks use injected fake resolvers/fetchers. Docker
containers have no network. No tool can reach external systems.
