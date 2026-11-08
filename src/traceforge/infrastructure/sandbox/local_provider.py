"""Local process sandbox for fast tests and deterministic development.

This is NOT an isolation boundary. Candidate code runs as a host subprocess with a minimal
environment, an audit-hook guard, rlimits on POSIX, and a wall-clock timeout. For that reason the
provider is marked ``trusted_code_only``: the agent runner refuses to use it with untrusted model
providers unless ``allow_untrusted_local_execution`` is explicitly enabled.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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

MAX_OUTPUT_BYTES = 256_000


def _posix_limits(budget: ResourceBudget) -> Any:
    def apply() -> None:  # pragma: no cover - executed in child on POSIX only
        if sys.platform == "win32":
            return
        import resource

        mem = budget.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        cpu = int(budget.test_timeout_seconds) + 1
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NPROC, (budget.pids_limit, budget.pids_limit))
        os.setsid()

    return apply


class LocalTestSandboxProvider:
    name = "local"
    isolation = "process"
    trusted_code_only = True

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = (
            Path(base_dir) if base_dir else Path(tempfile.gettempdir()) / "traceforge-sbx"
        ).resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        self._live: dict[str, SandboxHandle] = {}

    # ------------------------------------------------------------------ lifecycle
    def available(self) -> tuple[bool, str]:
        return True, "local process provider (no isolation; trusted code only)"

    def create(
        self, run_id: str, files: Mapping[str, str], budget: ResourceBudget
    ) -> SandboxHandle:
        sandbox_id = f"sbx-{uuid.uuid4().hex[:12]}"
        root = self._base / sandbox_id
        workspace = root / "workspace"
        harness = root / "harness"
        workspace.mkdir(parents=True)
        harness.mkdir()
        (root / "scratch").mkdir()
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
        root = Path(handle.metadata["root"])
        try:
            if root.exists():
                shutil.rmtree(root)
        except OSError as exc:
            handle.state = SandboxState.DESTROY_FAILED
            raise SandboxCleanupError(f"failed to remove {root}: {exc}") from exc
        handle.state = SandboxState.DESTROYED
        self._live.pop(handle.sandbox_id, None)

    def live_sandboxes(self) -> list[str]:
        return sorted(self._live)

    def reap_stale(self, max_age_seconds: float) -> list[str]:
        """Remove sandbox directories left behind by crashed workers."""
        removed: list[str] = []
        now = time.time()
        for path in self._base.glob("sbx-*"):
            if path.name in self._live:
                continue
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    shutil.rmtree(path)
                    removed.append(path.name)
            except OSError:
                continue
        return removed

    # ------------------------------------------------------------------ execution
    def run_tests(
        self, handle: SandboxHandle, test_module: str, selector: str, timeout: float
    ) -> TestRunResult:
        if handle.state != SandboxState.READY:
            raise SandboxError(f"sandbox {handle.sandbox_id} is not ready ({handle.state})")
        root = Path(handle.metadata["root"])
        test_path = (handle.workspace / test_module).resolve()
        if not test_path.is_relative_to(handle.workspace.resolve()):
            raise SandboxError("test module outside workspace")
        return self._execute(
            harness=root / "harness" / "runner.py",
            candidate_root=handle.workspace,
            test_path=test_path,
            selector=selector,
            cwd=root / "scratch",
            budget=handle.budget,
            timeout=timeout,
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
            candidate = root / "candidate"
            hidden = root / "hidden"
            scratch = root / "scratch"
            for d in (candidate, hidden, scratch):
                d.mkdir()
            write_files(candidate, dict(candidate_files))
            write_files(hidden, dict(hidden_files))
            harness = root / "runner.py"
            harness.write_text(runner_source(), encoding="utf-8")
            return self._execute(
                harness=harness,
                candidate_root=candidate,
                test_path=hidden / test_module,
                selector="",
                cwd=scratch,
                budget=budget,
                timeout=budget.test_timeout_seconds,
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def _execute(
        self,
        *,
        harness: Path,
        candidate_root: Path,
        test_path: Path,
        selector: str,
        cwd: Path,
        budget: ResourceBudget,
        timeout: float,
    ) -> TestRunResult:
        run_id = new_run_id()
        nonce = new_nonce()
        env = {"TF_NONCE": nonce, "PYTHONHASHSEED": "0", "PYTHONIOENCODING": "utf-8"}
        for key in ("SYSTEMROOT", "WINDIR"):  # required by CPython on Windows
            if key in os.environ:
                env[key] = os.environ[key]
        cmd = [
            sys.executable,
            "-I",
            "-B",
            str(harness),
            "--root",
            str(candidate_root),
            "--test",
            str(test_path),
            "--selector",
            selector,
        ]
        kwargs: dict[str, Any] = {}
        if os.name == "posix":
            kwargs["preexec_fn"] = _posix_limits(budget)
        started = time.perf_counter()
        timed_out = False
        exit_code: int | None = None
        with tempfile.TemporaryFile(dir=cwd) as out:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                **kwargs,
            )
            try:
                exit_code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait()
            out.seek(0, os.SEEK_END)
            size = out.tell()
            out.seek(max(0, size - MAX_OUTPUT_BYTES))
            output = out.read().decode("utf-8", errors="replace")
        return parse_runner_output(
            run_id=run_id,
            output=output,
            nonce=nonce,
            duration=time.perf_counter() - started,
            timed_out=timed_out,
            exit_code=exit_code,
        )
