"""Rollout orchestration: episode -> independent verification -> reward.

The executor is the only component that holds both the public spec (for the agent side) and the
oracle bundle (for the verifier side), and it keeps them on separate paths.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from traceforge.contracts.common import JSONValue
from traceforge.contracts.experiments import ProviderConfig, RolloutSummary
from traceforge.contracts.observations import EpisodeRecord
from traceforge.contracts.rewards import RewardBreakdown
from traceforge.contracts.tasks import TaskBundle
from traceforge.contracts.traces import Outcome, Trajectory
from traceforge.contracts.verification import Verdict, VerificationResult, VerifierProfile
from traceforge.domain.ports import GenerationConfig, ModelProvider
from traceforge.modules.agents.providers.factory import build_provider
from traceforge.modules.agents.runner import AgentRunner, CancelToken
from traceforge.modules.environments.task_env import EnvironmentPolicy
from traceforge.modules.rewards.engine import RewardEngine
from traceforge.modules.rewards.policies import PolicyRegistry
from traceforge.modules.verification.engine import VerificationEngine

VERDICT_TO_OUTCOME = {
    Verdict.PASS: Outcome.SUCCESS,
    Verdict.PARTIAL: Outcome.PARTIAL,
    Verdict.FAIL: Outcome.FAILURE,
    Verdict.ERROR: Outcome.UNKNOWN,
    Verdict.INCONCLUSIVE: Outcome.UNKNOWN,
}


@dataclass
class RolloutArtifacts:
    summary: RolloutSummary
    trajectory: Trajectory
    episode: EpisodeRecord
    cleanup_error: str | None = None


class RolloutExecutor:
    def __init__(
        self,
        runner: AgentRunner,
        verifier: VerificationEngine,
        rewards: RewardEngine,
        policies: PolicyRegistry,
    ) -> None:
        self.runner = runner
        self.verifier = verifier
        self.rewards = rewards
        self.policies = policies

    async def run(
        self,
        bundle: TaskBundle,
        provider_config: ProviderConfig,
        *,
        reward_policy: str = "verified-v1",
        verifier_profile: VerifierProfile = VerifierProfile.FULL,
        env_policy: EnvironmentPolicy | None = None,
        cancel: CancelToken | None = None,
        correlation_id: str | None = None,
        rollout_id: uuid.UUID | None = None,
        provider: ModelProvider | None = None,
    ) -> RolloutArtifacts:
        rollout_id = rollout_id or uuid.uuid4()
        correlation_id = correlation_id or str(rollout_id)
        provider = provider or build_provider(provider_config)
        started = time.perf_counter()
        # Agent side: public spec + visible workspace only.
        result = await self.runner.run_episode(
            bundle.spec,
            dict(bundle.workspace_files),
            provider,
            run_id=str(rollout_id),
            correlation_id=correlation_id,
            generation=GenerationConfig(
                temperature=provider_config.temperature,
                max_tokens=provider_config.max_tokens,
                seed=provider_config.seed,
                timeout_seconds=provider_config.timeout_seconds,
            ),
            env_policy=env_policy,
            cancel=cancel,
        )
        # Evaluator side: oracle bundle + host-recorded episode facts.
        verification = self.verifier.verify(
            bundle,
            result.episode,
            profile=verifier_profile,
            run_id=rollout_id,
            trajectory_id=result.trajectory.trajectory_id,
        )
        reward = self.rewards.compute(verification, self.policies.get(reward_policy))
        trajectory = self.link(result.trajectory, verification)
        summary = self.summarize(
            rollout_id,
            bundle,
            provider_config,
            result.episode,
            trajectory,
            verification,
            reward,
            time.perf_counter() - started,
        )
        summary.metadata["correlation_id"] = correlation_id
        summary.metadata["verifier_profile"] = verifier_profile.value
        summary.metadata["reward_policy"] = reward_policy
        if result.cleanup_error:
            summary.metadata["cleanup_error"] = result.cleanup_error
        return RolloutArtifacts(
            summary=summary,
            trajectory=trajectory,
            episode=result.episode,
            cleanup_error=result.cleanup_error,
        )

    @staticmethod
    def link(trajectory: Trajectory, verification: VerificationResult) -> Trajectory:
        """Attach the verified outcome. Non-authoritative verdicts never become SUCCESS."""
        outcome = (
            VERDICT_TO_OUTCOME[verification.verdict]
            if verification.authoritative
            else Outcome.UNKNOWN
        )
        return trajectory.model_copy(
            update={"outcome": outcome, "verification_id": verification.verification_id}
        )

    @staticmethod
    def summarize(
        rollout_id: uuid.UUID,
        bundle: TaskBundle,
        provider_config: ProviderConfig,
        episode: EpisodeRecord,
        trajectory: Trajectory,
        verification: VerificationResult,
        reward: RewardBreakdown,
        duration: float,
    ) -> RolloutSummary:
        return RolloutSummary(
            rollout_id=rollout_id,
            task_id=bundle.spec.task_id,
            task_version=bundle.spec.version,
            provider_kind=provider_config.kind,
            model_id=trajectory.model_id or provider_config.model_id,
            sandbox_provider=episode.sandbox_provider,
            environment_ref=episode.environment_ref,
            trajectory_id=trajectory.trajectory_id,
            steps=len(trajectory.steps),
            tool_calls=episode.tool_calls,
            rejected_actions=len(episode.rejected_actions),
            termination_reason=episode.termination_reason,
            duration_seconds=duration,
            verification=verification,
            reward=reward,
            metadata={
                "family": bundle.oracle.family_id,
                "template": bundle.oracle.template_id,
                "split": bundle.oracle.split.value,
                "difficulty": bundle.spec.difficulty.value,
                "policy": provider_config.policy if provider_config.kind == "demo" else None,
                "demo_provider": provider_config.kind == "demo",
                "protected_tests": episode.protected_tests,
                "seed": provider_config.seed,
                **generation_stats(trajectory),
            },
        )


def generation_stats(trajectory: Trajectory) -> dict[str, JSONValue]:
    """Per-episode generation telemetry captured from assistant steps.

    ``prompt_tokens``/``completion_tokens``/``generation_seconds`` keep their v0.2 meaning (sums
    over steps; tokens only from steps whose usage was reported). Everything else records what
    was and was not measured, so aggregation can report coverage instead of treating missing
    values as zero: request counts, how many reported usage or a known cost, the episode cost
    (None if any request's cost is unknown), per-request latencies, attempts/retries, finish
    reasons and any provider failure.
    """
    prompt = completion = 0
    seconds = 0.0
    requests = with_usage = with_cost = attempts_known = 0
    cost_total = 0.0
    attempts_total = retries = 0
    request_seconds: list[JSONValue] = []
    finish_reasons: dict[str, JSONValue] = {}
    failure: JSONValue = None
    failure_source: JSONValue = None
    for step in trajectory.steps:
        if step.role.value != "assistant":
            continue
        md = step.metadata
        requests += 1
        if step.token_usage is not None:
            prompt += step.token_usage.prompt_tokens
            completion += step.token_usage.completion_tokens
        if step.completed_at is not None:
            elapsed = (step.completed_at - step.started_at).total_seconds()
            seconds += elapsed
        else:
            elapsed = None
        if md.get("usage_reported", step.token_usage is not None):
            with_usage += 1
        cost = md.get("cost_usd")
        if isinstance(cost, int | float) and not isinstance(cost, bool):
            with_cost += 1
            cost_total += float(cost)
        measured = md.get("request_seconds")
        if isinstance(measured, int | float) and not isinstance(measured, bool):
            request_seconds.append(round(float(measured), 6))
        elif elapsed is not None:
            request_seconds.append(round(elapsed, 6))
        n_attempts = md.get("attempts")
        if isinstance(n_attempts, int) and not isinstance(n_attempts, bool):
            attempts_known += 1
            attempts_total += n_attempts
            retries += max(n_attempts - 1, 0)
        reason = md.get("finish_reason")
        key = str(reason) if reason is not None else "not_reported"
        if md.get("provider_failure") is None:
            count = finish_reasons.get(key, 0)
            finish_reasons[key] = (count if isinstance(count, int) else 0) + 1
        else:
            failure = md.get("provider_failure")
            failure_source = md.get("failure_source")
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "generation_seconds": round(seconds, 6),
        "n_generation_requests": requests,
        "n_requests_usage_reported": with_usage,
        "n_requests_cost_known": with_cost,
        # Unknown cost is never folded into a total: None unless every request's cost is known.
        "cost_usd": round(cost_total, 10) if with_cost == requests else None,
        "generation_request_seconds": request_seconds,
        "n_requests_attempts_known": attempts_known,
        # Like cost: totals only when every request's attempt count is known (a partial sum
        # would understate attempts and look complete). Coverage is n_requests_attempts_known.
        "provider_attempts": attempts_total if attempts_known == requests else None,
        "provider_retries": retries if attempts_known == requests else None,
        "finish_reasons": finish_reasons,
        "provider_failure": failure,
        "provider_failure_source": failure_source,
    }
