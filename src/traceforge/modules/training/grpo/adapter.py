"""TRL GRPO adapter (optional ``ml`` extra; isolated from core imports).

Implements the TRL ``environment_factory`` protocol verified against the installed TRL:
an environment class with ``reset(**row) -> str | None``, public typed methods exposed as tools,
and ``get_reward() -> float`` which makes the environment own the reward.

Guarantees:
* every tool call goes through the TraceForge ToolRouter (allow-list, schema, path policy, budget);
* each ``reset`` creates a FRESH TaskEnvironment/sandbox (TRL pools and reuses instances);
* reward comes only from independent verification of host-recorded episode facts;
* sandboxes are destroyed after scoring, on re-reset and on errors;
* per-rollout provenance (task/verifier/reward-policy versions, verdict) is recorded.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from traceforge.contracts.actions import ToolCall
from traceforge.contracts.common import CapabilityUnavailable
from traceforge.contracts.verification import VerifierProfile
from traceforge.domain.ports import SandboxProvider
from traceforge.modules.agents.budget import BudgetManager
from traceforge.modules.agents.prompts import task_prompt
from traceforge.modules.agents.recorder import TrajectoryRecorder
from traceforge.modules.agents.tool_registry import ToolRegistry
from traceforge.modules.agents.tool_router import ToolRouter
from traceforge.modules.environments.task_env import TaskEnvironment
from traceforge.modules.rewards.engine import RewardEngine
from traceforge.modules.rewards.policies import PolicyRegistry
from traceforge.modules.tasks.registry import TaskRegistry
from traceforge.modules.training.capabilities import grpo_environment_support
from traceforge.modules.verification.engine import VerificationEngine

log = logging.getLogger(__name__)
ADAPTER_VERSION = "1.0.0"


def check_grpo_compatibility() -> str:
    ok, why = grpo_environment_support()
    if not ok:
        raise CapabilityUnavailable(f"GRPO environment integration unavailable: {why}")
    return why


@dataclass
class RolloutRecord:
    rollout_id: str
    task_id: str
    task_version: str
    verifier_version: str
    reward_policy: str
    verdict: str
    reward: float
    tool_calls: int
    termination_reason: str


@dataclass
class EnvironmentDeps:
    registry: TaskRegistry
    sandbox: SandboxProvider
    verifier: VerificationEngine
    policies: PolicyRegistry
    reward_policy: str = "verified-v1"
    allow_untrusted_local_execution: bool = False
    model_id: str = "grpo-policy"
    on_record: Callable[[RolloutRecord], None] | None = None
    records: list[RolloutRecord] = field(default_factory=list)


class TraceForgeToolEnv:
    """One instance per concurrent rollout; ``reset`` starts a fresh episode."""

    def __init__(self, deps: EnvironmentDeps) -> None:
        if deps.sandbox.trusted_code_only and not deps.allow_untrusted_local_execution:
            raise CapabilityUnavailable(
                "GRPO executes model-generated code; use the docker sandbox or explicitly allow untrusted "
                "local execution for single-user research"
            )
        self._deps = deps
        self._env: TaskEnvironment | None = None
        self._router: ToolRouter | None = None
        self._bundle: Any = None
        self._scored = False

    # ------------------------------------------------------------------ lifecycle (not tools)
    def reset(self, **row: Any) -> str | None:
        self._close()
        task_id = str(row["task_id"])
        bundle = self._deps.registry.bundle(task_id, row.get("task_version"))
        run_id = str(uuid.uuid4())
        env = TaskEnvironment(bundle.spec, dict(bundle.workspace_files), self._deps.sandbox, run_id)
        env.reset()
        recorder = TrajectoryRecorder(
            source_id=f"grpo:{run_id}",
            task_id=bundle.spec.task_id,
            task_version=bundle.spec.version,
            model_id=self._deps.model_id,
            metadata={"adapter_version": ADAPTER_VERSION},
        )
        self._router = ToolRouter(
            env,
            ToolRegistry(bundle.spec.allowed_tools),
            BudgetManager(bundle.spec.budget),
            recorder,
            correlation_id=f"grpo:{run_id}",
        )
        self._env, self._bundle, self._scored = env, bundle, False
        return "\n\n" + task_prompt(bundle.spec)

    def get_reward(self) -> float:
        """Score the finished rollout via independent verification (environment-owned reward)."""
        if self._env is None or self._bundle is None:
            return 0.0
        try:
            episode = self._env.episode_record(metadata={"adapter": "trl-grpo"})
            verification = self._deps.verifier.verify(
                self._bundle, episode, profile=VerifierProfile.FULL
            )
            policy = self._deps.policies.get(self._deps.reward_policy)
            reward = RewardEngine().compute(verification, policy)
            record = RolloutRecord(
                rollout_id=episode.run_id,
                task_id=episode.task_id,
                task_version=episode.task_version,
                verifier_version=verification.verifier_version,
                reward_policy=f"{policy.policy_id}@{policy.version}",
                verdict=verification.verdict.value,
                reward=reward.final_reward,
                tool_calls=episode.tool_calls,
                termination_reason=episode.termination_reason,
            )
            self._deps.records.append(record)
            if self._deps.on_record:
                self._deps.on_record(record)
            self._scored = True
            return reward.final_reward
        finally:
            self._close()

    def _close(self) -> None:
        if self._env is not None:
            try:
                self._env.close()
            except Exception as exc:  # surfaced in logs; stale sandboxes are reaped
                log.error("sandbox cleanup failed: %s", exc)
        self._env, self._router = None, None

    def _call(self, name: str, arguments: dict[str, Any]) -> str:
        if self._router is None:
            return json.dumps(
                {"ok": False, "error": {"code": "terminal_state", "message": "no active episode"}}
            )
        result = self._router.execute(
            ToolCall(call_id=f"grpo-{uuid.uuid4().hex[:8]}", tool_name=name, arguments=arguments)
        )
        return json.dumps(
            {
                "ok": result.ok,
                "output": result.output,
                "error": result.error.model_dump(mode="json") if result.error else None,
            },
            default=str,
        )

    # ------------------------------------------------------------------ tools (public methods)
    def list_files(self, path: str = ".") -> str:
        """List files in the task workspace.

        Args:
            path: Workspace-relative directory or file path.
        """
        return self._call("list_files", {"path": path})

    def read_file(self, path: str) -> str:
        """Read a UTF-8 file from the task workspace.

        Args:
            path: Workspace-relative file path.
        """
        return self._call("read_file", {"path": path})

    def apply_patch(self, path: str, old: str, new: str) -> str:
        """Replace one exact occurrence of `old` with `new` in an editable file.

        Args:
            path: Workspace-relative path of an editable file.
            old: Exact text to replace; must occur exactly once.
            new: Replacement text.
        """
        return self._call(
            "apply_patch", {"path": path, "mode": "search_replace", "old": old, "new": new}
        )

    def run_visible_tests(self, test_selector: str = "") -> str:
        """Run the visible test suite.

        Args:
            test_selector: Optional substring filter on test names.
        """
        return self._call("run_visible_tests", {"test_selector": test_selector})

    def inspect_test_output(self, run_id: str) -> str:
        """Show per-test results of a previous visible test run.

        Args:
            run_id: The run_id returned by run_visible_tests.
        """
        return self._call("inspect_test_output", {"run_id": run_id})

    def submit_evidence(self, evidence_type: str, artifact_id: str, explanation: str) -> str:
        """Attach evidence supporting the fix.

        Args:
            evidence_type: One of test_run, patch, file_read.
            artifact_id: The run_id, patch_id or read_id being cited.
            explanation: Short explanation of what the evidence shows.
        """
        return self._call(
            "submit_evidence",
            {
                "evidence_type": evidence_type,
                "artifact_id": artifact_id,
                "explanation": explanation,
            },
        )

    def submit_final_report(self, summary: str, claimed_success: bool) -> str:
        """Finish the episode. The outcome is decided by independent verification.

        Args:
            summary: Summary of the change made.
            claimed_success: Whether you believe the task is solved.
        """
        return self._call(
            "submit_final_report", {"summary": summary, "claimed_success": claimed_success}
        )


def make_environment_factory(deps: EnvironmentDeps) -> Callable[[], TraceForgeToolEnv]:
    def factory() -> TraceForgeToolEnv:
        return TraceForgeToolEnv(deps)

    return factory


def rl_prompt_rows(
    registry: TaskRegistry, splits: tuple[str, ...] = ("train",)
) -> list[dict[str, Any]]:
    """Prompt dataset for GRPO. Only training-split tasks; held-out oracles never enter training."""
    from traceforge.contracts.tasks import Split
    from traceforge.modules.agents.prompts import SYSTEM_PROMPT

    rows = []
    for b in registry.list(splits=[Split(s) for s in splits]):
        if b.oracle.split.value not in ("train", "validation"):
            continue
        rows.append(
            {
                "prompt": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Solve task {b.spec.task_id}."},
                ],
                "task_id": b.spec.task_id,
                "task_version": b.spec.version,
            }
        )
    return rows
