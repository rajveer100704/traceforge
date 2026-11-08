"""Resolve a model's declared training lineage into task ids (evaluator side).

Sources: dataset versions (task ids read from the immutable Parquet partitions), training runs
(``metrics.training_task_ids`` recorded at training time, plus their dataset version), and task
ids declared directly. Anything that cannot be resolved is reported as a problem, never ignored:
unknown lineage cannot be shown to be disjoint from the benchmark.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from traceforge.contracts.common import NotFoundError
from traceforge.contracts.experiments import TrainingLineage
from traceforge.infrastructure.database import models as m

if TYPE_CHECKING:
    from traceforge.application.container import Platform


@dataclass
class ResolvedLineage:
    task_ids: set[str] = field(default_factory=set)
    sources: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    imported_rows_without_task: int = 0


def dataset_task_ids(p: Platform, dataset_version_id: str) -> tuple[set[str], int]:
    """(task ids, rows without a task id) over all partitions of a dataset version."""
    from traceforge.modules.trace_processing.dataset_builder import read_partition

    with p.db.session() as s:
        row = s.get(m.DatasetVersion, uuid.UUID(dataset_version_id))
        if row is None:
            raise NotFoundError(f"dataset version {dataset_version_id} not found")
        manifest: dict[str, Any] = row.manifest
    prefix = f"datasets/{manifest['dataset_id']}/{manifest['version']}"
    ids: set[str] = set()
    missing = 0
    for part in manifest["partitions"]:
        for r in read_partition(p.store.get_bytes(f"{prefix}/{part['name']}")):
            tid = str(r.get("task_id") or "")
            if tid:
                ids.add(tid)
            else:
                missing += 1
    return ids, missing


def resolve_lineage(p: Platform, lineage: TrainingLineage) -> ResolvedLineage:
    out = ResolvedLineage(task_ids=set(lineage.task_ids))
    if lineage.task_ids:
        out.sources.append(f"declared_task_ids:{len(lineage.task_ids)}")
    dataset_ids = list(lineage.dataset_version_ids)
    for run_id in lineage.training_run_ids:
        with p.db.session() as s:
            try:
                run = s.get(m.TrainingRun, uuid.UUID(run_id))
            except ValueError:
                run = None
            if run is None:
                out.problems.append(f"training run {run_id} not found (lineage unknown)")
                continue
            recorded = (run.metrics or {}).get("training_task_ids")
            dv = str(run.dataset_version_id) if run.dataset_version_id else None
        out.sources.append(f"training_run:{run_id}")
        if recorded is not None:
            out.task_ids.update(str(t) for t in recorded)
        if dv:
            dataset_ids.append(dv)
        if recorded is None and dv is None:
            out.problems.append(
                f"training run {run_id} records no training task ids or dataset (lineage unknown)"
            )
    for dv in sorted(set(dataset_ids)):
        try:
            ids, missing = dataset_task_ids(p, dv)
        except NotFoundError:
            out.problems.append(f"dataset version {dv} not found (lineage unknown)")
            continue
        out.sources.append(f"dataset_version:{dv}")
        out.task_ids.update(ids)
        out.imported_rows_without_task += missing
    return out
