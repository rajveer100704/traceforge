"""Task environment: the host-side implementation of the agent tool surface.

The environment receives only the *public* TaskSpec and the agent-visible workspace files.
It never sees oracle material; hidden tests live with the verifier.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from traceforge.contracts.actions import (
    ApplyPatchInput,
    InspectTestOutputInput,
    ListFilesInput,
    ReadFileInput,
    RunVisibleTestsInput,
    SubmitEvidenceInput,
    SubmitFinalReportInput,
)
from traceforge.contracts.common import ErrorCode, JSONValue, sha256_text
from traceforge.contracts.observations import (
    EpisodeRecord,
    EvidenceSubmission,
    FinalReport,
    PatchRecord,
    RejectedAction,
    TestRunRecord,
)
from traceforge.contracts.tasks import TaskSpec
from traceforge.domain.ports import SandboxHandle, SandboxProvider, SandboxState, TestRunResult
from traceforge.modules.environments.workspace import ToolError, Workspace

VISIBLE_TEST_MODULE = "tests/test_visible.py"
DEFAULT_PROTECTED = ("tests/*", "TASK.md", "app/__init__.py")
MAX_EVIDENCE = 20


@dataclass
class EnvironmentPolicy:
    protect_visible_tests: bool = True
    max_file_bytes: int = 64_000

    def protected_globs(self) -> tuple[str, ...]:
        if self.protect_visible_tests:
            return DEFAULT_PROTECTED
        # Research-only permissive harness used to demonstrate visible-test reward hacking.
        return ("TASK.md", "app/__init__.py")


@dataclass
class EpisodeState:
    sequence: int = 0
    tool_calls: int = 0
    test_runs: dict[str, TestRunRecord] = field(default_factory=dict)
    raw_test_results: dict[str, TestRunResult] = field(default_factory=dict)
    patches: dict[str, PatchRecord] = field(default_factory=dict)
    file_reads: list[str] = field(default_factory=list)
    evidence: list[EvidenceSubmission] = field(default_factory=list)
    rejected: list[RejectedAction] = field(default_factory=list)
    final_report: FinalReport | None = None
    terminal: bool = False
    termination_reason: str = "running"


class TaskEnvironment:
    def __init__(
        self,
        spec: TaskSpec,
        workspace_files: dict[str, str],
        sandbox: SandboxProvider,
        run_id: str,
        policy: EnvironmentPolicy | None = None,
    ) -> None:
        self.spec = spec
        self._files = dict(workspace_files)
        self.sandbox = sandbox
        self.run_id = run_id
        self.policy = policy or EnvironmentPolicy()
        self.state = EpisodeState()
        self.handle: SandboxHandle | None = None
        self.workspace: Workspace | None = None
        self._started = 0.0
        self.original_hashes = {p: sha256_text(c) for p, c in self._files.items()}

    # ------------------------------------------------------------------ lifecycle
    def reset(self) -> dict[str, JSONValue]:
        if self.handle is not None:
            raise RuntimeError("environment already reset; create a new environment per episode")
        self.handle = self.sandbox.create(self.run_id, self._files, self.spec.budget)
        editable = frozenset(
            f.path
            for f in self.spec.visible_files
            if f.editable or not self.policy.protect_visible_tests
        )
        self.workspace = Workspace(
            self.handle.workspace,
            editable=editable,
            protected_globs=self.policy.protected_globs(),
            max_file_bytes=self.policy.max_file_bytes,
        )
        self._started = time.monotonic()
        return {
            "task_id": self.spec.task_id,
            "objective": self.spec.objective,
            "files": [f.path for f in self.spec.visible_files],
            "editable": sorted(editable),
            "tools": list(self.spec.allowed_tools),
        }

    def close(self) -> None:
        if self.handle is not None and self.handle.state != SandboxState.DESTROYED:
            self.sandbox.destroy(self.handle)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started if self._started else 0.0

    def _ws(self) -> Workspace:
        if self.workspace is None:
            raise ToolError(ErrorCode.TERMINAL_STATE, "environment not initialised")
        return self.workspace

    def next_sequence(self) -> int:
        self.state.sequence += 1
        return self.state.sequence

    def record_rejection(self, seq: int, tool: str, code: ErrorCode, message: str) -> None:
        self.state.rejected.append(
            RejectedAction(
                sequence_number=seq, tool_name=tool, code=code.value, message=message[:500]
            )
        )

    def terminate(self, reason: str) -> None:
        self.state.terminal = True
        if self.state.termination_reason == "running":
            self.state.termination_reason = reason

    # ------------------------------------------------------------------ tools
    def list_files(self, args: ListFilesInput, seq: int) -> JSONValue:
        return {"entries": self._ws().list(args.path)}

    def read_file(self, args: ReadFileInput, seq: int) -> JSONValue:
        norm, content = self._ws().read(args.path)
        read_id = f"read-{seq}"
        self.state.file_reads.append(read_id)
        return {"path": norm, "content": content, "read_id": read_id}

    def apply_patch(self, args: ApplyPatchInput, seq: int) -> JSONValue:
        ws = self._ws()
        norm, path = ws.check_writable(args.path)
        current = path.read_text(encoding="utf-8")
        if args.mode == "replace_file":
            if args.content is None:
                raise ToolError(ErrorCode.MALFORMED_ACTION, "replace_file requires content")
            updated = args.content
        else:
            if args.old is None or args.new is None:
                raise ToolError(ErrorCode.MALFORMED_ACTION, "search_replace requires old and new")
            count = current.count(args.old)
            if count != 1:
                raise ToolError(
                    ErrorCode.PATCH_FAILED,
                    f"search text must occur exactly once (found {count})",
                    occurrences=count,
                )
            updated = current.replace(args.old, args.new)
        norm, before, after = ws.write(norm, updated)
        patch_id = f"patch-{seq}-{secrets.token_hex(3)}"
        self.state.patches[patch_id] = PatchRecord(
            patch_id=patch_id,
            sequence_number=seq,
            path=norm,
            before_sha256=before,
            after_sha256=after,
        )
        return {"patch_id": patch_id, "path": norm, "changed": before != after}

    def run_visible_tests(self, args: RunVisibleTestsInput, seq: int) -> JSONValue:
        if self.handle is None:
            raise ToolError(ErrorCode.TERMINAL_STATE, "environment not initialised")
        timeout = min(self.spec.budget.test_timeout_seconds, self.remaining_seconds())
        if timeout <= 0:
            raise ToolError(ErrorCode.BUDGET_EXCEEDED, "no wall-clock budget left for tests")
        result = self.sandbox.run_tests(
            self.handle, VISIBLE_TEST_MODULE, args.test_selector, timeout
        )
        record = TestRunRecord(
            run_id=result.run_id,
            sequence_number=seq,
            selector=args.test_selector,
            total=result.total,
            passed=result.passed,
            failed=result.failed,
            errors=result.errors,
            timed_out=result.timed_out,
            harness_ok=result.harness_ok,
            after_patch_count=len(self.state.patches),
            output_sha256=result.output_sha256,
            violations=result.violations,
            case_status={c.name: c.status for c in result.cases},
        )
        self.state.test_runs[result.run_id] = record
        self.state.raw_test_results[result.run_id] = result
        return {
            "run_id": result.run_id,
            "passed": result.passed,
            "failed": result.failed,
            "errors": result.errors,
            "total": result.total,
            "timed_out": result.timed_out,
            "harness_ok": result.harness_ok,
        }

    def inspect_test_output(self, args: InspectTestOutputInput, seq: int) -> JSONValue:
        result = self.state.raw_test_results.get(args.run_id)
        if result is None:
            raise ToolError(ErrorCode.NOT_FOUND, f"unknown run_id {args.run_id}")
        return {
            "run_id": result.run_id,
            "cases": [
                {"name": c.name, "status": c.status, "message": c.message} for c in result.cases
            ],
            "output_tail": result.output_tail[-1500:],
        }

    def submit_evidence(self, args: SubmitEvidenceInput, seq: int) -> JSONValue:
        if len(self.state.evidence) >= MAX_EVIDENCE:
            raise ToolError(ErrorCode.BUDGET_EXCEEDED, "too many evidence submissions")
        # Recorded verbatim; the evidence verifier later checks it against host records.
        self.state.evidence.append(
            EvidenceSubmission(
                sequence_number=seq,
                evidence_type=args.evidence_type,
                artifact_id=args.artifact_id,
                explanation=args.explanation,
            )
        )
        return {"accepted_for_review": True, "evidence_count": len(self.state.evidence)}

    def submit_final_report(self, args: SubmitFinalReportInput, seq: int) -> JSONValue:
        self.state.final_report = FinalReport(
            sequence_number=seq, summary=args.summary, claimed_success=args.claimed_success
        )
        self.terminate("final_report")
        return {"received": True, "note": "outcome is determined by independent verification"}

    def remaining_seconds(self) -> float:
        return self.spec.budget.max_wall_seconds - self.elapsed

    # ------------------------------------------------------------------ snapshot
    def episode_record(self, *, metadata: dict[str, JSONValue] | None = None) -> EpisodeRecord:
        files = self._ws().snapshot() if self.workspace is not None else dict(self._files)
        return EpisodeRecord(
            task_id=self.spec.task_id,
            task_version=self.spec.version,
            run_id=self.run_id,
            sandbox_provider=self.sandbox.name,
            environment_ref=self.spec.environment_ref,
            original_hashes=self.original_hashes,
            final_files=files,
            test_runs=tuple(self.state.test_runs.values()),
            patches=tuple(self.state.patches.values()),
            file_reads=tuple(self.state.file_reads),
            evidence=tuple(self.state.evidence),
            rejected_actions=tuple(self.state.rejected),
            final_report=self.state.final_report,
            tool_calls=self.state.tool_calls,
            max_actions=self.spec.budget.max_actions,
            duration_seconds=self.elapsed,
            termination_reason=self.state.termination_reason,
            protected_tests=self.policy.protect_visible_tests,
            metadata=metadata or {},
        )
