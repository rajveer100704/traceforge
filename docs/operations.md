# Operations

## Local (no Docker, no GPU)

```bash
uv sync --frozen                 # core + dev
uv run traceforge doctor         # capabilities
uv run traceforge db migrate
uv run traceforge demo           # full lifecycle smoke test
uv run traceforge serve          # http://127.0.0.1:8000/docs
```

Defaults: SQLite at `.traceforge/traceforge.db`, local object store `.traceforge/artifacts`, inline
job queue (background threads inside the API process), local process sandbox (trusted demo only).

## Full stack

```bash
cp .env.example .env              # edit placeholders
docker compose up -d postgres redis minio
export TRACEFORGE_DATABASE_URL=postgresql+psycopg://traceforge:...@localhost:5432/traceforge
export TRACEFORGE_QUEUE_BACKEND=rq TRACEFORGE_STORAGE_BACKEND=s3
uv run traceforge db migrate
uv run traceforge serve &
uv run traceforge worker          # one or more processes; SimpleWorker on Windows
make sandbox-image && export TRACEFORGE_SANDBOX_PROVIDER=docker   # isolated execution
```

`docker compose --profile app up` also runs API + worker containers (they do not mount the Docker
socket, so they cannot use the Docker sandbox; run workers on the host for that).

## Configuration

All settings are environment variables prefixed `TRACEFORGE_` (see `src/traceforge/config.py` and
`.env.example`). `TRACEFORGE_ENV=prod` requires `TRACEFORGE_API_KEY` and forbids untrusted local
execution.

## Recovery behaviour

| Failure | Behaviour |
|---|---|
| Redis down at submit | job persisted (`QUEUED`, not dispatched); recovery loop re-enqueues |
| Worker crash mid-job | lease expires → re-queued (bounded by `max_attempts`) or `FAILED lease_expired` |
| Duplicate / stale delivery | ignored if the job is terminal or claimed (CAS on status+version) |
| Retryable error (connection/timeout, `retryable` errors) | `RUNNING → QUEUED` up to `max_attempts` |
| Non-retryable error | `FAILED` with error code/message |
| Cancellation | `CREATED/QUEUED` → `CANCELLED` immediately; `RUNNING` → cooperative (checked between steps) |
| Sandbox destroy failure | logged with `cleanup_error`; leftover dirs/containers reaped by age |
| Verifier exception | verdict `ERROR`, reward 0 |
| Partially written dataset/object | objects published atomically (temp + rename); manifests written last; identical rebuilds reuse the version |
| Model rate limit / 5xx / timeout | bounded exponential backoff with jitter |

The recovery loop runs inside `traceforge worker` every 60 s; the API runs one recovery pass at startup.

## Observability

- Logs: JSON to stderr with correlation ids; secrets redacted; prompts/observations are not logged.
- Metrics: `GET /metrics` (Prometheus) — `traceforge_*` counters/histograms with low-cardinality labels.
- Tracing: `TRACEFORGE_OTEL_ENABLED=true` (+ `TRACEFORGE_OTEL_CONSOLE_EXPORT=true` for console spans)
  instruments FastAPI and SQLAlchemy.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `capability_unavailable: sandbox 'local' ... trusted demo policies` | use `TRACEFORGE_SANDBOX_PROVIDER=docker` for model providers |
| `docker sandbox unavailable` | start Docker; `make sandbox-image` |
| `/health/ready` 503 `migrations_current=false` | `traceforge db migrate` |
| training exits with code 2 | capability blocked; read the message (`traceforge doctor` shows modes) |
| `no trainable examples` | increase `max_length`; examples lost all assistant targets to truncation |

## Data retention

Raw traces are retained in object storage (configurable `TRACEFORGE_RAW_RETENTION_DAYS` for external
lifecycle rules). `make clean` never deletes data; `make clean-data` is the explicit destructive target.

## Task versions and rolling upgrades (verifier 1.1.0+)

Each instance builds an in-memory task registry: the **current** task version (new work,
listings, benchmark selection) plus **historical** versions kept only for re-verifying rollouts
recorded against them. Today that means current 1.1.0 and historical 1.0.0. Historical bundles are
rebuilt by a frozen legacy renderer, and registration fails if a rebuilt bundle's fixture hash
differs from the hash pinned from the original release.

Guarantees:

- **No version substitution.** A rollout request is pinned to a concrete task version when it is
  accepted (`RolloutRequest.task_version`), so a queued or retried job always runs the version it
  was accepted with. An instance asked for a version it cannot execute raises
  `UnsupportedTaskVersion` (HTTP 409, with `available` versions). It never runs a different version.
- **Only executable versions are advertised.** `GET /api/v1/tasks` lists only the versions this
  instance runs as current, even if other instances have synced newer rows into the shared
  database. `GET /api/v1/tasks/{id}` resolves through the instance's own registry.
- **Idempotent, concurrency-safe sync.** `TaskService.sync_registry` uses
  `INSERT ... ON CONFLICT DO NOTHING` on the unique keys (SQLite and PostgreSQL), then re-reads and
  validates the fixture hash. Concurrent syncs of the same version all succeed with one row; a
  different hash under an existing `(task_id, version)` raises `ConflictError`.
- **Visible skew.** `/health/ready` reports `checks.task_versions` (what this instance can execute)
  and `checks.task_version_skew` (versions in the shared DB it cannot execute). Skew is
  informational: it does not make the instance unready.

Safe rolling-upgrade order (e.g. 1.0.0 → 1.1.0):

1. Upgrade **workers** first. New workers execute both 1.1.0 and 1.0.0, so they can finish jobs
   accepted by old API instances.
2. Then upgrade **API instances**. Until an API instance is upgraded it keeps accepting, and
   pinning, 1.0.0 jobs, which the new workers still execute.
3. Old workers that receive a 1.1.0 job fail it explicitly instead of running 1.0.0. v0.2 workers
   raise `NotFoundError`; this release raises `UnsupportedTaskVersion`. Drain old workers before
   upgrading the API if you want to avoid those failures.
4. Watch `task_version_skew` on each instance until it is empty everywhere.

Caveat for the first upgrade away from v0.2: v0.2 API instances do not pin a version at
acceptance. A job they accepted without an explicit `task_version` is resolved when it executes,
so an upgraded worker runs it as 1.1.0. To keep such jobs on 1.0.0, drain the v0.2 queue before
upgrading workers, or submit with an explicit `task_version`. From this release on, every accepted
job is pinned.

Re-verifying a rollout whose task version is no longer bundled (beyond the historical set) fails
with an actionable error. Use the TraceForge commit recorded in the rollout's reproducibility
bundle.
