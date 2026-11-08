"""Ports: interfaces defined by the core and implemented by infrastructure adapters.

Core/application code depends only on these protocols, never on Docker, Redis, boto3, etc.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from traceforge.contracts.common import ArtifactRef
from traceforge.contracts.tasks import ResourceBudget

# --------------------------------------------------------------------------- storage


@runtime_checkable
class ObjectStore(Protocol):
    backend_name: str

    def put_bytes(self, key: str, data: bytes, media_type: str = ...) -> ArtifactRef: ...

    def get_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def uri_for(self, key: str) -> str: ...


# --------------------------------------------------------------------------- queue


@runtime_checkable
class JobQueue(Protocol):
    backend_name: str

    def enqueue(self, job_id: uuid.UUID) -> None: ...


# --------------------------------------------------------------------------- sandbox


class SandboxState(StrEnum):
    CREATING = "creating"
    READY = "ready"
    DESTROYED = "destroyed"
    DESTROY_FAILED = "destroy_failed"


@dataclass
class SandboxHandle:
    sandbox_id: str
    provider: str
    workspace: Path
    budget: ResourceBudget
    state: SandboxState = SandboxState.CREATING
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TestCaseResult:
    name: str
    status: str  # passed | failed | error
    message: str = ""


@dataclass(frozen=True)
class TestRunResult:
    run_id: str
    total: int
    passed: int
    failed: int
    errors: int
    cases: tuple[TestCaseResult, ...]
    duration_seconds: float
    timed_out: bool
    exit_code: int | None
    harness_ok: bool
    violations: tuple[str, ...] = ()
    output_tail: str = ""
    output_sha256: str = ""
    result_parsed: bool = True  # False => runner produced no framed result (infrastructure error)

    @property
    def all_passed(self) -> bool:
        return (
            self.harness_ok and not self.timed_out and self.total > 0 and self.passed == self.total
        )


class SandboxProvider(Protocol):
    """Host-controlled execution environment. Agents never receive a handle to this object."""

    name: str
    isolation: str  # "process" | "container"
    trusted_code_only: bool

    def create(
        self, run_id: str, files: Mapping[str, str], budget: ResourceBudget
    ) -> SandboxHandle: ...

    def run_tests(
        self, handle: SandboxHandle, test_module: str, selector: str, timeout: float
    ) -> TestRunResult:
        """Run a test module located inside the workspace (visible tests)."""
        ...

    def run_isolated_verifier(
        self,
        candidate_files: Mapping[str, str],
        hidden_files: Mapping[str, str],
        test_module: str,
        budget: ResourceBudget,
    ) -> TestRunResult:
        """Run hidden tests against a candidate snapshot in a fresh, separate environment."""
        ...

    def destroy(self, handle: SandboxHandle) -> None:
        """Idempotent teardown. Raises SandboxCleanupError if teardown fails."""
        ...

    def available(self) -> tuple[bool, str]: ...


# --------------------------------------------------------------------------- model providers


@dataclass(frozen=True)
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str
    tool_calls: tuple[dict[str, Any], ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class GenerationConfig:
    temperature: float = 0.0
    max_tokens: int = 1024
    seed: int | None = None
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class GeneratedToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any] | str


@dataclass(frozen=True)
class Generation:
    content: str
    tool_calls: tuple[GeneratedToolCall, ...]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


class ModelProvider(Protocol):
    kind: str
    model_id: str
    model_revision: str
    trusted: bool  # True only for deterministic, audited demo policies

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        config: GenerationConfig,
    ) -> Generation: ...

    async def aclose(self) -> None: ...
