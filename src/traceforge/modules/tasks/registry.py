"""Task registry: immutable task versions, deterministic generation, split assignment."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

from traceforge.contracts.common import ConflictError, NotFoundError, sha256_json, sha256_text
from traceforge.contracts.tasks import (
    ResourceBudget,
    Split,
    TaskBundle,
    TaskOracleSpec,
    TaskSpec,
    VisibleFile,
)
from traceforge.modules.tasks.families.access_control import AccessControlFamily
from traceforge.modules.tasks.families.base import GENERATOR_VERSION, RenderedTask, TaskFamily
from traceforge.modules.tasks.families.sql_injection import SqlInjectionFamily
from traceforge.modules.tasks.families.ssrf import SsrfFamily

TASK_VERSION = "1.1.0"
SPLIT_POLICY = "template-holdout"
SPLIT_POLICY_VERSION = "1"
VERIFIER_ID = "traceforge.hidden-tests"
VERIFIER_VERSION = "1.1.0"
HIDDEN_TEST_MODULE = "test_hidden.py"
AGENT_TOOLS: tuple[str, ...] = (
    "list_files",
    "read_file",
    "apply_patch",
    "run_visible_tests",
    "inspect_test_output",
    "submit_evidence",
    "submit_final_report",
)
EDITABLE_GLOBS: tuple[str, ...] = ("app/*.py",)
PROTECTED_GLOBS: tuple[str, ...] = ("tests/*", "TASK.md", "app/__init__.py")

_FAMILY_LIST: list[TaskFamily] = [SqlInjectionFamily(), AccessControlFamily(), SsrfFamily()]
FAMILIES: dict[str, TaskFamily] = {f.family_id: f for f in _FAMILY_LIST}

# Default catalogue: seeds per template. The last template of each family is held out entirely
# (private test = unseen task-family variant).
DEFAULT_SEEDS: dict[str, int] = {"t0": 5, "t1": 5, "t2": 3}


def assign_split(family: TaskFamily, template_id: str, seed: int) -> Split:
    if template_id == family.templates[-1]:
        return Split.PRIVATE_TEST
    bucket = seed % 5
    if bucket in (0, 1, 2):
        return Split.TRAIN
    if bucket == 3:
        return Split.VALIDATION
    return Split.PUBLIC_TEST


def task_id_for(family: TaskFamily, template_id: str, seed: int) -> str:
    return f"{family.short}-{template_id}-s{seed:03d}"


def is_editable(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, g) for g in EDITABLE_GLOBS) and not is_protected(path)


def is_protected(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, g) for g in PROTECTED_GLOBS)


def build_bundle(family: TaskFamily, template_id: str, seed: int) -> TaskBundle:
    rendered: RenderedTask = family.render(template_id, seed)
    task_id = task_id_for(family, template_id, seed)
    hidden = {HIDDEN_TEST_MODULE: rendered.hidden_tests}
    fixture_hash = sha256_json({"workspace": rendered.workspace_files, "hidden": hidden})
    visible = tuple(
        VisibleFile(path=p, sha256=sha256_text(c), editable=is_editable(p))
        for p, c in sorted(rendered.workspace_files.items())
    )
    spec = TaskSpec(
        task_id=task_id,
        version=TASK_VERSION,
        category=family.category,
        title=f"{family.title} ({rendered.target_function})",
        objective=_objective_from(rendered),
        allowed_tools=AGENT_TOOLS,
        environment_ref=f"local-fixture:{family.family_id}/{template_id}@{GENERATOR_VERSION}",
        budget=ResourceBudget(),
        visible_files=visible,
        difficulty=rendered.difficulty,
        public_metadata={
            "family": family.family_id,
            "module": f"app/{rendered.module_name}.py",
            "target_function": rendered.target_function,
        },
    )
    oracle = TaskOracleSpec(
        task_id=task_id,
        version=TASK_VERSION,
        verifier_id=VERIFIER_ID,
        verifier_version=VERIFIER_VERSION,
        family_id=family.family_id,
        template_id=template_id,
        generator_version=GENERATOR_VERSION,
        seed=seed,
        split=assign_split(family, template_id, seed),
        hidden_test_module=HIDDEN_TEST_MODULE,
        editable_globs=EDITABLE_GLOBS,
        protected_globs=PROTECTED_GLOBS,
        allowed_imports=rendered.allowed_imports,
        fixture_hash=fixture_hash,
    )
    return TaskBundle(
        spec=spec,
        oracle=oracle,
        workspace_files=dict(rendered.workspace_files),
        hidden_files=hidden,
    )


def _objective_from(rendered: RenderedTask) -> str:
    task_md = rendered.workspace_files["TASK.md"]
    lines = task_md.split("\n\n")
    return lines[1] if len(lines) > 1 else task_md


class TaskRegistry:
    """In-memory immutable registry. Evaluator-side code may access bundles; agents get specs."""

    def __init__(self) -> None:
        self._bundles: dict[str, TaskBundle] = {}
        self._latest: dict[str, str] = {}

    def register(self, bundle: TaskBundle) -> None:
        key = bundle.spec.key
        existing = self._bundles.get(key)
        if existing is not None:
            if existing.oracle.fixture_hash != bundle.oracle.fixture_hash:
                raise ConflictError(f"task version {key} is immutable and already registered")
            return
        self._bundles[key] = bundle
        self._latest[bundle.spec.task_id] = bundle.spec.version

    def bundle(self, task_id: str, version: str | None = None) -> TaskBundle:
        version = version or self._latest.get(task_id)
        bundle = self._bundles.get(f"{task_id}@{version}")
        if bundle is None:
            raise NotFoundError(f"unknown task {task_id}@{version}")
        return bundle

    def spec(self, task_id: str, version: str | None = None) -> TaskSpec:
        return self.bundle(task_id, version).spec

    def list(
        self, splits: Iterable[Split] | None = None, families: Iterable[str] | None = None
    ) -> list[TaskBundle]:
        split_set = set(splits) if splits else None
        family_set = set(families) if families else None
        out = [
            b
            for b in self._bundles.values()
            if (split_set is None or b.oracle.split in split_set)
            and (family_set is None or b.oracle.family_id in family_set)
        ]
        return sorted(out, key=lambda b: b.spec.task_id)

    def __len__(self) -> int:
        return len(self._bundles)


def default_registry(seeds: dict[str, int] | None = None) -> TaskRegistry:
    registry = TaskRegistry()
    seeds = seeds or DEFAULT_SEEDS
    for family in FAMILIES.values():
        for template_id in family.templates:
            for seed in range(seeds.get(template_id, 0)):
                registry.register(build_bundle(family, template_id, seed))
    return registry


def validate_registry(registry: TaskRegistry) -> list[str]:
    """Structural validation of every registered task. Returns a list of problems."""
    problems: list[str] = []
    for bundle in registry.list():
        spec, oracle = bundle.spec, bundle.oracle
        if HIDDEN_TEST_MODULE in bundle.workspace_files or any(
            p.startswith("hidden") for p in bundle.workspace_files
        ):
            problems.append(f"{spec.key}: hidden material present in workspace")
        for content in bundle.workspace_files.values():
            if "test_sec_" in content:
                problems.append(f"{spec.key}: hidden test identifiers leaked into workspace")
        if not any(f.editable for f in spec.visible_files):
            problems.append(f"{spec.key}: no editable files")
        if "tests/test_visible.py" not in bundle.workspace_files:
            problems.append(f"{spec.key}: missing visible tests")
        if oracle.task_id != spec.task_id or oracle.version != spec.version:
            problems.append(f"{spec.key}: oracle/spec identity mismatch")
        if "test_sec_" not in bundle.hidden_files[oracle.hidden_test_module]:
            problems.append(f"{spec.key}: hidden module has no security tests")
    return problems
