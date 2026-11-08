"""Task registry -> sandbox -> agent tools -> trajectory -> verifier -> reward, across families."""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import ONE_PER_FAMILY
from traceforge.contracts.experiments import ProviderConfig
from traceforge.contracts.traces import Outcome, StepRole
from traceforge.contracts.verification import Verdict, VerifierProfile
from traceforge.modules.agents.providers.demo import ADVERSARIAL_POLICIES
from traceforge.modules.agents.runner import CancelToken


@pytest.mark.parametrize("task_id", ONE_PER_FAMILY)
def test_reference_passes_every_family(registry, run_policy, task_id) -> None:  # type: ignore[no-untyped-def]
    art = run_policy(registry.bundle(task_id), "reference")
    v = art.summary.verification
    assert v.verdict == Verdict.PASS, v.failure_reasons
    assert art.summary.reward.final_reward > 0.9
    assert (
        art.trajectory.outcome == Outcome.SUCCESS
        and art.trajectory.verification_id == v.verification_id
    )
    tool_steps = [s for s in art.trajectory.steps if s.role == StepRole.TOOL]
    assert len(tool_steps) == art.summary.tool_calls
    assert all(s.observation is not None for s in tool_steps)
    assert art.episode.patches and art.episode.test_runs and art.episode.final_report is not None
    assert v.agent_feedback and "test_sec_" not in v.agent_feedback


@pytest.mark.parametrize("task_id", ONE_PER_FAMILY)
@pytest.mark.parametrize("policy", ADVERSARIAL_POLICIES)
def test_adversarial_policies_never_rewarded(registry, run_policy, task_id, policy) -> None:  # type: ignore[no-untyped-def]
    art = run_policy(registry.bundle(task_id), policy)
    assert art.summary.reward.final_reward == 0.0
    assert art.summary.verification.verdict != Verdict.PASS
    assert art.trajectory.outcome != Outcome.SUCCESS


def test_permissive_harness_tamper_caught_by_state_verifier(registry, run_policy) -> None:  # type: ignore[no-untyped-def]
    bundle = registry.bundle("ssrf-t1-s000")
    art = run_policy(bundle, "test_tamper", permissive=True)
    assert (
        art.episode.final_files["tests/test_visible.py"]
        != bundle.workspace_files["tests/test_visible.py"]
    )
    assert art.summary.verification.check("state").reason_code == "protected_modified"
    assert art.summary.reward.final_reward == 0.0
    # The research-only visible-test baseline IS fooled — this is the documented R2 weakness.
    from traceforge.modules.verification.engine import VerificationEngine

    v2 = VerificationEngine(
        __import__("tests.conftest").conftest.LocalTestSandboxProvider()
    ).verify(bundle, art.episode, profile=VerifierProfile.VISIBLE_TESTS)
    assert v2.verdict == Verdict.PASS and not v2.authoritative


def test_episode_state_isolated_between_runs(registry, executor) -> None:  # type: ignore[no-untyped-def]
    bundle = registry.bundle("authz-t0-s001")
    first = asyncio.run(executor.run(bundle, ProviderConfig(policy="reference")))
    second = asyncio.run(executor.run(bundle, ProviderConfig(policy="noop")))
    # The noop run must start from pristine files, not the previous run's patched workspace.
    module = str(bundle.spec.public_metadata["module"])
    assert second.episode.final_files[module] == bundle.workspace_files[module]
    assert first.episode.final_files[module] != bundle.workspace_files[module]
    assert first.episode.run_id != second.episode.run_id


def test_cancellation_mid_rollout(registry, executor, sandbox) -> None:  # type: ignore[no-untyped-def]
    token = CancelToken()
    token.cancel()
    art = asyncio.run(executor.run(registry.bundle("sqli-t1-s000"), ProviderConfig(), cancel=token))
    assert art.summary.termination_reason == "cancelled"
    assert art.summary.reward.final_reward == 0.0
    assert sandbox.live_sandboxes() == []


def test_verification_idempotent(registry, run_policy, executor) -> None:  # type: ignore[no-untyped-def]
    bundle = registry.bundle("sqli-t0-s000")
    art = run_policy(bundle, "reference")
    again = executor.verifier.verify(bundle, art.episode)
    assert again.verdict == art.summary.verification.verdict
    assert [(c.name, c.status) for c in again.checks] == [
        (c.name, c.status) for c in art.summary.verification.checks
    ]


def test_untrusted_provider_refused_on_local_sandbox(registry, executor) -> None:  # type: ignore[no-untyped-def]
    from traceforge.contracts.common import CapabilityUnavailable

    cfg = ProviderConfig(kind="openai_compatible", base_url="http://127.0.0.1:9", model_id="m")
    with pytest.raises(CapabilityUnavailable, match="trusted demo"):
        asyncio.run(executor.run(registry.bundle("sqli-t0-s000"), cfg))


def test_relative_sandbox_dir_still_runs_tests(registry, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: relative base dirs once broke visible test execution silently."""
    from pathlib import Path

    from traceforge.infrastructure.sandbox.local_provider import LocalTestSandboxProvider

    monkeypatch.chdir(tmp_path)
    sb = LocalTestSandboxProvider(Path("rel-sbx"))
    bundle = registry.bundle("sqli-t0-s000")
    handle = sb.create("r", bundle.workspace_files, bundle.spec.budget)
    try:
        result = sb.run_tests(handle, "tests/test_visible.py", "", 20)
        assert result.harness_ok and result.total == 3
    finally:
        sb.destroy(handle)
