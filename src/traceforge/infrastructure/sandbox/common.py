"""Helpers shared by sandbox providers."""

from __future__ import annotations

import json
import secrets
from importlib import resources
from pathlib import Path

from traceforge.contracts.common import TraceForgeError, sha256_text
from traceforge.domain.ports import TestCaseResult, TestRunResult

MAX_OUTPUT_CHARS = 200_000
RESULT_PREFIX = "<<TF-RESULT-"


class SandboxError(TraceForgeError):
    pass


class SandboxCleanupError(SandboxError):
    retryable = True


def runner_source() -> str:
    return (
        resources.files("traceforge.modules.tasks.harness")
        .joinpath("runner.py")
        .read_text(encoding="utf-8")
    )


def new_nonce() -> str:
    return secrets.token_hex(16)


def new_run_id() -> str:
    return "run-" + secrets.token_hex(6)


def write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = (root / rel).resolve()
        if not target.is_relative_to(root.resolve()):
            raise SandboxError(f"refusing to write outside sandbox root: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")


def parse_runner_output(
    *,
    run_id: str,
    output: str,
    nonce: str,
    duration: float,
    timed_out: bool,
    exit_code: int | None,
) -> TestRunResult:
    output = output[-MAX_OUTPUT_CHARS:]
    marker = f"{RESULT_PREFIX}{nonce}>>"
    payload = None
    for line in reversed(output.splitlines()):
        if line.startswith(marker):
            try:
                payload = json.loads(line[len(marker) :])
            except json.JSONDecodeError:
                payload = None
            break
    tail = output[-2000:]
    if payload is None:
        return TestRunResult(
            run_id=run_id,
            total=0,
            passed=0,
            failed=0,
            errors=0,
            cases=(),
            duration_seconds=duration,
            timed_out=timed_out,
            exit_code=exit_code,
            harness_ok=False,
            violations=(),
            output_tail=tail,
            output_sha256=sha256_text(output),
            result_parsed=False,
        )
    cases = tuple(
        TestCaseResult(
            name=str(c.get("name", "?")),
            status=str(c.get("status", "error")),
            message=str(c.get("message", ""))[:600],
        )
        for c in payload.get("cases", [])
    )
    harness_error = str(payload.get("harness_error", ""))
    return TestRunResult(
        run_id=run_id,
        total=len(cases),
        passed=sum(c.status == "passed" for c in cases),
        failed=sum(c.status == "failed" for c in cases),
        errors=sum(c.status == "error" for c in cases),
        cases=cases,
        duration_seconds=duration,
        timed_out=timed_out,
        exit_code=exit_code,
        harness_ok=bool(payload.get("harness_ok", False)) and not timed_out,
        violations=tuple(str(v) for v in payload.get("violations", [])),
        output_tail=(harness_error + "\n" + str(payload.get("captured_output_tail", "")))[-2000:],
        output_sha256=sha256_text(output),
    )
