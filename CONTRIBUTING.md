# Contributing

1. `uv sync --frozen` (add `--extra ml` for training work).
2. Keep the dependency direction: `modules/` must not import infrastructure (see docs/architecture.md).
3. Before a PR: `make lint typecheck test` and `uv run traceforge demo` must pass.
4. New task families need: a deterministic seeded generator, visible + hidden tests, a reference fix
   for the demo policy, split assignment, and tests proving the vulnerable code fails hidden security tests.
5. Changing a task, reward policy or verifier behaviour requires a new version (ADR-004).
6. Never add real-world targets, network access in tasks, credentials, or unlicensed data.
7. Report only executed results; mark anything not run as NOT EXECUTED.
