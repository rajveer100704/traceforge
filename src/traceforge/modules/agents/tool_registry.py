"""Allow-listed tool catalogue: names, input schemas, descriptions."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from traceforge.contracts.actions import (
    ApplyPatchInput,
    InspectTestOutputInput,
    ListFilesInput,
    ReadFileInput,
    RunVisibleTestsInput,
    SubmitEvidenceInput,
    SubmitFinalReportInput,
)
from traceforge.domain.ports import ToolSpec


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: str  # TaskEnvironment method name
    max_output_chars: int = 70_000
    terminal: bool = False

    def spec(self) -> ToolSpec:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(name=self.name, description=self.description, parameters=schema)


TOOLS: dict[str, ToolDefinition] = {
    t.name: t
    for t in (
        ToolDefinition(
            "list_files",
            "List files under a workspace-relative path.",
            ListFilesInput,
            "list_files",
        ),
        ToolDefinition(
            "read_file", "Read a workspace file (UTF-8, size-limited).", ReadFileInput, "read_file"
        ),
        ToolDefinition(
            "apply_patch",
            "Modify an editable file. mode=replace_file with content, or mode=search_replace "
            "with old (must occur exactly once) and new.",
            ApplyPatchInput,
            "apply_patch",
        ),
        ToolDefinition(
            "run_visible_tests",
            "Run the visible test suite (optionally filtered by a name substring).",
            RunVisibleTestsInput,
            "run_visible_tests",
        ),
        ToolDefinition(
            "inspect_test_output",
            "Show per-test results for a previous visible test run.",
            InspectTestOutputInput,
            "inspect_test_output",
        ),
        ToolDefinition(
            "submit_evidence",
            "Attach evidence (e.g. a test run_id or patch_id) supporting your fix.",
            SubmitEvidenceInput,
            "submit_evidence",
        ),
        ToolDefinition(
            "submit_final_report",
            "Finish the episode with a summary. Outcome is decided by independent verification.",
            SubmitFinalReportInput,
            "submit_final_report",
            terminal=True,
        ),
    )
}


class ToolRegistry:
    def __init__(self, allowed: tuple[str, ...]) -> None:
        unknown = [name for name in allowed if name not in TOOLS]
        if unknown:
            raise ValueError(f"task references unknown tools: {unknown}")
        self.allowed = allowed

    def get(self, name: str) -> ToolDefinition | None:
        return TOOLS.get(name)

    def is_allowed(self, name: str) -> bool:
        return name in self.allowed

    def specs(self) -> list[ToolSpec]:
        return [TOOLS[name].spec() for name in self.allowed]
