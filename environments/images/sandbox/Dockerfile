# TraceForge task/verifier sandbox image.
# Minimal Python runtime: no compilers, no network tools, non-root user. Code and tests are
# bind-mounted read-only at run time by the host-controlled DockerSandboxProvider.
# Base pinned by digest (python:3.12-slim, resolved 2026-11-08). Re-pin deliberately.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox  && rm -rf /var/lib/apt/lists/* /root/.cache
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT []
CMD ["python", "-c", "print('traceforge sandbox image: run via DockerSandboxProvider')"]
