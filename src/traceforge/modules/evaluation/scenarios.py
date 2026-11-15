"""Structural scenarios: the independent resampling unit of paired comparisons.

Seeds repeat a task; generated task instances of one template vary only names and literal
values. The structural scenario, a group of templates with one authoritative oracle identity
(``modules/tasks/oracle_identity.py``), is the highest unit below the task family, and paired
comparisons resample it. Independence of scenarios is an *assumption* (they share families,
authors, the harness and the verifier), stated in every comparison report.

Scenario membership comes only from authoritative sources, never from record metadata:

* Legacy task versions 1.0.0 and 1.1.0 use ``LEGACY_SCENARIOS``: an explicit, version-scoped
  registry with pinned oracle fingerprints. At resolution time the task is rebuilt from the
  task registry, its oracle fingerprint is recomputed and must equal the pin.
* Any other task version needs the authoritative benchmark catalogue (a
  ``CatalogueScenarioResolver``, provided by Benchmark v2). Without one, resolution is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from traceforge.modules.tasks.oracle_identity import oracle_fingerprint
from traceforge.modules.tasks.registry import VERSION_PROFILES, TaskRegistry

SCENARIO_REGISTRY_VERSION = "1"
LEGACY_TASK_VERSIONS = ("1.0.0", "1.1.0")


@dataclass(frozen=True)
class LegacyScenario:
    scenario_id: str
    reason: str
    oracle_fingerprint: str


_OWN = "own oracle: hidden tests, visible tests and verifier configuration not shared"
_SSRF_SHARED = (
    "t0 and t1 render the same visible and hidden oracle tests (VALIDATE_VISIBLE / "
    "VALIDATE_HIDDEN in families/ssrf.py) under identical verifier configuration, so their "
    "normalised oracle fingerprints are equal; only the vulnerable module differs. Template ids "
    "and task identities stay separate; only the structural scenario is shared."
)

# (task_version, family_id, template_id) -> scenario. Fingerprints are pinned: a change in the
# generator or oracle that alters them must be a deliberate, reviewed registry update.
LEGACY_SCENARIOS: dict[tuple[str, str, str], LegacyScenario] = {
    ("1.0.0", "sql_injection", "t0"): LegacyScenario(
        "sql_injection/t0",
        _OWN,
        "1861dd791ec6686d5978719f90f7844d18920e8017a272eeafac9091d3dd8fe9",
    ),
    ("1.0.0", "sql_injection", "t1"): LegacyScenario(
        "sql_injection/t1",
        _OWN,
        "6923989bb07facf5a4388dbc720c05ba14023cdc729bcd37fd663fe8b752ba8c",
    ),
    ("1.0.0", "sql_injection", "t2"): LegacyScenario(
        "sql_injection/t2",
        _OWN,
        "eb47f8bbaeae1c3598a8614e090200b4a970036de4dbe5762e4a5553641976fe",
    ),
    ("1.0.0", "access_control", "t0"): LegacyScenario(
        "access_control/t0",
        _OWN,
        "35e875da7eb4990a6d0dedbdc276ac2f244bbb0324858edec50e88ddbf64766a",
    ),
    ("1.0.0", "access_control", "t1"): LegacyScenario(
        "access_control/t1",
        _OWN,
        "4ab37ac5ddbed7eddd619febaa91ed83b589d7afb520d1331240875c723cb5bf",
    ),
    ("1.0.0", "access_control", "t2"): LegacyScenario(
        "access_control/t2",
        _OWN,
        "137b14844f2f66cd93384566a8055db97317642055678cfde17ada5f7ae87898",
    ),
    ("1.0.0", "ssrf", "t0"): LegacyScenario(
        "ssrf/t0+t1",
        _SSRF_SHARED,
        "12d96a023f5ec283a62da6ecdf31b3278de560ed4675d40c9abe353dd5c59ef1",
    ),
    ("1.0.0", "ssrf", "t1"): LegacyScenario(
        "ssrf/t0+t1",
        _SSRF_SHARED,
        "12d96a023f5ec283a62da6ecdf31b3278de560ed4675d40c9abe353dd5c59ef1",
    ),
    ("1.0.0", "ssrf", "t2"): LegacyScenario(
        "ssrf/t2",
        _OWN,
        "0441abfecae4b2426c44de97fca1e4e6c9644a172e6d08653222e0f92f2eef34",
    ),
    ("1.1.0", "sql_injection", "t0"): LegacyScenario(
        "sql_injection/t0",
        _OWN,
        "c112395acae120e7c147365e7bafc5485f0103eeeca9977d8b1ee804bfb33911",
    ),
    ("1.1.0", "sql_injection", "t1"): LegacyScenario(
        "sql_injection/t1",
        _OWN,
        "70c120aca463af93cc8266bebbc3a055ef2dd143a961adc79e97fd31ee294004",
    ),
    ("1.1.0", "sql_injection", "t2"): LegacyScenario(
        "sql_injection/t2",
        _OWN,
        "6f0245bd1d4c1ea1becda6a36ccec15a5ebdd0ef9705130897b2ba98b7017ac5",
    ),
    ("1.1.0", "access_control", "t0"): LegacyScenario(
        "access_control/t0",
        _OWN,
        "162e77cbd5d8ee9e759e5a36cd6da64ee52228394ea1ebf5d8c914f91fcff5a8",
    ),
    ("1.1.0", "access_control", "t1"): LegacyScenario(
        "access_control/t1",
        _OWN,
        "3f2e1fc6282a2f5c2acf39ee19612a21cc0414b346d5962c379ca982f0bb2eba",
    ),
    ("1.1.0", "access_control", "t2"): LegacyScenario(
        "access_control/t2",
        _OWN,
        "572b2a28027ba5c5c8963def05da456fb34c691e5e420007447fb85bfdf8424b",
    ),
    ("1.1.0", "ssrf", "t0"): LegacyScenario(
        "ssrf/t0+t1",
        _SSRF_SHARED,
        "3b041f2ba368b865bf974339c75860443d0f7ae1a3acaa5a3b69fd71139b90c9",
    ),
    ("1.1.0", "ssrf", "t1"): LegacyScenario(
        "ssrf/t0+t1",
        _SSRF_SHARED,
        "3b041f2ba368b865bf974339c75860443d0f7ae1a3acaa5a3b69fd71139b90c9",
    ),
    ("1.1.0", "ssrf", "t2"): LegacyScenario(
        "ssrf/t2",
        _OWN,
        "6fff62e9551f7cdd608912013bc79e1abe20c438811428de405aebb8a11445f5",
    ),
}


@dataclass(frozen=True)
class ScenarioRef:
    """Authoritative identity of the task behind one episode."""

    scenario_id: str
    family_id: str
    template_id: str
    split: str
    difficulty: str
    oracle_fingerprint: str
    source: str  # legacy_registry | catalogue
    benchmark_identity: dict[str, Any] | None = None


class ScenarioError(Exception):
    """A record whose task or scenario identity cannot be verified (always refused)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class ScenarioResolver(Protocol):
    def resolve(self, record: Any) -> ScenarioRef: ...


class CatalogueScenarioResolver(Protocol):
    """Resolver backed by an authoritative benchmark catalogue (Benchmark v2).

    An implementation must, for every record: load the catalogue, recompute and check its hash;
    check the run's benchmark identity against it; require ``task_id@task_version`` to exist in
    it; require the catalogue's scenario assignment to equal ``metadata.scenario_id``; recompute
    the oracle fingerprint and require it to equal the catalogue's; and raise ``ScenarioError``
    otherwise.
    """

    def handles(self, task_version: str) -> bool: ...

    def resolve(self, record: Any) -> ScenarioRef: ...


def _meta(record: Any, key: str) -> Any:
    md = getattr(record, "metadata", None) or {}
    return md.get(key)


def _value(x: Any) -> str:
    return str(getattr(x, "value", x))


class LegacyScenarioResolver:
    """Resolves task versions 1.0.0/1.1.0 through the task registry and ``LEGACY_SCENARIOS``."""

    source = "legacy_registry"

    def __init__(self, registry: TaskRegistry) -> None:
        self.registry = registry
        self._cache: dict[tuple[str, str], tuple[Any, str]] = {}

    def handles(self, task_version: str) -> bool:
        return task_version in LEGACY_TASK_VERSIONS

    def _bundle_and_fingerprint(self, task_id: str, version: str) -> tuple[Any, str]:
        key = (task_id, version)
        if key not in self._cache:
            try:
                bundle = self.registry.bundle(task_id, version)
            except Exception as exc:  # NotFound / UnsupportedTaskVersion / ConflictError
                raise ScenarioError("unknown_task", f"{task_id}@{version}: {exc}") from exc
            fp = oracle_fingerprint(bundle, VERSION_PROFILES[version]["oracle"])
            self._cache[key] = (bundle, fp)
        return self._cache[key]

    def resolve(self, record: Any) -> ScenarioRef:
        task_id, version = str(record.task_id), str(record.task_version)
        if not self.handles(version):
            raise ScenarioError(
                "scenario_unverifiable",
                f"{task_id}@{version} is not a legacy task version and no authoritative "
                "benchmark catalogue resolver is available",
            )
        bundle, fp = self._bundle_and_fingerprint(task_id, version)
        o = bundle.oracle
        env = getattr(record, "environment_ref", None)
        if env is not None and env != bundle.spec.environment_ref:
            raise ScenarioError(
                "task_identity_mismatch",
                f"{task_id}@{version}: environment_ref {env!r} != registry "
                f"{bundle.spec.environment_ref!r}",
            )
        for field, expected in (
            ("family", o.family_id),
            ("template", o.template_id),
            ("split", _value(o.split)),
        ):
            got = _meta(record, field)
            if got is not None and str(got) != expected:
                raise ScenarioError(
                    "task_identity_mismatch",
                    f"{task_id}@{version}: metadata {field}={got!r} != registry {expected!r}",
                )
        entry = LEGACY_SCENARIOS.get((version, o.family_id, o.template_id))
        if entry is None:
            raise ScenarioError(
                "scenario_unresolved", f"no legacy scenario for {o.family_id}/{o.template_id}"
            )
        if fp != entry.oracle_fingerprint:
            raise ScenarioError(
                "oracle_identity_mismatch",
                f"{task_id}@{version}: recomputed oracle fingerprint {fp[:12]} != pinned "
                f"{entry.oracle_fingerprint[:12]} for scenario {entry.scenario_id}",
            )
        claimed = _meta(record, "scenario_id")
        if claimed is not None and claimed != entry.scenario_id:
            raise ScenarioError(
                "scenario_mismatch",
                f"{task_id}@{version}: metadata scenario_id {claimed!r} != registry "
                f"{entry.scenario_id!r} (metadata never overrides the registry)",
            )
        return ScenarioRef(
            scenario_id=entry.scenario_id,
            family_id=o.family_id,
            template_id=o.template_id,
            split=_value(o.split),
            difficulty=_value(bundle.spec.difficulty),
            oracle_fingerprint=fp,
            source=self.source,
        )


class CompositeScenarioResolver:
    """Legacy versions via the registry; everything else via the catalogue resolver, if any."""

    def __init__(
        self, legacy: LegacyScenarioResolver, catalogue: CatalogueScenarioResolver | None = None
    ) -> None:
        self.legacy = legacy
        self.catalogue = catalogue

    def resolve(self, record: Any) -> ScenarioRef:
        version = str(record.task_version)
        if self.legacy.handles(version):
            return self.legacy.resolve(record)
        if self.catalogue is not None and self.catalogue.handles(version):
            return self.catalogue.resolve(record)
        raise ScenarioError(
            "scenario_unverifiable",
            f"{record.task_id}@{version}: no authoritative scenario source for this task version",
        )
