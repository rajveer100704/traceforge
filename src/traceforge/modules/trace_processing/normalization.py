"""Parse raw records and normalise them to the canonical Trajectory schema.

Supported inputs:
* canonical TraceForge trajectory JSON (has ``schema_version``)
* OpenAI-style chat-completions tool traces (has ``messages``) via an isolated adapter

Normalisation is deterministic: identical raw bytes always produce an identical Trajectory
(ids are derived from the source location and content when absent). A claimed success that is
not backed by a verification id is recorded as ``claimed_outcome`` and the outcome stays UNKNOWN.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from traceforge.contracts.common import sha256_text
from traceforge.contracts.traces import Outcome, SourceType, Trajectory

NAMESPACE = uuid.UUID("6f1d2a1e-6c39-4f5e-9b0b-7a3f6b0e4c11")
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


class NormalizationError(ValueError):
    def __init__(self, reason: str, details: list[str]) -> None:
        super().__init__(f"{reason}: {'; '.join(details)[:500]}")
        self.reason = reason
        self.details = details


def deterministic_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "|".join(parts))


def _coerce_claim(data: dict[str, Any]) -> dict[str, Any]:
    outcome = str(data.get("outcome", "UNKNOWN")).upper()
    if outcome not in Outcome.__members__:
        data["outcome"] = Outcome.UNKNOWN.value
        data.setdefault("metadata", {})["unrecognised_outcome"] = outcome[:32]
        return data
    data["outcome"] = outcome
    if outcome != Outcome.UNKNOWN.value and not data.get("verification_id"):
        # Unverified outcome labels are claims, never ground truth.
        data["claimed_outcome"] = data.get("claimed_outcome") or outcome
        data["outcome"] = Outcome.UNKNOWN.value
    return data


def _parse_ts(value: Any, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def normalize_canonical(
    data: dict[str, Any], source_uri: str, line: int | None, raw_sha: str
) -> Trajectory:
    if str(data.get("schema_version")) not in SUPPORTED_SCHEMA_VERSIONS:
        raise NormalizationError("unsupported_schema_version", [str(data.get("schema_version"))])
    data = dict(data)
    data.setdefault("trajectory_id", str(deterministic_id(source_uri, str(line), raw_sha)))
    data.setdefault("source_id", f"{source_uri}#L{line}")
    data.pop("content_hash", None)  # always recomputed; never trust a supplied hash
    steps = data.get("steps") or []
    for i, step in enumerate(steps):
        if isinstance(step, dict):
            step.setdefault("step_id", str(deterministic_id(str(data["trajectory_id"]), str(i))))
    data = _coerce_claim(data)
    return Trajectory.model_validate(data)


def normalize_chat(
    data: dict[str, Any], source_uri: str, line: int | None, raw_sha: str
) -> Trajectory:
    """Adapter for OpenAI-style chat logs: messages with tool_calls and tool results."""
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise NormalizationError("chat_format_invalid", ["messages must be a list"])
    base = _parse_ts(data.get("created"), EPOCH)
    trajectory_id = str(deterministic_id(source_uri, str(line), raw_sha))
    steps: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        seq = i
        if not isinstance(msg, dict) or "role" not in msg:
            raise NormalizationError("chat_format_invalid", [f"message {i} missing role"])
        ts = (base + timedelta(seconds=i)).isoformat()
        role = msg["role"]
        if role == "tool":
            content = msg.get("content")
            try:
                observation = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                observation = {"text": content}
            steps.append(
                {
                    "step_id": str(deterministic_id(trajectory_id, str(seq))),
                    "sequence_number": seq,
                    "role": "tool",
                    "tool_name": msg.get("name") or "<unknown>",
                    "action_type": "tool_result",
                    "observation": observation,
                    "started_at": ts,
                    "completed_at": ts,
                    "metadata": {"call_id": msg.get("tool_call_id")},
                }
            )
        else:
            tool_calls = msg.get("tool_calls") or []
            steps.append(
                {
                    "step_id": str(deterministic_id(trajectory_id, str(seq))),
                    "sequence_number": seq,
                    "role": role,
                    "content": msg.get("content") or "",
                    "started_at": ts,
                    "completed_at": ts,
                    "action_type": "tool_call" if tool_calls else None,
                    "metadata": {"tool_calls": tool_calls} if tool_calls else {},
                }
            )
    success = data.get("success")
    outcome = "SUCCESS" if success is True else "FAILURE" if success is False else "UNKNOWN"
    record = {
        "trajectory_id": trajectory_id,
        "source_id": str(data.get("id") or f"{source_uri}#L{line}"),
        "source_type": SourceType.IMPORTED.value,
        "task_id": data.get("task_id"),
        "task_version": data.get("task_version"),
        "model_id": data.get("model"),
        "started_at": base.isoformat(),
        "completed_at": (base + timedelta(seconds=len(messages))).isoformat(),
        "steps": steps,
        "outcome": outcome,
        "metadata": {"import_format": "chat_completions", **(data.get("metadata") or {})},
    }
    return Trajectory.model_validate(_coerce_claim(record))


def normalize_record(payload: str, source_uri: str, line: int | None) -> Trajectory:
    raw_sha = sha256_text(payload)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise NormalizationError("json_parse_error", [f"{exc.msg} at col {exc.colno}"]) from None
    if not isinstance(data, dict):
        raise NormalizationError("json_not_object", [type(data).__name__])
    if "schema_version" in data:
        return normalize_canonical(data, source_uri, line, raw_sha)
    if "messages" in data:
        return normalize_chat(data, source_uri, line, raw_sha)
    raise NormalizationError("unknown_format", ["expected schema_version or messages"])
