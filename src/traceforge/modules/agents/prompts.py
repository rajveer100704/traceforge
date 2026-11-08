"""Prompt construction from the PUBLIC task spec only."""

from __future__ import annotations

from traceforge.contracts.tasks import TaskSpec

PROMPT_TEMPLATE_VERSION = "agent-v1"

SYSTEM_PROMPT = """You are a careful security engineer working inside a restricted workspace.
You can only act through the provided tools. There is no shell and no network.
Inspect the code, apply a minimal correct fix to the editable file, run the visible tests,
attach evidence (test run ids) and finish with submit_final_report.
Your final outcome is decided by independent hidden verification, not by your report.
Do not modify tests or files outside the editable set."""


def task_prompt(spec: TaskSpec) -> str:
    editable = [f.path for f in spec.visible_files if f.editable]
    files = [f.path for f in spec.visible_files]
    return (
        f"Task {spec.task_id} (v{spec.version}): {spec.title}\n\n{spec.objective}\n\n"
        f"Files: {', '.join(files)}\nEditable: {', '.join(editable)}\n"
        f"Budget: at most {spec.budget.max_actions} tool calls."
    )
