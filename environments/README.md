# Task environments

Task families are generated deterministically in code (`src/traceforge/modules/tasks/families/`):

| Directory (logical) | Family | Templates |
|---|---|---|
| `sql_injection` | SQL injection remediation (in-memory SQLite) | t0 f-string lookup · t1 LIKE concatenation · t2 two-parameter `.format` |
| `access_control` | Broken access control | t0 IDOR read · t1 privilege check on update · t2 client-supplied admin flag |
| `ssrf` | SSRF prevention (fake resolver/fetcher, no network) | t0 scheme-only validation · t1 string blocklist · t2 redirect re-validation |

`images/sandbox/Dockerfile` is the runtime image used by the Docker sandbox provider for both
agent-visible test runs and hidden verification. Build it with `make sandbox-image`.
