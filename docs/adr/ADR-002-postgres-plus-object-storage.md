# ADR-002: PostgreSQL for metadata, object storage for artifacts

**Status:** accepted

**Decision.** Typed, integrity-sensitive metadata (jobs, runs, verifications, rewards, lineage) in
PostgreSQL via SQLAlchemy 2 + Alembic. Large or immutable blobs (raw traces, episodes, Parquet,
reports, checkpoints) in S3-compatible storage (MinIO locally) with SHA-256 recorded in `artifacts`.
SQLite + local filesystem are supported for local/test mode with the same migrations.

**Consequences.** No large blobs in the database; artifacts are content-addressed and write-once.
