# ADR-001: Modular monolith before microservices

**Status:** accepted

**Context.** v0.1 is a single-team research platform. Subsystems (traces, tasks, runtime,
verification, rewards, training, evaluation) change together and share contracts.

**Decision.** One Python package with explicit layers (contracts → domain/ports → modules →
application → api/cli/workers). Long-running work runs in separate worker processes.

**Consequences.** Simple deployment and refactoring; boundaries are enforced by import direction and
ports, so a subsystem can be extracted later. A single process failure domain for the API.
