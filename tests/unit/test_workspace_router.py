"""Path safety, tool authorisation and router fail-closed behaviour."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from traceforge.contracts.actions import ToolCall
from traceforge.contracts.common import ErrorCode
from traceforge.contracts.traces import ExecutionStatus
from traceforge.modules.agents.budget import BudgetManager
from traceforge.modules.agents.recorder import TrajectoryRecorder
from traceforge.modules.agents.tool_registry import ToolRegistry
from traceforge.modules.agents.tool_router import ToolRouter
from traceforge.modules.environments.task_env import TaskEnvironment
from traceforge.modules.environments.workspace import ToolError, Workspace

TRAVERSALS = [
    "../x",
    "../../etc/passwd",
    "/etc/passwd",
    "C:/Windows/win.ini",
    "C:\\x",
    "app/../../x",
    "~/x",
    "app\\x.py",
    "a\x00b",
    "",
]


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "m.py").write_text("x = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_visible.py").write_text("def test_a(): pass\n")
    (tmp_path.parent / "secret.txt").write_text("s3cr3t")
    return Workspace(tmp_path, editable=frozenset({"app/m.py"}), protected_globs=("tests/*",))


@pytest.mark.parametrize("path", TRAVERSALS)
def test_traversal_rejected(ws: Workspace, path: str) -> None:
    with pytest.raises(ToolError) as e:
        ws.read(path)
    assert e.value.code == ErrorCode.PATH_VIOLATION


def test_symlink_escape_rejected(ws: Workspace, tmp_path: Path) -> None:
    link = tmp_path / "app" / "link.txt"
    try:
        os.symlink(tmp_path.parent / "secret.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    with pytest.raises(ToolError):
        ws.read("app/link.txt")


def test_write_policy(ws: Workspace) -> None:
    with pytest.raises(ToolError) as e:
        ws.write("tests/test_visible.py", "")
    assert e.value.code == ErrorCode.PROTECTED_PATH
    with pytest.raises(ToolError):
        ws.write("app/new.py", "x")  # not an existing editable file
    with pytest.raises(ToolError) as e:
        ws.write("app/m.py", "x" * 70_000)
    assert e.value.code == ErrorCode.SIZE_LIMIT
    norm, before, after = ws.write("app/m.py", "x = 2\n")
    assert norm == "app/m.py" and before != after


class SpySandbox:
    """Records every call: proves rejected actions never reach the sandbox provider."""

    name, isolation, trusted_code_only = "spy", "process", True

    def __init__(self, inner):  # type: ignore[no-untyped-def]
        self.inner, self.calls = inner, []

    def create(self, *a, **k):  # type: ignore[no-untyped-def]
        return self.inner.create(*a, **k)

    def destroy(self, h):  # type: ignore[no-untyped-def]
        return self.inner.destroy(h)

    def run_tests(self, *a, **k):  # type: ignore[no-untyped-def]
        self.calls.append("run_tests")
        return self.inner.run_tests(*a, **k)


def make_router(bundle, sandbox, allowed=None, max_actions=None):  # type: ignore[no-untyped-def]
    spec = bundle.spec
    if max_actions:
        spec = spec.model_copy(
            update={"budget": spec.budget.model_copy(update={"max_actions": max_actions})}
        )
    env = TaskEnvironment(spec, dict(bundle.workspace_files), sandbox, "r1")
    env.reset()
    rec = TrajectoryRecorder(
        source_id="t", task_id=spec.task_id, task_version=spec.version, model_id="m"
    )
    return (
        env,
        rec,
        ToolRouter(
            env, ToolRegistry(allowed or spec.allowed_tools), BudgetManager(spec.budget), rec, "c"
        ),
    )


@pytest.mark.parametrize(
    "call,code",
    [
        (ToolCall(call_id="1", tool_name="launch_shell", arguments={}), ErrorCode.UNKNOWN_TOOL),
        (
            ToolCall(call_id="1", tool_name="run_visible_tests", arguments="{bad json"),
            ErrorCode.MALFORMED_ACTION,
        ),
        (
            ToolCall(
                call_id="1",
                tool_name="run_visible_tests",
                arguments={"test_selector": "x; rm -rf /"},
            ),
            ErrorCode.MALFORMED_ACTION,
        ),
        (
            ToolCall(
                call_id="1", tool_name="read_file", arguments={"path": "../hidden/test_hidden.py"}
            ),
            ErrorCode.PATH_VIOLATION,
        ),
        (
            ToolCall(
                call_id="1",
                tool_name="apply_patch",
                arguments={"path": "tests/test_visible.py", "mode": "replace_file", "content": ""},
            ),
            ErrorCode.PROTECTED_PATH,
        ),
    ],
)
def test_router_rejects_and_records(registry, sandbox, call, code) -> None:  # type: ignore[no-untyped-def]
    spy = SpySandbox(sandbox)
    env, rec, router = make_router(registry.bundle("sqli-t0-s000"), spy)
    try:
        result = router.execute(call)
        assert not result.ok and result.error is not None and result.error.code == code
        assert spy.calls == []
        assert rec.steps[-1].execution_status == ExecutionStatus.REJECTED
        assert env.state.rejected[-1].code == code.value
    finally:
        env.close()


def test_unauthorized_tool_never_reaches_sandbox(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    spy = SpySandbox(sandbox)
    env, _, router = make_router(registry.bundle("sqli-t0-s000"), spy, allowed=("read_file",))
    try:
        r = router.execute(ToolCall(call_id="1", tool_name="run_visible_tests", arguments={}))
        assert r.error is not None and r.error.code == ErrorCode.UNAUTHORIZED_TOOL
        assert spy.calls == []
    finally:
        env.close()


def test_budget_and_terminal_state(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    env, _, router = make_router(registry.bundle("sqli-t0-s000"), sandbox, max_actions=2)
    try:
        assert router.execute(ToolCall(call_id="1", tool_name="list_files", arguments={})).ok
        assert router.execute(ToolCall(call_id="2", tool_name="list_files", arguments={})).ok
        r = router.execute(ToolCall(call_id="3", tool_name="list_files", arguments={}))
        assert r.error is not None and r.error.code == ErrorCode.BUDGET_EXCEEDED
        r = router.execute(ToolCall(call_id="4", tool_name="list_files", arguments={}))
        assert r.error is not None and r.error.code == ErrorCode.TERMINAL_STATE
    finally:
        env.close()


def test_listing_never_exposes_hidden_or_harness(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    env, _, router = make_router(registry.bundle("ssrf-t0-s000"), sandbox)
    try:
        listing = router.execute(
            ToolCall(call_id="1", tool_name="list_files", arguments={"path": "."})
        )
        paths = [e["path"] for e in listing.output["entries"]]  # type: ignore[index]
        assert not any("hidden" in p or "runner" in p or "oracle" in p for p in paths)
        assert "tests/test_visible.py" in paths
    finally:
        env.close()
