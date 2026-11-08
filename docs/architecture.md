# Architecture

TraceForge is a **modular monolith** (domain + application logic in one package) with separate
**worker processes** for long-running jobs. Infrastructure is reached only through ports.

## Layers and dependency direction

```mermaid
flowchart TB
  subgraph Interfaces
    API[api/ FastAPI control plane]
    CLI[cli.py Typer CLI]
    W[workers/ RQ worker + recovery loop]
  end
  subgraph Application
    C[container.Platform — composition root]
    S[services: ingestion, datasets, rollouts, evaluation, rewards, experiments]
    J[jobs.JobService — outbox, CAS transitions, retries, leases]
    T[training.TrainingService]
  end
  subgraph Modules["Domain modules (no infrastructure imports)"]
    TP[trace_processing]
    TK[tasks registry + families]
    ENV[environments: workspace, task_env]
    AG[agents: runner, router, providers]
    VE[verification]
    RE[rewards]
    EV[evaluation]
    TR[training: sft, grpo]
  end
  subgraph Contracts
    K[contracts/ Pydantic v2 schemas]
    P[domain/ports.py + job state machine]
  end
  subgraph Infrastructure
    DB[(PostgreSQL / SQLite)]
    OS[(S3 / MinIO / local FS)]
    Q[[Redis/RQ or inline queue]]
    SB[sandbox: local process / Docker]
    OBS[logging, Prometheus, OTel]
  end
  API & CLI & W --> C --> S & J & T
  S --> Modules
  Modules --> K & P
  Infrastructure -. implements .-> P
  C --> Infrastructure
```

Rules enforced by construction:

- `modules/*` import only `contracts`, `domain` and other modules — never SQLAlchemy, boto3, Redis,
  Docker or Transformers at module import time (training imports ML libraries lazily inside functions).
- `RewardEngine` is a pure function of `(VerificationResult, RewardPolicy)`.
- `AgentRunner` receives only the public `TaskSpec` + visible files and **never decides success**.
- `VerificationEngine` reads host-recorded `EpisodeRecord` facts plus oracle data; it never trusts
  agent-supplied labels.
- Route handlers translate HTTP ⇄ services; no domain logic in `api/`.

## Rollout sequence

```mermaid
sequenceDiagram
  participant Client
  participant API
  participant Jobs as JobService/DB
  participant Worker
  participant Runner as AgentRunner
  participant Router as ToolRouter
  participant SBX as Sandbox (host-controlled)
  participant Ver as VerificationEngine
  participant Rew as RewardEngine
  Client->>API: POST /api/v1/rollouts
  API->>Jobs: persist job (CREATED) → QUEUED → enqueue(job_id)
  API-->>Client: 202 {job_id, status_url}
  Worker->>Jobs: CAS QUEUED→RUNNING (lease)
  Worker->>Runner: run_episode(public spec, visible files)
  Runner->>SBX: create workspace
  loop until final report / budget
    Runner->>Router: model tool call (untrusted)
    Router->>Router: allow-list, schema, path policy, budget
    Router->>SBX: file op / visible tests
    Router-->>Runner: structured result (recorded)
  end
  Runner->>SBX: destroy (finally)
  Worker->>Ver: verify(bundle incl. hidden tests, EpisodeRecord)
  Ver->>SBX: fresh isolated run: hidden tests vs candidate + pristine protected files
  Worker->>Rew: compute(verification, policy)
  Worker->>Jobs: persist rollout/trajectory/verification/reward → SUCCEEDED
```

## Trust boundaries

| Boundary | Untrusted side | Controls |
|---|---|---|
| Model output → environment | tool calls, arguments, patches | ToolRouter allow-list, Pydantic schemas, path policy, size/budget limits, protected files |
| Candidate code → host | code executed by tests | Docker provider (no network, read-only, non-root, caps dropped, limits); local provider restricted to trusted demo policies; static allow-list scan before verification; audit-hook guard |
| Agent claims → outcomes | final report, evidence explanations | verdict from hidden tests + state/safety checks; evidence IDs checked against host records |
| Imported traces → datasets | outcome labels, hashes, verification refs | claims kept as `claimed_outcome`; content hash recomputed; dangling verification refs rejected |
| API clients → control plane | requests | API key (mandatory in prod), strict schemas (`extra="forbid"`), size limits, consistent errors |

## Persistence

- Operational metadata in PostgreSQL (SQLite for local/test). 20 tables, Alembic migration `0001`.
- Large/immutable artifacts (raw traces, episode records, Parquet partitions, reports, checkpoints,
  oracle bundles) in object storage, content-addressed, with SHA-256 recorded in `artifacts`.

## Jobs

`CREATED → QUEUED → RUNNING → SUCCEEDED | FAILED | TIMED_OUT | CANCELLED` (plus `RUNNING → QUEUED`
for bounded retries). Transitions are compare-and-set on `(status, version)`. See ADR-010.
