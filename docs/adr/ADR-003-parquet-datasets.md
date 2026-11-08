# ADR-003: Parquet for dataset partitions

**Status:** accepted

**Decision.** Dataset partitions are Parquet (PyArrow, snappy, fixed schema, statistics disabled for
byte-stable output). Messages are stored as canonical JSON strings.

**Consequences.** Columnar, typed, widely supported by HF `datasets`; partition hashes are reproducible
for identical inputs (verified by tests).
