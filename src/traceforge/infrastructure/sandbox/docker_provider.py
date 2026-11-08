"""Docker sandbox provider (host-controlled; agents never receive the Docker API).

Every test/verifier execution runs in a fresh, short-lived container with:
non-root user, read-only root filesystem, all capabilities dropped, no-new-privileges, the
default seccomp profile, ``network_mode=none``, CPU/memory/PID limits, a size-limited noexec
tmpfs, read-only bind mounts of the workspace/candidate/hidden tests, bounded log capture, and
forced removal in ``finally``. Containers carry labels so a reaper can remove leftovers after a
worker crash.

Docker is not a complete security boundary for hostile multi-tenant workloads; use a stronger
isolation layer (e.g. microVMs) before running untrusted third-party code in production.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from traceforge.contracts.tasks import ResourceBudget
from traceforge.domain.ports import SandboxHandle, SandboxState, TestRunResult
from traceforge.infrastructure.sandbox.common import (
    SandboxCleanupError,
    SandboxError,
    new_nonce,
    new_run_id,
    parse_runner_output,
    runner_source,
    write_files,
)

LABEL = "traceforge.sandbox"
SANDBOX_USER = "65534:65534"  # nobody
LOG_TAIL_LINES = 2000


def container_kwargs(
    image: str,
    budget: ResourceBudget,
    mounts: dict[str, dict[str, str]],
    command: list[str],
    nonce: str,
    labels: dict[str, str],
) -> dict[str, Any]:
    """Security-relevant container configuration (unit-tested without a daemon)."""
    return {
        "image": image,
        "command": command,
        "user": SANDBOX_USER,
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "network_mode": "none",
        "mem_limit": f"{budget.memory_mb}m",
        "memswap_limit": f"{budget.memory_mb}m",
        "nano_cpus": int(budget.cpu_limit * 1e9),
        "pids_limit": budget.pids_limit,
        "tmpfs": {"/tmp": "rw,noexec,nosuid,size=16m"},  # noqa: S108  # nosec B108 - in-container tmpfs
        "volumes": mounts,
        "working_dir": "/tmp",  # noqa: S108  # nosec B108 - in-container path
        "environment": {"TF_NONCE": nonce, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"},
        "labels": {LABEL: "1", **labels},
        "detach": True,
        "stdin_open": False,
        "tty": False,
        "privileged": False,
        "ipc_mode": "none",
        "log_config": {"type": "json-file", "config": {"max-size": "1m", "max-file": "1"}},
    }


class DockerSandboxProvider:
    name = "docker"
    isolation = "container"
    trusted_code_only = False

    def __init__(self, image: str, base_dir: Path | None = None, client: Any = None) -> None:
        self.image = image
        self._base = (
            Path(base_dir) if base_dir else Path(tempfile.gettempdir()) / "traceforge-docker"
        ).resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._live: dict[str, SandboxHandle] = {}

    def _docker(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def available(self) -> tuple[bool, str]:
        try:
            client = self._docker()
            client.ping()
        except Exception as exc:
            return False, f"docker daemon not reachable: {type(exc).__name__}"
        try:
            client.images.get(self.image)
        except Exception:
            return False, f"sandbox image {self.image} not built (make sandbox-image)"
        return True, f"docker daemon reachable; image {self.image}"

    # ------------------------------------------------------------------ lifecycle
    def create(
        self, run_id: str, files: Mapping[str, str], budget: ResourceBudget
    ) -> SandboxHandle:
        sandbox_id = f"sbx-{uuid.uuid4().hex[:12]}"
        root = self._base / sandbox_id
        workspace, harness = root / "workspace", root / "harness"
        workspace.mkdir(parents=True)
        harness.mkdir()
        write_files(workspace, dict(files))
        (harness / "runner.py").write_text(runner_source(), encoding="utf-8")
        handle = SandboxHandle(
            sandbox_id=sandbox_id,
            provider=self.name,
            workspace=workspace,
            budget=budget,
            state=SandboxState.READY,
            metadata={"root": str(root), "run_id": run_id},
        )
        self._live[sandbox_id] = handle
        return handle

    def destroy(self, handle: SandboxHandle) -> None:
        if handle.state == SandboxState.DESTROYED:
            return
        errors = []
        try:
            for c in self._docker().containers.list(
                all=True, filters={"label": f"traceforge.sandbox_id={handle.sandbox_id}"}
            ):
                c.remove(force=True)
        except Exception as exc:
            errors.append(f"containers: {exc}")
        try:
            shutil.rmtree(handle.metadata["root"], ignore_errors=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            errors.append(f"workspace: {exc}")
        if errors:
            handle.state = SandboxState.DESTROY_FAILED
            raise SandboxCleanupError("; ".join(errors))
        handle.state = SandboxState.DESTROYED
        self._live.pop(handle.sandbox_id, None)

    def reap_stale(self, max_age_seconds: float) -> list[str]:
        removed = []
        now = time.time()
        for c in self._docker().containers.list(all=True, filters={"label": f"{LABEL}=1"}):
            created = c.attrs.get("Created", "")
            try:
                from datetime import datetime

                age = now - datetime.fromisoformat(created[:19]).timestamp()
            except ValueError:
                age = max_age_seconds + 1
            if age > max_age_seconds:
                c.remove(force=True)
                removed.append(c.id[:12])
        return removed

    # ------------------------------------------------------------------ execution
    def _run(
        self,
        mounts: dict[str, dict[str, str]],
        test_path: str,
        candidate_root: str,
        selector: str,
        budget: ResourceBudget,
        timeout: float,
        labels: dict[str, str],
    ) -> TestRunResult:
        nonce, run_id = new_nonce(), new_run_id()
        command = [
            "python",
            "-I",
            "-B",
            "/harness/runner.py",
            "--root",
            candidate_root,
            "--test",
            test_path,
            "--selector",
            selector,
        ]
        kwargs = container_kwargs(
            self.image, budget, mounts, command, nonce, {**labels, "traceforge.run": run_id}
        )
        started = time.perf_counter()
        container = None
        timed_out = False
        exit_code: int | None = None
        output = ""
        try:
            container = self._docker().containers.run(**kwargs)
            try:
                status = container.wait(timeout=timeout)
                exit_code = int(status.get("StatusCode", -1))
            except Exception:
                timed_out = True
                container.kill()
            output = container.logs(stdout=True, stderr=True, tail=LOG_TAIL_LINES).decode(
                "utf-8", "replace"
            )
        except Exception as exc:
            if container is None:
                raise SandboxError(f"failed to start sandbox container: {exc}") from exc
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception as exc:  # the label-based reaper will collect it
                    logging.getLogger(__name__).warning(
                        "container removal deferred to reaper: %s", exc
                    )
        return parse_runner_output(
            run_id=run_id,
            output=output,
            nonce=nonce,
            duration=time.perf_counter() - started,
            timed_out=timed_out,
            exit_code=exit_code,
        )

    def run_tests(
        self, handle: SandboxHandle, test_module: str, selector: str, timeout: float
    ) -> TestRunResult:
        if handle.state != SandboxState.READY:
            raise SandboxError(f"sandbox {handle.sandbox_id} is not ready")
        root = Path(handle.metadata["root"])
        mounts = {
            str(handle.workspace.resolve()): {"bind": "/workspace", "mode": "ro"},
            str((root / "harness").resolve()): {"bind": "/harness", "mode": "ro"},
        }
        return self._run(
            mounts,
            f"/workspace/{test_module}",
            "/workspace",
            selector,
            handle.budget,
            timeout,
            {"traceforge.sandbox_id": handle.sandbox_id},
        )

    def run_isolated_verifier(
        self,
        candidate_files: Mapping[str, str],
        hidden_files: Mapping[str, str],
        test_module: str,
        budget: ResourceBudget,
    ) -> TestRunResult:
        root = Path(tempfile.mkdtemp(prefix="tf-verify-", dir=self._base))
        try:
            for d in ("candidate", "hidden", "harness"):
                (root / d).mkdir()
            write_files(root / "candidate", dict(candidate_files))
            write_files(root / "hidden", dict(hidden_files))
            (root / "harness" / "runner.py").write_text(runner_source(), encoding="utf-8")
            mounts = {
                str((root / "candidate").resolve()): {"bind": "/candidate", "mode": "ro"},
                str((root / "hidden").resolve()): {"bind": "/hidden", "mode": "ro"},
                str((root / "harness").resolve()): {"bind": "/harness", "mode": "ro"},
            }
            return self._run(
                mounts,
                f"/hidden/{test_module}",
                "/candidate",
                "",
                budget,
                budget.test_timeout_seconds,
                {"traceforge.role": "verifier"},
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)
