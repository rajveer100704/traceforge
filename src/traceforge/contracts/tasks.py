"""Task contracts. Public (agent-facing) and private (oracle) data are separate types."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator

from traceforge.contracts.common import FrozenModel, JSONValue


class Split(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    PUBLIC_TEST = "public_test"
    PRIVATE_TEST = "private_test"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ResourceBudget(FrozenModel):
    max_actions: int = Field(default=20, ge=1, le=500)
    max_wall_seconds: float = Field(default=120.0, gt=0, le=3600)
    max_tokens: int = Field(default=32_000, ge=1)
    max_cost_usd: float = Field(default=0.0, ge=0)
    test_timeout_seconds: float = Field(default=20.0, gt=0, le=600)
    cpu_limit: float = Field(default=1.0, gt=0, le=16)
    memory_mb: int = Field(default=256, ge=32, le=16_384)
    pids_limit: int = Field(default=64, ge=8, le=4096)


class VisibleFile(FrozenModel):
    path: str
    sha256: str
    editable: bool


class TaskSpec(FrozenModel):
    """Everything an agent may see about a task. Never contains oracle material."""

    task_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.\-]{2,127}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    domain: str = "cybersecurity"
    category: str
    title: str
    objective: str = Field(max_length=8000)
    allowed_tools: tuple[str, ...]
    environment_ref: str = Field(description="Image digest or local fixture reference")
    budget: ResourceBudget = ResourceBudget()
    visible_files: tuple[VisibleFile, ...]
    difficulty: Difficulty = Difficulty.MEDIUM
    public_metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("allowed_tools")
    @classmethod
    def _non_empty(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("allowed_tools must be non-empty")
        return v

    @property
    def key(self) -> str:
        return f"{self.task_id}@{self.version}"


class TaskOracleSpec(FrozenModel):
    """Evaluator-only data. Must never be serialised into prompts/observations/workspaces."""

    task_id: str
    version: str
    verifier_id: str
    verifier_version: str
    family_id: str
    template_id: str
    generator_version: str
    seed: int
    split: Split
    hidden_test_module: str
    security_test_prefix: str = "test_sec_"
    functional_test_prefix: str = "test_func_"
    editable_globs: tuple[str, ...] = ("app/*.py",)
    protected_globs: tuple[str, ...] = ("tests/*", "TASK.md")
    allowed_imports: tuple[str, ...] = ()
    fixture_hash: str
    success_predicates: tuple[str, ...] = ("hidden_security_tests_pass", "hidden_functional_pass")


class TaskBundle(FrozenModel):
    """In-memory pairing used by the evaluator side only."""

    spec: TaskSpec
    oracle: TaskOracleSpec
    workspace_files: dict[str, str]
    hidden_files: dict[str, str]
