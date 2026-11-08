# Developer workflow. On Windows without make, run the `uv run ...` commands directly.
.PHONY: install install-ml services-up services-down migrate lint format typecheck test test-unit \
        test-integration test-security demo serve worker experiments sandbox-image audit clean clean-data

install:            ## core + dev dependencies (locked)
	uv sync --frozen
install-ml:         ## add optional ML extra (torch/transformers/trl/peft)
	uv sync --frozen --extra ml
services-up:        ## postgres, redis, minio
	docker compose up -d postgres redis minio
services-down:
	docker compose down
migrate:
	uv run traceforge db migrate
lint:
	uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
format:
	uv run ruff format src tests scripts && uv run ruff check --fix src tests scripts
typecheck:
	uv run mypy
test:
	uv run pytest
test-unit:
	uv run pytest tests/unit tests/property tests/contract
test-integration:
	uv run pytest tests/integration tests/end_to_end
test-security:
	uv run pytest tests/security
demo:               ## full lifecycle smoke test; no GPU/API/Docker needed
	uv run traceforge demo --profile local
serve:
	uv run traceforge serve
worker:
	TRACEFORGE_QUEUE_BACKEND=rq uv run traceforge worker
experiments:        ## research experiments A-E (writes to .traceforge/reports/experiments)
	for e in A B C D E; do uv run traceforge experiment run $$e || exit 1; done
sandbox-image:
	docker build -t traceforge-sandbox:0.1 environments/images/sandbox
audit:
	uv run pip-audit && uv run bandit -q -r src -c pyproject.toml
clean:              ## caches only — never deletes data or volumes
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis htmlcov .coverage dist build
clean-data:         ## DESTRUCTIVE: deletes local .traceforge data (DB, artifacts, reports)
	@echo "This deletes ./.traceforge (local DB, artifacts, reports). Ctrl-C to abort."; sleep 5
	rm -rf .traceforge
