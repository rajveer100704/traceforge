"""Tool router: the ONLY path from model output to environment actions.

Order of checks (fail closed): terminal state -> budget -> unknown tool -> task allow-list ->
JSON parse -> schema validation -> handler (path policy, size limits) -> output bound.
Every call, accepted or rejected, is recorded in the trajectory and in the episode state.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import ValidationError

from traceforge.contracts.actions import ToolCall, ToolResult
from traceforge.contracts.common import ErrorCode, JSONValue, StructuredError, utc_now
from traceforge.contracts.traces import ExecutionStatus
from traceforge.modules.agents.budget import BudgetManager
from traceforge.modules.agents.recorder import TrajectoryRecorder
from traceforge.modules.agents.tool_registry import ToolRegistry
from traceforge.modules.environments.task_env import TaskEnvironment
from traceforge.modules.environments.workspace import ToolError

MAX_RAW_ARGS = 200_000
SAFETY_CODES = frozenset(
    {
        ErrorCode.PATH_VIOLATION,
        ErrorCode.PROTECTED_PATH,
        ErrorCode.UNKNOWN_TOOL,
        ErrorCode.UNAUTHORIZED_TOOL,
    }
)


class ToolRouter:
    def __init__(
        self,
        env: TaskEnvironment,
        registry: ToolRegistry,
        budget: BudgetManager,
        recorder: TrajectoryRecorder,
        correlation_id: str,
    ) -> None:
        self.env = env
        self.registry = registry
        self.budget = budget
        self.recorder = recorder
        self.correlation_id = correlation_id

    def execute(self, call: ToolCall) -> ToolResult:
        started = utc_now()
        action_id = f"act-{uuid.uuid4().hex[:12]}"
        seq = self.env.next_sequence()
        raw_input: JSONValue = call.arguments
        try:
            result = self._execute(call, action_id, seq)
            status = ExecutionStatus.OK if result.ok else ExecutionStatus.ERROR
        except ToolError as exc:
            status = ExecutionStatus.REJECTED
            err = StructuredError(code=exc.code, message=exc.message, details=_safe(exc.details))
            self.env.record_rejection(seq, call.tool_name, exc.code, exc.message)
            result = ToolResult(
                action_id=action_id,
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=False,
                error=err,
            )
        except Exception as exc:  # sandbox/internal errors are surfaced, never swallowed
            status = ExecutionStatus.ERROR
            err = StructuredError(
                code=ErrorCode.SANDBOX_ERROR, message=f"{type(exc).__name__}: {exc}"[:500]
            )
            result = ToolResult(
                action_id=action_id,
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=False,
                error=err,
            )
        if isinstance(raw_input, str) and len(raw_input) > 2000:
            raw_input = raw_input[:2000] + "...<truncated>"
        self.recorder.tool(
            tool_name=call.tool_name,
            action_id=action_id,
            action_input=raw_input,
            observation=result.output if result.ok else None,
            status=status,
            started_at=started,
            error=result.error,
            metadata={
                "call_id": call.call_id,
                "correlation_id": self.correlation_id,
                "env_seq": seq,
            },
        )
        return result

    def _execute(self, call: ToolCall, action_id: str, seq: int) -> ToolResult:
        if self.env.state.terminal:
            raise ToolError(ErrorCode.TERMINAL_STATE, "episode already terminated")
        exhausted = self.budget.exhausted()
        if exhausted:
            self.env.terminate(f"budget:{exhausted}")
            raise ToolError(ErrorCode.BUDGET_EXCEEDED, f"budget exhausted: {exhausted}")
        self.budget.charge_action()
        self.env.state.tool_calls += 1
        definition = self.registry.get(call.tool_name)
        if definition is None:
            raise ToolError(ErrorCode.UNKNOWN_TOOL, f"unknown tool '{call.tool_name[:64]}'")
        if not self.registry.is_allowed(call.tool_name):
            raise ToolError(
                ErrorCode.UNAUTHORIZED_TOOL, f"tool '{call.tool_name}' not allowed for task"
            )
        arguments: Any = call.arguments
        if isinstance(arguments, str):
            if len(arguments) > MAX_RAW_ARGS:
                raise ToolError(ErrorCode.SIZE_LIMIT, "tool arguments too large")
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as exc:
                raise ToolError(
                    ErrorCode.MALFORMED_ACTION, f"arguments are not valid JSON: {exc.msg}"
                ) from None
        if not isinstance(arguments, dict):
            raise ToolError(ErrorCode.MALFORMED_ACTION, "arguments must be a JSON object")
        try:
            parsed = definition.input_model.model_validate(arguments)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:5]
            raise ToolError(
                ErrorCode.MALFORMED_ACTION, "invalid arguments: " + "; ".join(errors)
            ) from None
        handler = getattr(self.env, definition.handler)
        output = handler(parsed, seq)
        encoded = json.dumps(output, default=str)
        if len(encoded) > definition.max_output_chars:
            output = {"truncated": True, "preview": encoded[: definition.max_output_chars]}
        return ToolResult(
            action_id=action_id,
            call_id=call.call_id,
            tool_name=call.tool_name,
            ok=True,
            output=output,
            terminal=definition.terminal,
        )


def _safe(details: dict[str, Any]) -> dict[str, JSONValue]:
    return {
        k: v if isinstance(v, (int, float, str, bool)) or v is None else str(v)
        for k, v in details.items()
    }
