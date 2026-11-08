"""Task-family framework: deterministic, seeded template rendering."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from string import Template
from typing import Protocol

from traceforge.contracts.tasks import Difficulty

GENERATOR_VERSION = "1.0.0"


@dataclass(frozen=True)
class RenderedTask:
    family_id: str
    template_id: str
    seed: int
    difficulty: Difficulty
    module_name: str
    target_function: str
    workspace_files: dict[str, str]
    hidden_tests: str
    allowed_imports: tuple[str, ...]
    params: dict[str, object] = field(default_factory=dict)


class TaskFamily(Protocol):
    family_id: str
    short: str
    category: str
    title: str
    templates: tuple[str, ...]
    allowed_imports: tuple[str, ...]

    def render(self, template_id: str, seed: int) -> RenderedTask: ...


def rng_for(family_id: str, template_id: str, seed: int) -> random.Random:
    # str seeding is deterministic across processes (sha512-based in CPython).
    return random.Random(f"traceforge:{GENERATOR_VERSION}:{family_id}:{template_id}:{seed}")  # noqa: S311


def render(template: str, **values: object) -> str:
    return Template(template).substitute({k: str(v) for k, v in values.items()})


def task_markdown(title: str, objective: str, module_path: str, notes: list[str]) -> str:
    bullet_notes = "\n".join(f"- {n}" for n in notes)
    return (
        f"# {title}\n\n{objective}\n\n"
        f"## Files\n\n- `{module_path}` — the code you must fix (editable)\n"
        f"- `tests/test_visible.py` — visible behaviour tests (read-only)\n\n"
        f"## Notes\n\n{bullet_notes}\n\n"
        "Additional hidden tests will independently verify the security property and that\n"
        "legitimate behaviour is preserved. Editing tests or harness files is not permitted.\n"
    )


DISTRACTORS: tuple[str, ...] = (
    '''def normalize_label(value: str) -> str:
    """Collapse whitespace and lowercase a label (unrelated helper)."""
    return " ".join(value.split()).lower()
''',
    '''def chunked(items: list, size: int) -> list:
    """Split a list into fixed-size chunks (unrelated helper)."""
    return [items[i : i + size] for i in range(0, len(items), size)]
''',
    '''def safe_int(value: str, default: int = 0) -> int:
    """Parse an integer, falling back to a default (unrelated helper)."""
    try:
        return int(value)
    except ValueError:
        return default
''',
    '''def truncate(text: str, limit: int = 80) -> str:
    """Truncate text for display (unrelated helper)."""
    return text if len(text) <= limit else text[: limit - 3] + "..."
''',
)


def pick_distractors(rng: random.Random, count: int = 2) -> str:
    return "\n\n".join(rng.sample(list(DISTRACTORS), count))
