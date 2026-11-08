"""Task registry endpoints. Only PUBLIC task specs are ever returned."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from traceforge.api.dependencies import Pagination, get_platform, pagination, require_api_key
from traceforge.api.schemas import TaskCreateRequest, TaskListItem, TaskPage
from traceforge.application.container import Platform
from traceforge.contracts.common import NotFoundError, ValidationFailure
from traceforge.contracts.tasks import TaskSpec
from traceforge.infrastructure.database.repositories import TaskRepository
from traceforge.modules.tasks.registry import FAMILIES, build_bundle

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"], dependencies=[Depends(require_api_key)])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TaskSpec)
def create_task(body: TaskCreateRequest, platform: Platform = Depends(get_platform)) -> TaskSpec:
    family = FAMILIES.get(body.family)
    if family is None:
        raise NotFoundError(
            f"unknown task family '{body.family}'", details={"known": sorted(FAMILIES)}
        )
    if body.template_id not in family.templates:
        raise ValidationFailure(
            f"unknown template '{body.template_id}'", details={"known": list(family.templates)}
        )
    bundle = build_bundle(family, body.template_id, body.seed)
    platform.registry.register(bundle)  # immutable: same key + different content -> 409
    platform.tasks.sync_registry()
    return bundle.spec


@router.get("", response_model=TaskPage)
def list_tasks(
    page: Pagination = Depends(pagination),
    split: str | None = Query(
        default=None, pattern=r"^(train|validation|public_test|private_test)$"
    ),
    family: str | None = Query(default=None, max_length=64),
    platform: Platform = Depends(get_platform),
) -> TaskPage:
    with platform.db.session() as s:
        items, total = TaskRepository(s).list_specs(
            split=split, family=family, limit=page.limit, offset=page.offset
        )
    return TaskPage(
        items=[TaskListItem(**i) for i in items], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/{task_id}", response_model=TaskSpec)
def get_task(
    task_id: str, version: str | None = None, platform: Platform = Depends(get_platform)
) -> TaskSpec:
    with platform.db.session() as s:
        return TaskRepository(s).get_spec(task_id, version)
