"""Split leakage / contamination checks across task family, template, fixture and lineage.

Checks (all recorded in the report, in order):

* ``split_fixture_disjoint``    - a fixture appears in exactly one split;
* ``private_template_holdout``  - a private-test template is used by no other split;
* ``task_lineage``              - each bundle's spec/oracle agree, the fixture hash recomputes from
  the bundle's files, the environment ref names the same family/template/generator, and the split
  and task id match the declared split policy;
* ``training_overlap``          - tasks a model was trained on (dataset lineage, GRPO prompts, or
  declared task ids) share no task id, fixture, or private template with the evaluated tasks and
  are never held-out tasks themselves.

``LeakageReport.ok`` is False when any check reports a problem. Official benchmark runs refuse to
start on a failed report (see ``BenchmarkRunner``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from traceforge.contracts.common import sha256_json
from traceforge.contracts.tasks import Split, TaskBundle

HELD_OUT = frozenset({Split.PUBLIC_TEST, Split.PRIVATE_TEST})
TRAINING_SPLITS = frozenset({Split.TRAIN, Split.VALIDATION})
LEAKAGE_CHECKS_VERSION = "2"


@dataclass
class LeakageReport:
    problems: list[str] = field(default_factory=list)
    counts_by_split: dict[str, int] = field(default_factory=dict)
    checks: list[str] = field(default_factory=list)
    training_overlap: list[str] = field(default_factory=list)
    training_task_ids: list[str] = field(default_factory=list)
    unlinked_training_task_ids: list[str] = field(default_factory=list)
    training_sources: list[str] = field(default_factory=list)
    evaluated_task_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and not self.training_overlap

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks_version": LEAKAGE_CHECKS_VERSION,
            "checks": list(self.checks),
            "problems": list(self.problems),
            "training_overlap": list(self.training_overlap),
            "task_counts_by_split": dict(self.counts_by_split),
            "training_sources": list(self.training_sources),
            "training_task_ids": list(self.training_task_ids),
            "unlinked_training_task_ids": list(self.unlinked_training_task_ids),
            "evaluated_task_ids": list(self.evaluated_task_ids),
        }


def check_task_splits(bundles: Iterable[TaskBundle]) -> LeakageReport:
    bundles = list(bundles)
    report = LeakageReport(checks=["split_fixture_disjoint", "private_template_holdout"])
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
    report.checks.append("task_lineage")
    report.problems.extend(check_task_lineage(bundles))
    return report


def check_task_lineage(bundles: Iterable[TaskBundle]) -> list[str]:
    """Verify each bundle's task/template/fixture lineage is internally consistent."""
    from traceforge.modules.tasks.registry import FAMILIES, assign_split, task_id_for

    problems: list[str] = []
    for b in bundles:
        s, o = b.spec, b.oracle
        tid = s.task_id
        if o.task_id != tid or o.version != s.version:
            problems.append(f"{tid}: spec/oracle identity mismatch ({o.task_id}@{o.version})")
        recomputed = sha256_json({"workspace": b.workspace_files, "hidden": b.hidden_files})
        if recomputed != o.fixture_hash:
            problems.append(f"{tid}: fixture hash does not match bundle contents")
        expected_ref = f"local-fixture:{o.family_id}/{o.template_id}@{o.generator_version}"
        if s.environment_ref != expected_ref:
            problems.append(f"{tid}: environment_ref {s.environment_ref!r} != {expected_ref!r}")
        if s.public_metadata.get("family") not in (None, o.family_id):
            problems.append(f"{tid}: public family metadata disagrees with oracle family")
        family = FAMILIES.get(o.family_id)
        if family is not None:
            if o.template_id not in family.templates:
                problems.append(f"{tid}: unknown template {o.template_id} for {o.family_id}")
            else:
                if o.split != assign_split(family, o.template_id, o.seed):
                    problems.append(f"{tid}: split {o.split.value} violates the split policy")
                if tid != task_id_for(family, o.template_id, o.seed):
                    problems.append(f"{tid}: task id does not match family/template/seed")
    return problems


def check_training_overlap(
    training_task_ids: Iterable[str],
    evaluated: Iterable[TaskBundle],
    lookup: Mapping[str, TaskBundle],
) -> list[str]:
    """Problems if any training task is held out, unknown, or shares lineage with an eval task."""
    evaluated = list(evaluated)
    eval_ids = {b.spec.task_id for b in evaluated}
    eval_fixtures = {b.oracle.fixture_hash: b.spec.task_id for b in evaluated}
    private_templates = {
        (b.oracle.family_id, b.oracle.template_id)
        for b in evaluated
        if b.oracle.split == Split.PRIVATE_TEST
    }
    problems: list[str] = []
    for tid in sorted(set(training_task_ids)):
        if tid in eval_ids:
            problems.append(f"training task {tid} is also an evaluated task")
        bundle = lookup.get(tid)
        if bundle is None:
            # Not a registered task, so it cannot be a benchmark task by id; recorded separately
            # (LeakageReport.unlinked_training_task_ids). Content-level overlap is not detected.
            continue
        o = bundle.oracle
        if o.split in HELD_OUT:
            problems.append(f"training task {tid} belongs to held-out split {o.split.value}")
        if o.fixture_hash in eval_fixtures and tid not in eval_ids:
            problems.append(
                f"training task {tid} shares fixture with evaluated task {eval_fixtures[o.fixture_hash]}"
            )
        if (o.family_id, o.template_id) in private_templates:
            problems.append(
                f"training task {tid} uses private held-out template {o.family_id}/{o.template_id}"
            )
    return problems


def check_dataset_lineage(
    trajectory_task_ids: Iterable[str | None], task_split: dict[str, Split]
) -> list[str]:
    """Training datasets must not contain trajectories derived from held-out tasks."""
    problems = []
    for task_id in trajectory_task_ids:
        if task_id and task_split.get(task_id) in HELD_OUT:
            problems.append(f"trajectory for held-out task {task_id} in training data")
    return problems


def check_training_rows(rows: Iterable[Mapping[str, Any]], registry: Any) -> list[str]:
    """Dataset partition rows used for training must reference training-split tasks only.

    Rows without a task id, or with an id that is not a registered task (imported traces), are
    allowed: they cannot be linked to any benchmark task by id. Known limitation: a held-out
    fixture re-imported under a different id is not detected (no content-level matching).
    """
    from traceforge.contracts.common import NotFoundError

    problems: list[str] = []
    for r in rows:
        tid = str(r.get("task_id") or "")
        if not tid:
            continue
        try:
            split = registry.bundle(tid).oracle.split
        except NotFoundError:
            continue
        if split not in TRAINING_SPLITS:
            problems.append(f"row for held-out task {tid} ({split.value}) in training data")
        if str(r.get("split") or "") not in ("", "train", "validation"):
            problems.append(f"row for {tid} labelled with non-training split {r.get('split')}")
    return problems


def check_prompt_rows(rows: Iterable[Mapping[str, Any]], registry: Any) -> list[str]:
    """GRPO prompt rows must name known training-split tasks."""
    from traceforge.contracts.common import NotFoundError

    problems: list[str] = []
    for r in rows:
        tid = str(r.get("task_id") or "")
        if not tid:
            problems.append("prompt row without task_id")
            continue
        try:
            split = registry.bundle(tid).oracle.split
        except NotFoundError:
            problems.append(f"prompt for unknown task {tid}")
            continue
        if split not in TRAINING_SPLITS:
            problems.append(f"prompt for held-out task {tid} ({split.value})")
    return problems
