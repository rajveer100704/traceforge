"""Rollout, verification and reward endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, status

from traceforge.api.dependencies import correlation_id, get_platform, require_api_key
from traceforge.api.schemas import (
    JobAcceptedResponse,
    RolloutCreateRequest,
    VerificationCreateRequest,
    VerificationCreateResponse,
)
from traceforge.application.container import Platform
from traceforge.contracts.common import CapabilityUnavailable
from traceforge.contracts.experiments import RolloutRequest, RolloutSummary
from traceforge.contracts.jobs import JobKind
from traceforge.contracts.rewards import RewardBreakdown
from traceforge.contracts.verification import VerificationResult

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])
ROLLOUT_NS = uuid.UUID("0b8f0a3e-2a7c-4f43-9a51-7d0a5b8e2c19")


@router.post(
    "/rollouts",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
    tags=["rollouts"],
)
def create_rollout(
    body: RolloutCreateRequest,
    platform: Platform = Depends(get_platform),
    cid: str = Depends(correlation_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
) -> JobAcceptedResponse:
    # 404 early for unknown tasks; pin the concrete version at acceptance so a queued or retried
    # job cannot silently switch task versions across a deployment.
    task_version = platform.registry.spec(body.task_id, body.task_version).version
    platform.policies.get(body.reward_policy)  # 404 early for unknown policies
    if (
        body.provider.kind != "demo"
        and platform.sandbox.trusted_code_only
        and not platform.settings.allow_untrusted_local_execution
    ):
        raise CapabilityUnavailable(
            f"sandbox '{platform.sandbox.name}' only runs trusted demo policies; configure the docker sandbox "
            "for model providers"
        )
    # Deterministic id under an idempotency key so a replayed request maps to the same job/rollout.
    rollout_id = uuid.uuid5(ROLLOUT_NS, idempotency_key) if idempotency_key else uuid.uuid4()
    request = RolloutRequest(
        task_id=body.task_id,
        task_version=task_version,
        provider=body.provider,
        sandbox=platform.sandbox.name,
        reward_policy=body.reward_policy,
        verifier_profile=body.verifier_profile.value,
        seed=body.seed,
    )
    job = platform.jobs.submit(
        JobKind.ROLLOUT,
        {"request": request.model_dump(mode="json"), "rollout_id": str(rollout_id)},
        idempotency_key=idempotency_key,
        correlation_id=cid,
    )
    actual_id = str(
        job.input.get("rollout_id", rollout_id)
    )  # idempotent replay returns the original id
    return JobAcceptedResponse(
        job_id=job.job_id,
        status=job.status,
        status_url=f"/api/v1/jobs/{job.job_id}",
        resource_id=actual_id,
        resource_url=f"/api/v1/rollouts/{actual_id}",
    )


@router.get("/rollouts/{rollout_id}", response_model=RolloutSummary, tags=["rollouts"])
def get_rollout(
    rollout_id: uuid.UUID, platform: Platform = Depends(get_platform)
) -> RolloutSummary:
    return platform.rollouts.get(rollout_id)


@router.post("/verifications", response_model=VerificationCreateResponse, tags=["verifications"])
def create_verification(
    body: VerificationCreateRequest, platform: Platform = Depends(get_platform)
) -> VerificationCreateResponse:
    """Re-run independent verification over a stored, immutable episode record."""
    verification, reward = platform.rollouts.reverify(
        body.rollout_id, body.profile, body.reward_policy
    )
    return VerificationCreateResponse(
        verification=verification.model_dump(mode="json"),
        reward=reward.model_dump(mode="json") if reward else None,
    )


@router.get(
    "/verifications/{verification_id}", response_model=VerificationResult, tags=["verifications"]
)
def get_verification(
    verification_id: uuid.UUID, platform: Platform = Depends(get_platform)
) -> VerificationResult:
    return platform.verifications.get(verification_id)


@router.get("/rewards/{reward_id}", response_model=RewardBreakdown, tags=["rewards"])
def get_reward(reward_id: uuid.UUID, platform: Platform = Depends(get_platform)) -> RewardBreakdown:
    return platform.rewards.get(reward_id)
