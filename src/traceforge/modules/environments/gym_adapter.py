"""Gymnasium adapter over TaskEnvironment + ToolRouter.

Action: a JSON string ``{"tool": <name>, "arguments": {...}}`` (validated by the ToolRouter).
Observation: a JSON string (tool result, or the task prompt after reset).
Reward: 0 for intermediate steps; on termination, the verification-derived reward (R3 by default).
Each ``reset`` creates a fresh sandbox; ``close`` destroys it.
"""

from __future__ import annotations

import json
import string
import uuid
from typing import Any

import gymnasium as gym
from gymnasium import spaces

from traceforge.contracts.actions import ToolCall
from traceforge.contracts.rewards import RewardPolicy
from traceforge.contracts.tasks import TaskBundle
from traceforge.domain.ports import SandboxProvider
from traceforge.modules.agents.budget import BudgetManager
from traceforge.modules.agents.prompts import task_prompt
from traceforge.modules.agents.recorder import TrajectoryRecorder
from traceforge.modules.agents.tool_registry import ToolRegistry
from traceforge.modules.agents.tool_router import ToolRouter
from traceforge.modules.environments.task_env import TaskEnvironment
from traceforge.modules.rewards.engine import RewardEngine
from traceforge.modules.tasks.registry import TaskRegistry
from traceforge.modules.verification.engine import VerificationEngine

MAX_TEXT = 200_000
CHARSET = string.printable


def _ascii(text: str) -> str:
    return text.encode("ascii", "backslashreplace").decode("ascii")[:MAX_TEXT]


class TraceForgeGymEnv(gym.Env[str, str]):
    metadata = {"render_modes": []}

    def __init__(
        self,
        registry: TaskRegistry,
        sandbox: SandboxProvider,
        verifier: VerificationEngine,
        policy: RewardPolicy,
        task_ids: list[str] | None = None,
    ) -> None:
        self.registry, self.sandbox, self.verifier, self.policy = (
            registry,
            sandbox,
            verifier,
            policy,
        )
        self.task_ids = task_ids or [b.spec.task_id for b in registry.list()]
        self.action_space = spaces.Text(max_length=MAX_TEXT, charset=CHARSET)
        self.observation_space = spaces.Text(max_length=MAX_TEXT, charset=CHARSET)
        self._env: TaskEnvironment | None = None
        self._router: ToolRouter | None = None
        self._bundle: TaskBundle | None = None

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[str, dict[str, Any]]:
        super().reset(seed=seed)
        self._teardown()
        options = options or {}
        task_id = (
            options.get("task_id")
            or self.task_ids[int(self.np_random.integers(len(self.task_ids)))]
        )
        bundle = self.registry.bundle(str(task_id))
        run_id = str(uuid.uuid4())
        env = TaskEnvironment(bundle.spec, dict(bundle.workspace_files), self.sandbox, run_id)
        initial = env.reset()
        recorder = TrajectoryRecorder(
            source_id=f"gym:{run_id}",
            task_id=bundle.spec.task_id,
            task_version=bundle.spec.version,
            model_id="gym-agent",
        )
        self._router = ToolRouter(
            env,
            ToolRegistry(bundle.spec.allowed_tools),
            BudgetManager(bundle.spec.budget),
            recorder,
            correlation_id=f"gym:{run_id}",
        )
        self._env, self._bundle = env, bundle
        return _ascii(task_prompt(bundle.spec)), {
            "task_id": bundle.spec.task_id,
            "initial": initial,
        }

    def step(self, action: str) -> tuple[str, float, bool, bool, dict[str, Any]]:
        if self._env is None or self._router is None or self._bundle is None:
            raise RuntimeError("call reset() before step()")
        try:
            parsed = json.loads(action)
            call = ToolCall(
                call_id=f"gym-{uuid.uuid4().hex[:8]}",
                tool_name=str(parsed.get("tool", "")),
                arguments=parsed.get("arguments", {}),
            )
        except (json.JSONDecodeError, AttributeError, ValueError):
            call = ToolCall(
                call_id="gym-malformed", tool_name="<malformed>", arguments=str(action)[:2000]
            )
        result = self._router.execute(call)
        obs = json.dumps(
            {
                "ok": result.ok,
                "output": result.output,
                "error": result.error.model_dump(mode="json") if result.error else None,
            },
            default=str,
        )
        obs = _ascii(obs)
        state = self._env.state
        terminated = state.terminal and state.termination_reason == "final_report"
        truncated = state.terminal and not terminated
        info: dict[str, Any] = {"termination_reason": state.termination_reason}
        reward = 0.0
        if terminated or truncated:
            episode = self._env.episode_record(metadata={"adapter": "gymnasium"})
            verification = self.verifier.verify(self._bundle, episode)
            breakdown = RewardEngine().compute(verification, self.policy)
            reward = breakdown.final_reward
            info.update(
                verdict=verification.verdict.value,
                verification_id=str(verification.verification_id),
                reward_breakdown=breakdown.model_dump(mode="json"),
            )
            self._teardown()
        return obs, reward, terminated, truncated, info

    def _teardown(self) -> None:
        if self._env is not None:
            self._env.close()
        self._env, self._router, self._bundle = None, None, None

    def close(self) -> None:
        self._teardown()
