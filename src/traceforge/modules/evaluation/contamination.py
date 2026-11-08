"""Split leakage / contamination checks across task family, template, fixture and lineage."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from traceforge.contracts.tasks import Split, TaskBundle

HELD_OUT = frozenset({Split.PUBLIC_TEST, Split.PRIVATE_TEST})


@dataclass
class LeakageReport:
    problems: list[str] = field(default_factory=list)
    counts_by_split: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems


def check_task_splits(bundles: Iterable[TaskBundle]) -> LeakageReport:
    report = LeakageReport()
    by_fixture: dict[str, set[Split]] = defaultdict(set)
    template_splits: dict[tuple[str, str], set[Split]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for b in bundles:
        o = b.oracle
        counts[o.split.value] += 1
        by_fixture[o.fixture_hash].add(o.split)
        template_splits[(o.family_id, o.template_id)].add(o.split)
    for fixture, splits in by_fixture.items():
        if len(splits) > 1:
            report.problems.append(
                f"fixture {fixture[:12]} appears in multiple splits {sorted(splits)}"
            )
    for (family, template), splits in template_splits.items():
        if Split.PRIVATE_TEST in splits and splits != {Split.PRIVATE_TEST}:
            report.problems.append(
                f"private template {family}/{template} also used in {sorted(splits)}"
            )
    report.counts_by_split = dict(sorted(counts.items()))
    return report


def check_dataset_lineage(
    trajectory_task_ids: Iterable[str | None], task_split: dict[str, Split]
) -> list[str]:
    """Training datasets must not contain trajectories derived from held-out tasks."""
    problems = []
    for task_id in trajectory_task_ids:
        if task_id and task_split.get(task_id) in HELD_OUT:
            problems.append(f"trajectory for held-out task {task_id} in training data")
    return problems
