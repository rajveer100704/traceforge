"""Experiment, training, evaluation and job endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, status

from traceforge.api.dependencies import correlation_id, get_platform, require_api_key
from traceforge.api.schemas import (
    CancelResponse,
    ExperimentCreateRequest,
    JobAcceptedResponse,
    TrainRequest,
)
from traceforge.application.container import Platform
from traceforge.contracts.experiments import BenchmarkConfig, ExperimentSpec
from traceforge.contracts.jobs import JobKind, JobView

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.post("/experiments", status_code=status.HTTP_201_CREATED, tags=["experiments"])
def create_experiment(
    body: ExperimentCreateRequest, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    spec = ExperimentSpec(
        name=body.name, kind=body.kind, description=body.description, config=body.config
    )
    experiment_id = platform.experiments.create(spec)
    return platform.experiments.get(experiment_id)


@router.get("/experiments/{experiment_id}", tags=["experiments"])
def get_experiment(
    experiment_id: uuid.UUID, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    return platform.experiments.get(experiment_id)


@router.post(
    "/experiments/{experiment_id}/train",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
    tags=["experiments"],
)
def train(
    experiment_id: uuid.UUID,
    body: TrainRequest,
    platform: Platform = Depends(get_platform),
    cid: str = Depends(correlation_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
) -> JobAcceptedResponse:
    platform.experiments.get(experiment_id)  # 404 if missing
    payload = {"experiment_id": str(experiment_id), **body.model_dump(mode="json")}
    job = platform.jobs.submit(
        JobKind.TRAINING, payload, idempotency_key=idempotency_key, correlation_id=cid
    )
    return JobAcceptedResponse(
        job_id=job.job_id, status=job.status, status_url=f"/api/v1/jobs/{job.job_id}"
    )


@router.post(
    "/evaluations",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
    tags=["evaluations"],
)
def create_evaluation(
    body: BenchmarkConfig,
    platform: Platform = Depends(get_platform),
    cid: str = Depends(correlation_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
) -> JobAcceptedResponse:
    platform.policies.get(body.reward_policy)
    job = platform.jobs.submit(
        JobKind.EVALUATION,
        {"config": body.model_dump(mode="json")},
        idempotency_key=idempotency_key,
        correlation_id=cid,
    )
    return JobAcceptedResponse(
        job_id=job.job_id, status=job.status, status_url=f"/api/v1/jobs/{job.job_id}"
    )


@router.get("/evaluations/{evaluation_id}", tags=["evaluations"])
def get_evaluation(
    evaluation_id: uuid.UUID, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    return platform.evaluations.get(evaluation_id)


@router.get("/jobs/{job_id}", response_model=JobView, tags=["jobs"])
def get_job(job_id: uuid.UUID, platform: Platform = Depends(get_platform)) -> JobView:
    return platform.jobs.get(job_id)


@router.post("/jobs/{job_id}/cancel", response_model=CancelResponse, tags=["jobs"])
def cancel_job(job_id: uuid.UUID, platform: Platform = Depends(get_platform)) -> CancelResponse:
    job = platform.jobs.cancel(job_id)
    return CancelResponse(
        job_id=job.job_id, status=job.status, cancel_requested=job.cancel_requested
    )
