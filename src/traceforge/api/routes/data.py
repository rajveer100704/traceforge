"""Trace ingestion and dataset endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, status

from traceforge.api.dependencies import correlation_id, get_platform, require_api_key
from traceforge.api.schemas import (
    DatasetBuildRequest,
    IngestRequest,
    IngestResponse,
    JobAcceptedResponse,
)
from traceforge.application.container import Platform
from traceforge.contracts.common import ErrorCode, TraceForgeError
from traceforge.contracts.jobs import JobKind
from traceforge.contracts.traces import Trajectory
from traceforge.infrastructure.database.repositories import TrajectoryRepository

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


class PayloadTooLarge(TraceForgeError):
    code = ErrorCode.SIZE_LIMIT


@router.post("/traces/ingest", response_model=IngestResponse, tags=["traces"])
def ingest(body: IngestRequest, platform: Platform = Depends(get_platform)) -> IngestResponse:
    data = body.content.encode("utf-8")
    if len(data) > platform.settings.max_ingest_bytes:
        raise PayloadTooLarge(f"content exceeds {platform.settings.max_ingest_bytes} bytes")
    summary = platform.ingestion.ingest_bytes(body.source_name, data)
    return IngestResponse(
        source_uri=summary.source_uri,
        raw_artifact_uri=summary.raw_artifact_uri,
        stored=len(summary.stored_trajectory_ids),
        quarantined=summary.quarantined,
        stored_trajectory_ids=summary.stored_trajectory_ids,
        report=summary.report.model_dump(mode="json"),
    )


@router.get("/traces/{trajectory_id}", response_model=Trajectory, tags=["traces"])
def get_trace(trajectory_id: uuid.UUID, platform: Platform = Depends(get_platform)) -> Trajectory:
    with platform.db.session() as s:
        return TrajectoryRepository(s).get(trajectory_id)


@router.post(
    "/datasets/build",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
    tags=["datasets"],
)
def build_dataset(
    body: DatasetBuildRequest,
    platform: Platform = Depends(get_platform),
    cid: str = Depends(correlation_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
) -> JobAcceptedResponse:
    job = platform.jobs.submit(
        JobKind.DATASET_BUILD,
        body.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        correlation_id=cid,
    )
    return JobAcceptedResponse(
        job_id=job.job_id, status=job.status, status_url=f"/api/v1/jobs/{job.job_id}"
    )


@router.get("/datasets/{dataset_id}", tags=["datasets"])
def get_dataset(
    dataset_id: uuid.UUID, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    out = platform.datasets.get(dataset_id)
    return {"dataset_version_id": out["dataset_version_id"], "manifest": out["manifest"]}


@router.get("/datasets/{dataset_id}/quality", tags=["datasets"])
def get_dataset_quality(
    dataset_id: uuid.UUID, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    out = platform.datasets.get(dataset_id)
    return {"dataset_version_id": out["dataset_version_id"], "quality": out["quality"]}
