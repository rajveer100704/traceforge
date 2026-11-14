"""Agent episode runner.

Runs one episode against a fresh environment using only the PUBLIC task spec and workspace
files. It records every message/action/observation and returns host-recorded facts. It does
NOT decide success: verification and reward are computed afterwards by independent engines.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from traceforge.contracts.actions import ToolCall
from traceforge.contracts.common import CapabilityUnavailable, JSONValue, utc_now
from traceforge.contracts.observations import EpisodeRecord
from traceforge.contracts.tasks import TaskSpec
from traceforge.contracts.traces import Outcome, StepRole, TokenUsage, Trajectory
from traceforge.domain.ports import ChatMessage, GenerationConfig, ModelProvider, SandboxProvider
from traceforge.infrastructure.sandbox.common import SandboxCleanupError
from traceforge.modules.agents.budget import BudgetManager
from traceforge.modules.agents.prompts import PROMPT_TEMPLATE_VERSION, SYSTEM_PROMPT, task_prompt
from traceforge.modules.agents.recorder import TrajectoryRecorder
from traceforge.modules.agents.tool_registry import ToolRegistry
from traceforge.modules.agents.tool_router import ToolRouter
from traceforge.modules.environments.task_env import EnvironmentPolicy, TaskEnvironment

log = logging.getLogger(__name__)


def _failure_kind(exc: BaseException, details: dict[str, Any]) -> str:
    """Classify a failed generation call. Provider failures are not verifier outcomes."""
    kind = details.get("failure")
    if isinstance(kind, str) and kind:
        return kind
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return "timeout"
    return "other"


def _int_or_none(value: Any) -> int | None:
    """Exception details are untrusted; only integers are recorded in step metadata."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class EpisodeResult:
    trajectory: Trajectory
    episode: EpisodeRecord
    cleanup_error: str | None = None
    metadata: dict[str, JSONValue] = field(default_factory=dict)


class AgentRunner:
    def __init__(
        self, sandbox: SandboxProvider, *, allow_untrusted_local_execution: bool = False
    ) -> None:
        self.sandbox = sandbox
        self.allow_untrusted = allow_untrusted_local_execution

    def check_trust(self, provider: ModelProvider) -> None:
        if self.sandbox.trusted_code_only and not provider.trusted and not self.allow_untrusted:
            raise CapabilityUnavailable(
                f"sandbox '{self.sandbox.name}' executes code without isolation and only accepts "
                f"trusted demo policies; use the docker sandbox for provider '{provider.kind}' or "
                "explicitly set TRACEFORGE_ALLOW_UNTRUSTED_LOCAL_EXECUTION=true (single-user research)"
            )

    async def run_episode(
        self,
        spec: TaskSpec,
        workspace_files: dict[str, str],
        provider: ModelProvider,
        *,
        run_id: str,
        correlation_id: str,
        generation: GenerationConfig | None = None,
        env_policy: EnvironmentPolicy | None = None,
        cancel: CancelToken | None = None,
    ) -> EpisodeResult:
        self.check_trust(provider)
        if spec.budget.max_cost_usd > 0 and not getattr(provider, "pricing_known", False):
            # A cost budget that cannot be measured must not be silently unenforced.
            raise CapabilityUnavailable(
                f"task budget max_cost_usd={spec.budget.max_cost_usd} cannot be enforced: provider "
                f"'{provider.kind}' has no known pricing (set provider.price_per_1k_prompt and "
                "price_per_1k_completion; 0.0 means free)"
            )
        generation = generation or GenerationConfig()
        metadata: dict[str, JSONValue] = {
            "provider_kind": provider.kind,
            "model_id": provider.model_id,
            "model_revision": provider.model_revision,
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
            "sandbox_provider": self.sandbox.name,
            "sandbox_isolation": self.sandbox.isolation,
            "correlation_id": correlation_id,
            "demo_provider": provider.kind == "demo",
            "temperature": generation.temperature,
            "seed": generation.seed,
        }
        env = TaskEnvironment(spec, workspace_files, self.sandbox, run_id, env_policy)
        recorder = TrajectoryRecorder(
            source_id=f"rollout:{run_id}",
            task_id=spec.task_id,
            task_version=spec.version,
            model_id=provider.model_id,
            metadata=metadata,
        )
        budget = BudgetManager(spec.budget)
        cleanup_error: str | None = None
        try:
            initial = env.reset()
            metadata["sandbox_id"] = env.handle.sandbox_id if env.handle else None
            registry = ToolRegistry(spec.allowed_tools)
            router = ToolRouter(env, registry, budget, recorder, correlation_id)
            messages = [
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                ChatMessage(role="user", content=task_prompt(spec)),
            ]
            recorder.message(StepRole.SYSTEM, SYSTEM_PROMPT)
            recorder.message(
                StepRole.USER, messages[1].content, metadata={"initial_observation": initial}
            )
            await self._loop(
                env, router, registry, budget, recorder, provider, messages, generation, cancel
            )
            episode = env.episode_record(metadata=metadata)
        finally:
            try:
                env.close()
            except SandboxCleanupError as exc:
                cleanup_error = str(exc)
                log.error("sandbox cleanup failed (will be reaped): %s", exc)
            await provider.aclose()
        claimed = None
        if episode.final_report is not None:
            claimed = Outcome.SUCCESS if episode.final_report.claimed_success else Outcome.FAILURE
        recorder.metadata.update({"termination_reason": episode.termination_reason})
        trajectory = recorder.build(claimed=claimed)
        return EpisodeResult(
            trajectory=trajectory, episode=episode, cleanup_error=cleanup_error, metadata=metadata
        )

    async def _loop(
        self,
        env: TaskEnvironment,
        router: ToolRouter,
        registry: ToolRegistry,
        budget: BudgetManager,
        recorder: TrajectoryRecorder,
        provider: ModelProvider,
        messages: list[ChatMessage],
        generation: GenerationConfig,
        cancel: CancelToken | None,
    ) -> None:
        tools = registry.specs()
        max_turns = env.spec.budget.max_actions * 2
        for _ in range(max_turns):
            if cancel is not None and cancel.cancelled:
                env.terminate("cancelled")
                return
            exhausted = budget.exhausted()
            if exhausted:
                env.terminate(f"budget:{exhausted}")
                return
            started = utc_now()
            t0 = time.perf_counter()
            timeout = max(0.1, min(generation.timeout_seconds, env.remaining_seconds()))
            try:
                gen = await asyncio.wait_for(
                    provider.generate(messages, tools, generation), timeout
                )
            except TimeoutError:
                # The runner's own deadline (per-call timeout or remaining wall clock) expired
                # while the provider was still working; its attempt count is unknown.
                env.terminate("provider_timeout")
                recorder.message(
                    StepRole.ASSISTANT,
                    "",
                    started_at=started,
                    metadata={
                        "error": "provider_timeout",
                        "provider_failure": "timeout",
                        "failure_source": "runner_deadline",
                        "attempts": None,
                        "http_status": None,
                        "request_seconds": round(time.perf_counter() - t0, 6),
                        "usage_reported": False,
                        "cost_usd": None,
                        "finish_reason": None,
                    },
                )
                return
            except Exception as exc:
                env.terminate("provider_error")
                details = getattr(exc, "details", None) or {}
                recorder.message(
                    StepRole.ASSISTANT,
                    "",
                    started_at=started,
                    metadata={
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                        "provider_failure": _failure_kind(exc, details),
                        "failure_source": "provider",
                        "attempts": _int_or_none(details.get("attempts")),
                        "http_status": _int_or_none(details.get("status")),
                        "request_seconds": round(time.perf_counter() - t0, 6),
                        "usage_reported": False,
                        # A failed call may still have been billed; its cost is unknown.
                        "cost_usd": None,
                        "finish_reason": None,
                    },
                )
                return
            budget.charge_generation(gen.prompt_tokens + gen.completion_tokens, gen.cost_usd)
            encoded_calls = tuple(
                {
                    "id": c.call_id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": c.arguments
                        if isinstance(c.arguments, str)
                        else json.dumps(c.arguments),
                    },
                }
                for c in gen.tool_calls
            )
            recorder.message(
                StepRole.ASSISTANT,
                gen.content,
                started_at=started,
                # Token usage is attached only when the provider reported it: absent != zero.
                token_usage=TokenUsage(
                    prompt_tokens=gen.prompt_tokens, completion_tokens=gen.completion_tokens
                )
                if gen.usage_reported
                else None,
                metadata={
                    "tool_calls": list(encoded_calls),
                    "finish_reason": gen.finish_reason,
                    "provider_failure": None,
                    "attempts": gen.attempts,
                    "request_seconds": gen.request_seconds
                    if gen.request_seconds is not None
                    else round(time.perf_counter() - t0, 6),
                    "usage_reported": gen.usage_reported,
                    "cost_usd": gen.cost_usd,
                },
            )
            messages.append(
                ChatMessage(role="assistant", content=gen.content, tool_calls=encoded_calls)
            )
            if not gen.tool_calls:
                env.terminate("no_tool_calls")
                return
            for call in gen.tool_calls:
                result = router.execute(
                    ToolCall(
                        call_id=call.call_id[:128],
                        tool_name=call.name[:64],
                        arguments=call.arguments,
                    )
                )
                payload = {
                    "ok": result.ok,
                    "output": result.output,
                    "error": result.error.model_dump(mode="json") if result.error else None,
                }
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(payload, default=str),
                        tool_call_id=call.call_id,
                        name=call.name,
                    )
                )
                if env.state.terminal:
                    return
        env.terminate("max_turns")
