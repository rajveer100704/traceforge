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
