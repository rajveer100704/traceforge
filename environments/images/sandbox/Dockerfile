# TraceForge task/verifier sandbox image.
# Minimal Python runtime: no compilers, no network tools, non-root user. Code and tests are
# bind-mounted read-only at run time by the host-controlled DockerSandboxProvider.
# Base pinned by digest (python:3.12-slim, resolved 2026-11-08; still the newest digest on
# 2026-11-14). Re-pin deliberately.

# --- Stage 1: SQLite built from verified upstream source ------------------------------------
# Debian 13 ships SQLite 3.46.1 with FTS5 and the session extension (CVE-2026-119373/119374/
# 119379; no Debian fix, fixed upstream in 3.54.0). Candidate code in the sandbox can run
# arbitrary SQL, so the sandbox uses SQLite 3.54.1 compiled WITHOUT FTS3/4/5, R-Tree and
# session, with compile-time maxima that in-process code cannot raise (no attached databases,
# bounded SQL/expression/compound/LIKE/blob sizes). The tarball is verified against the SHA3-256
# published on https://sqlite.org/download.html. Compiler packages exist only in this stage.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS sqlite-build
ARG SQLITE_VERSION=3.54.1
ARG SQLITE_URL=https://sqlite.org/2026/sqlite-autoconf-3540100.tar.gz
ARG SQLITE_SHA3_256=b6ced585c750916b1052667239a12c25a7ff34e7f06e1e6e01ecb0427657d9c4
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN python -c "import hashlib, sys, urllib.request; \
data = urllib.request.urlopen(sys.argv[1], timeout=120).read(); \
digest = hashlib.sha3_256(data).hexdigest(); \
sys.exit('SQLite source SHA3-256 mismatch: ' + digest) if digest != sys.argv[2] else None; \
open('sqlite.tar.gz', 'wb').write(data)" "$SQLITE_URL" "$SQLITE_SHA3_256" \
 && tar -xzf sqlite.tar.gz --strip-components=1 \
 && gcc -O2 -fPIC -shared -o libsqlite3.so.0 sqlite3.c \
      -Wl,-soname,libsqlite3.so.0 -lm -lpthread \
      -DSQLITE_THREADSAFE=1 -DHAVE_USLEEP=1 -DSQLITE_TRUSTED_SCHEMA=0 \
      -DSQLITE_ENABLE_MATH_FUNCTIONS \
      -DSQLITE_MAX_ATTACHED=0 -DSQLITE_MAX_SQL_LENGTH=1000000 -DSQLITE_MAX_EXPR_DEPTH=200 \
      -DSQLITE_MAX_COMPOUND_SELECT=100 -DSQLITE_MAX_LIKE_PATTERN_LENGTH=10000 \
      -DSQLITE_MAX_LENGTH=100000000 \
 && strip --strip-unneeded libsqlite3.so.0

# --- Stage 2: sandbox runtime ----------------------------------------------------------------
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ARG SQLITE_VERSION=3.54.1
LABEL org.traceforge.sqlite.version="3.54.1" \
      org.traceforge.sqlite.source="https://sqlite.org/2026/sqlite-autoconf-3540100.tar.gz" \
      org.traceforge.sqlite.source-sha3-256="b6ced585c750916b1052667239a12c25a7ff34e7f06e1e6e01ecb0427657d9c4"
COPY --from=sqlite-build /build/libsqlite3.so.0 /usr/local/lib/libsqlite3.so.0

# 1. Apply Debian security updates published after the base image was built (e.g. zlib1g
#    CVE-2026-85091). Trade-off: the package set can change between builds of the same digest;
#    the weekly CI re-scan and the vulnerability gate catch regressions.
# 2. Replace Debian's libsqlite3-0 with the build above. `dpkg --purge` (not apt) fails the
#    build if any installed package still depends on it, instead of removing more packages.
#    Trivy cannot see the source-built library; CI asserts its version and options instead,
#    and SQLite advisories are tracked manually (docs/security/container-vulnerabilities.md).
# 3. Remove setuid/setgid bits: sandboxed code must never be able to elevate privileges
#    (defence in depth on top of no-new-privileges + cap_drop ALL in DockerSandboxProvider).
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && dpkg --purge libsqlite3-0 \
 && ldconfig \
 && python -c "import sqlite3, sys; \
v = sqlite3.sqlite_version; opts = [r[0] for r in sqlite3.connect(':memory:').execute('PRAGMA compile_options')]; \
bad = [o for o in opts if any(k in o for k in ('FTS3', 'FTS4', 'FTS5', 'RTREE', 'SESSION'))]; \
sys.exit('unexpected SQLite build: %s %s' % (v, bad)) if v != sys.argv[1] or bad or 'MAX_ATTACHED=0' not in opts else print('sqlite', v)" "$SQLITE_VERSION" \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /root/.cache \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} +
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT []
CMD ["python", "-c", "print('traceforge sandbox image: run via DockerSandboxProvider')"]
