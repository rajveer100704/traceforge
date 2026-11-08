"""Shared fixtures. Core tests need no GPU, network, paid API or Docker."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

from traceforge.application.rollouts import RolloutArtifacts, RolloutExecutor
from traceforge.contracts.experiments import ProviderConfig
from traceforge.contracts.tasks import TaskBundle
from traceforge.infrastructure.sandbox.local_provider import LocalTestSandboxProvider
from traceforge.modules.agents.runner import AgentRunner
from traceforge.modules.environments.task_env import EnvironmentPolicy
from traceforge.modules.rewards.engine import RewardEngine
from traceforge.modules.rewards.policies import PolicyRegistry
from traceforge.modules.tasks.registry import TaskRegistry, default_registry
from traceforge.modules.verification.engine import VerificationEngine

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_TRACES = ROOT / "examples" / "traces" / "synthetic_traces.jsonl"
ONE_PER_FAMILY = ("sqli-t0-s000", "authz-t1-s000", "ssrf-t2-s000")


@pytest.fixture(scope="session")
def registry() -> TaskRegistry:
    return default_registry()


@pytest.fixture(scope="session")
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> LocalTestSandboxProvider:
    return LocalTestSandboxProvider(tmp_path_factory.mktemp("sbx"))


@pytest.fixture(scope="session")
def executor(sandbox: LocalTestSandboxProvider) -> RolloutExecutor:
    return RolloutExecutor(
        AgentRunner(sandbox), VerificationEngine(sandbox), RewardEngine(), PolicyRegistry()
    )


@pytest.fixture(scope="session")
def run_policy(executor: RolloutExecutor):  # type: ignore[no-untyped-def]
    cache: dict[tuple[str, str, bool], RolloutArtifacts] = {}

    def run(
        bundle: TaskBundle, policy: str = "reference", permissive: bool = False
    ) -> RolloutArtifacts:
        key = (bundle.spec.task_id, policy, permissive)
        if key not in cache:
            cache[key] = asyncio.run(
                executor.run(
                    bundle,
                    ProviderConfig(policy=policy),
                    env_policy=EnvironmentPolicy(protect_visible_tests=not permissive),
                )
            )
        return cache[key]

    return run


@pytest.fixture
def platform(tmp_path: Path) -> Iterator[object]:
    from traceforge.application.container import Platform
    from traceforge.config import Settings

    p = Platform.build(Settings(env="test", data_dir=tmp_path / "data"))
    yield p
    p.close()
