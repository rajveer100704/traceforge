"""Agent tool-call contracts: allow-listed tool input schemas and tool results."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from traceforge.contracts.common import FrozenModel, JSONValue, StructuredError

MAX_PATH_LEN = 256
MAX_PATCH_BYTES = 64_000


class ListFilesInput(FrozenModel):
    path: str = Field(default=".", max_length=MAX_PATH_LEN)


class ReadFileInput(FrozenModel):
    path: str = Field(max_length=MAX_PATH_LEN)


class ApplyPatchInput(FrozenModel):
    """Either full replacement (`content`) or exact search/replace (`old` -> `new`)."""

    path: str = Field(max_length=MAX_PATH_LEN)
    mode: Literal["replace_file", "search_replace"]
    content: str | None = Field(default=None, max_length=MAX_PATCH_BYTES)
    old: str | None = Field(default=None, max_length=MAX_PATCH_BYTES)
    new: str | None = Field(default=None, max_length=MAX_PATCH_BYTES)

    @model_validator(mode="after")
    def _check(self) -> ApplyPatchInput:
        if self.mode == "replace_file" and self.content is None:
            raise ValueError("replace_file requires content")
        if self.mode == "search_replace" and (self.old is None or self.new is None):
            raise ValueError("search_replace requires old and new")
        if self.mode == "search_replace" and self.old == "":
            raise ValueError("old must be non-empty")
        return self


class RunVisibleTestsInput(FrozenModel):
    test_selector: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_]*$")


class InspectTestOutputInput(FrozenModel):
    run_id: str = Field(max_length=64)


class EvidenceType(StrEnum):
    TEST_RUN = "test_run"
    PATCH = "patch"
    FILE_READ = "file_read"


class SubmitEvidenceInput(FrozenModel):
    evidence_type: EvidenceType
    artifact_id: str = Field(max_length=64)
    explanation: str = Field(max_length=2000)


class SubmitFinalReportInput(FrozenModel):
    summary: str = Field(max_length=4000)
    claimed_success: bool = False


class ToolCall(FrozenModel):
    """A model-proposed tool invocation (untrusted)."""

    call_id: str = Field(max_length=128)
    tool_name: str = Field(max_length=64)
    arguments: dict[str, JSONValue] | str  # str => raw (possibly malformed) JSON from a model


class ToolResult(FrozenModel):
    action_id: str
    call_id: str
    tool_name: str
    ok: bool
    output: JSONValue | None = None
    error: StructuredError | None = None
    terminal: bool = False
