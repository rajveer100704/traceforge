# TraceForge platform image (API + workers). Does NOT include the optional ml extra.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# Apply Debian security updates published after the base image was built (e.g. zlib1g
# CVE-2026-85091), then remove setuid/setgid bits (mount, su, passwd, ...): the service never
# needs privilege elevation, which removes the local-privilege-escalation path behind the
# util-linux/shadow CVEs. See docs/security/container-vulnerabilities.md.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /root/.cache \
 && useradd --uid 10001 --create-home --shell /usr/sbin/nologin traceforge \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} +
WORKDIR /app
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 examples ./examples
COPY --chown=10001:10001 configs ./configs
ENV PATH="/app/.venv/bin:$PATH" TRACEFORGE_DATA_DIR=/data PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live')"
CMD ["uvicorn", "traceforge.api.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
