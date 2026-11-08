"""Deterministic Trajectory -> chat-format SFT example conversion.

Only assistant turns are training targets. System/user/tool turns are context and must be
masked out of the loss (see ``assistant_mask``). Outcome-conditioned failures (curation D4)
carry an explicit verified-outcome tag in the system turn so they are never presented as
successful demonstrations.
"""

from __future__ import annotations

import json
from typing import Any

from traceforge.contracts.traces import Outcome, StepRole, Trajectory

FORMAT_VERSION = "sft-chat-v1"


def trajectory_to_messages(t: Trajectory) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for s in t.steps:
        if s.role == StepRole.TOOL:
            payload = {
                "ok": s.execution_status.value == "ok",
                "output": s.observation,
                "error": s.error.model_dump(mode="json") if s.error else None,
            }
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": s.metadata.get("call_id"),
                    "name": s.tool_name,
                    "content": json.dumps(payload, sort_keys=True, default=str)[:8000],
                }
            )
        elif s.role == StepRole.ASSISTANT:
            msg: dict[str, Any] = {"role": "assistant", "content": s.content or ""}
            calls = s.metadata.get("tool_calls")
            if calls:
                msg["tool_calls"] = calls
            messages.append(msg)
        else:
            messages.append({"role": s.role.value, "content": s.content or ""})
    if t.outcome != Outcome.SUCCESS and messages and messages[0]["role"] == "system":
        messages[0] = {
            **messages[0],
            "content": f"[verified outcome: {t.outcome.value}]\n" + messages[0]["content"],
        }
    return messages


def assistant_mask(messages: list[dict[str, Any]]) -> list[bool]:
    """True where the message is a loss target."""
    return [m["role"] == "assistant" for m in messages]


def render_plain(messages: list[dict[str, Any]]) -> tuple[str, list[tuple[int, int]]]:
    """Template-free rendering used by the CPU test path. Returns text + target char spans."""
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for m in messages:
        body = m.get("content") or ""
        if m.get("tool_calls"):
            body += json.dumps(m["tool_calls"], sort_keys=True)
        header = f"<|{m['role']}|>\n"
        chunk = header + body + "\n<|end|>\n"
        if m["role"] == "assistant":
            start = cursor + len(header)
            spans.append((start, start + len(body) + len("\n<|end|>\n")))
        parts.append(chunk)
        cursor += len(chunk)
    return "".join(parts), spans
