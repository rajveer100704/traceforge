# ADR-010: Observability and recoverable job dispatch

**Status:** accepted

**Decision.** Jobs are persisted before enqueue (outbox semantics). Transitions are compare-and-set.
Workers are idempotent per job id (terminal/claimed jobs ignore deliveries). Retries are bounded and
only for retryable errors. A recovery loop re-dispatches stranded jobs and expires stale leases;
stale sandboxes are reaped by label/age. Correlation ids flow API → job → rollout → trajectory.
Structured JSON logs with secret redaction; low-cardinality Prometheus metrics; optional OTel spans.

**Consequences.** Redis outages or worker crashes delay but do not lose work, and cannot resurrect
terminal jobs.
