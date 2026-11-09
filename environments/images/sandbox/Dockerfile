# TraceForge task/verifier sandbox image.
# Minimal Python runtime: no compilers, no network tools, non-root user. Code and tests are
# bind-mounted read-only at run time by the host-controlled DockerSandboxProvider.
# Base pinned by digest (python:3.12-slim, resolved 2026-11-08; still the newest digest on
# 2026-11-09). Re-pin deliberately.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# 1. Apply Debian security updates published after the base image was built (e.g. zlib1g
#    CVE-2026-85091). Trade-off: the package set can change between builds of the same digest;
#    the weekly CI re-scan and the vulnerability gate catch regressions.
# 2. Remove setuid/setgid bits: sandboxed code must never be able to elevate privileges
#    (defence in depth on top of no-new-privileges + cap_drop ALL in DockerSandboxProvider).
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /root/.cache \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} +
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT []
CMD ["python", "-c", "print('traceforge sandbox image: run via DockerSandboxProvider')"]
