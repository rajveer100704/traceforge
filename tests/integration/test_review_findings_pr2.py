"""Regression tests for the eight Sourcery findings on PR #2 (verifier 1.1.0)."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from traceforge.contracts.common import ConflictError, UnsupportedTaskVersion
from traceforge.contracts.experiments import ProviderConfig, RolloutRequest
from traceforge.contracts.traces import Outcome
from traceforge.contracts.verification import Verdict, VerifierProfile
from traceforge.modules.agents.providers.heldout_attacks import universal_equality_source
from traceforge.modules.evaluation import attack_catalog
from traceforge.modules.evaluation.experiments import _episode
from traceforge.modules.rewards.engine import RewardEngine
from traceforge.modules.rewards.policies import VERIFIED_V1, PolicyRegistry
from traceforge.modules.tasks.families.legacy_1_0_0 import FIXTURE_HASHES_1_0_0
from traceforge.modules.tasks.registry import default_registry
from traceforge.modules.verification.engine import VerificationEngine
from traceforge.modules.verification.safety import scan_source

# ------------------------------------------------------------------ Finding 1: assignment bypass

ASSIGNMENT_FORMS = {
    "assign": "class A:\n    __eq__ = lambda self, other: True\n",
    "multi-target": "class A:\n    __eq__ = __ne__ = lambda self, other: True\n",
    "annassign": "class A:\n    __lt__: object = lambda self, other: True\n",
    "augassign": "class A:\n    __ge__ = None\n    __ge__ += 0\n",
    "nested-class": "class Outer:\n    class Inner:\n        __gt__ = lambda s, o: True\n",
    "aliased-callable": "def _always(s, o):\n    return True\n\n\nclass A:\n    __le__ = _always\n",
}


@pytest.mark.parametrize("src", ASSIGNMENT_FORMS.values(), ids=ASSIGNMENT_FORMS.keys())
def test_assignment_definitions_rejected(src: str) -> None:
    codes = [f.code for f in scan_source("app/x.py", src, ()).findings]
    assert codes and set(codes) == {"comparison_dunder_definition"}


def test_def_and_assignment_both_rejected_but_legit_methods_allowed() -> None:
    assert not scan_source(
        "app/x.py", "class A:\n    def __eq__(s, o):\n        return 1\n", ()
    ).safe
    legit = (
        "class A:\n    def __init__(self):\n        self.x = 1\n\n    __repr__ = object.__repr__\n"
    )
    findings = scan_source("app/x.py", legit, ()).findings
    # `object.__repr__` is attribute access to a dunder (pre-existing rule), not a comparison rule
    assert "comparison_dunder_definition" not in {f.code for f in findings}


class _NoExec:
    name, isolation, trusted_code_only = "no-exec", "none", True

    def __init__(self) -> None:
        self.calls = 0

    def __getattr__(self, item: str) -> Any:
        def _forbidden(*a: Any, **k: Any) -> Any:
            self.calls += 1
            raise AssertionError(f"sandbox.{item} called")

        return _forbidden


@pytest.mark.parametrize("form", ["assign", "aliased-callable"])
def test_assignment_form_blocked_before_execution(registry: Any, form: str) -> None:
    bundle = registry.bundle("sqli-t1-s000")
    module = str(bundle.spec.public_metadata["module"])
    files = dict(bundle.workspace_files)
    files[module] = files[module] + "\n\n" + ASSIGNMENT_FORMS[form]
    sandbox = _NoExec()
    result = VerificationEngine(sandbox).verify(bundle, _episode(bundle, files))  # type: ignore[arg-type]
    assert sandbox.calls == 0 and result.verdict == Verdict.FAIL
    assert "comparison_dunder_definition" in result.failure_reasons


# ------------------------------------------------------------------ Finding 2: historical versions


def test_historical_1_0_0_bundles_reproduce_pinned_hashes(registry: Any) -> None:
    assert set(FIXTURE_HASHES_1_0_0) == {b.spec.task_id for b in registry.list()}
    for task_id, pinned in FIXTURE_HASHES_1_0_0.items():
        old = registry.bundle(task_id, "1.0.0")
        assert old.oracle.fixture_hash == pinned and old.spec.version == "1.0.0"
        assert old.oracle.verifier_version == "1.0.0" and old.oracle.generator_version == "1.0.0"
        new = registry.bundle(task_id)
        assert new.spec.version == "1.1.0" and new.oracle.verifier_version == "1.1.0"
        assert new.workspace_files == old.workspace_files  # value sampling unchanged
    sql_old = registry.bundle("sqli-t1-s000", "1.0.0").hidden_files["test_hidden.py"]
    sql_new = registry.bundle("sqli-t1-s000", "1.1.0").hidden_files["test_hidden.py"]
    assert "_assert_exact" not in sql_old and " == []" in sql_old  # original raw-== oracle
    assert "_assert_exact" in sql_new


def test_listing_and_selection_use_current_version_only(registry: Any) -> None:
    assert len(registry.list()) == 39 and {b.spec.version for b in registry.list()} == {"1.1.0"}
    assert registry.supported_versions() == {"current": ["1.1.0"], "historical": ["1.0.0"]}


def test_reverify_old_rollout_against_original_version(platform: Any) -> None:
    p = platform
    old_req = RolloutRequest(
        task_id="sqli-t1-s000", task_version="1.0.0", provider=ProviderConfig()
    )
    old = asyncio.run(p.rollouts.run(old_req))
    assert old.task_version == "1.0.0" and old.verification.verdict == Verdict.PASS
    new = asyncio.run(p.rollouts.run(old_req.model_copy(update={"task_version": "1.1.0"})))
    assert new.task_version == "1.1.0"
    v_old, _ = p.rollouts.reverify(old.rollout_id, VerifierProfile.FULL)
    v_new, _ = p.rollouts.reverify(new.rollout_id, VerifierProfile.FULL)
    # oracle version recorded first, engine version second: never a silent substitution
    assert v_old.verifier_version.startswith("1.0.0+engine")
    assert v_new.verifier_version.startswith("1.1.0+engine")
    assert v_old.verdict == Verdict.PASS and v_new.verdict == Verdict.PASS
    assert (
        p.registry.bundle("sqli-t1-s000", "1.0.0").oracle.fixture_hash
        == FIXTURE_HASHES_1_0_0["sqli-t1-s000"]
    )


def test_missing_historical_version_gives_actionable_error(tmp_path: Path) -> None:
    from traceforge.application.container import Platform
    from traceforge.config import Settings

    db = f"sqlite:///{(tmp_path / 'shared.db').as_posix()}"
    full = Platform.build(Settings(env="test", data_dir=tmp_path / "a", database_url=db))
    s = asyncio.run(full.rollouts.run(RolloutRequest(task_id="sqli-t0-s000", task_version="1.0.0")))
    full.close()
    lean = Platform.build(
        Settings(env="test", data_dir=tmp_path / "a", database_url=db),
        registry=default_registry(historical_versions=()),
    )
    try:
        with pytest.raises(UnsupportedTaskVersion) as err:
            lean.rollouts.reverify(s.rollout_id, VerifierProfile.FULL)
        assert err.value.details["requested"] == "1.0.0"
        assert err.value.details["available"] == ["1.1.0"]
        assert "commit recorded" in err.value.message
    finally:
        lean.close()


# ------------------------------------------------------------------ Finding 3: pin at acceptance


def _client(platform: Any) -> Any:
    from fastapi.testclient import TestClient

    from traceforge.api.app import create_app

    return TestClient(create_app(platform=platform))


def _job_request(platform: Any, job_id: str) -> dict[str, Any]:
    import uuid

    from traceforge.infrastructure.database import models as m

    with platform.db.session() as s:
        return dict(s.get(m.Job, uuid.UUID(job_id)).input["request"])


def test_acceptance_pins_versions_and_replay_returns_original(platform: Any) -> None:
    with _client(platform) as c:
        implicit = c.post("/api/v1/rollouts", json={"task_id": "sqli-t0-s000"}).json()
        explicit = c.post(
            "/api/v1/rollouts", json={"task_id": "sqli-t0-s000", "task_version": "1.0.0"}
        ).json()
        first = c.post(
            "/api/v1/rollouts", json={"task_id": "authz-t0-s000"}, headers={"Idempotency-Key": "k1"}
        ).json()
        replay = c.post(
            "/api/v1/rollouts", json={"task_id": "authz-t0-s000"}, headers={"Idempotency-Key": "k1"}
        ).json()
        unknown = c.post(
            "/api/v1/rollouts", json={"task_id": "sqli-t0-s000", "task_version": "9.9.9"}
        )
    assert _job_request(platform, implicit["job_id"])["task_version"] == "1.1.0"
    assert _job_request(platform, explicit["job_id"])["task_version"] == "1.0.0"
    assert replay["job_id"] == first["job_id"]
    assert _job_request(platform, replay["job_id"])["task_version"] == "1.1.0"
    assert unknown.status_code == 409 and unknown.json()["error"]["code"] == "conflict"


def test_queued_job_retried_after_upgrade_runs_original_version(tmp_path: Path) -> None:
    """A job accepted by an old instance (pinned 1.0.0) and retried by an upgraded worker."""
    from traceforge.application.container import Platform
    from traceforge.config import Settings

    db = f"sqlite:///{(tmp_path / 'shared.db').as_posix()}"
    old = Platform.build(
        Settings(env="test", data_dir=tmp_path / "old", database_url=db),
        registry=default_registry(current_version="1.0.0", historical_versions=()),
    )
    with _client(old) as c:
        job = c.post("/api/v1/rollouts", json={"task_id": "ssrf-t0-s000"}).json()
    payload = _job_request(old, job["job_id"])
    old.close()
    assert payload["task_version"] == "1.0.0"
    new = Platform.build(Settings(env="test", data_dir=tmp_path / "new", database_url=db))
    try:  # same stored payload, re-executed by the upgraded worker's handler logic
        summary = asyncio.run(new.rollouts.run(RolloutRequest.model_validate(payload)))
        assert summary.task_version == "1.0.0"
        assert (
            summary.metadata["split"]
            and new.registry.bundle("ssrf-t0-s000").spec.version == "1.1.0"
        )
    finally:
        new.close()


# ------------------------------------------------------------------ Finding 4: mixed versions


def test_old_and_new_instances_sharing_a_database(tmp_path: Path) -> None:
    from traceforge.application.container import Platform
    from traceforge.config import Settings

    db = f"sqlite:///{(tmp_path / 'shared.db').as_posix()}"
    old = Platform.build(
        Settings(env="test", data_dir=tmp_path / "old", database_url=db),
        registry=default_registry(current_version="1.0.0", historical_versions=()),
    )
    new = Platform.build(Settings(env="test", data_dir=tmp_path / "new", database_url=db))
    try:
        assert old.tasks.version_skew() == ["1.1.0"] and new.tasks.version_skew() == []
        with _client(old) as c:
            listed = c.get("/api/v1/tasks", params={"limit": 200}).json()
            assert {i["spec"]["version"] for i in listed["items"]} == {"1.0.0"}
            assert listed["total"] == 39
            assert c.get("/api/v1/tasks/sqli-t0-s000").json()["version"] == "1.0.0"
            r = c.get("/api/v1/tasks/sqli-t0-s000", params={"version": "1.1.0"})
            assert r.status_code == 409 and "not available on this instance" in r.text
            r = c.post(
                "/api/v1/rollouts", json={"task_id": "sqli-t0-s000", "task_version": "1.1.0"}
            )
            assert r.status_code == 409
            ready = c.get("/health/ready").json()["checks"]
            assert ready["task_version_skew"] == ["1.1.0"]
        with _client(new) as c:
            listed = c.get("/api/v1/tasks", params={"limit": 200}).json()
            assert {i["spec"]["version"] for i in listed["items"]} == {"1.1.0"}
            assert (
                c.get("/api/v1/tasks/sqli-t0-s000", params={"version": "1.0.0"}).status_code == 200
            )
        # an old worker handed a 1.1.0 job fails explicitly instead of running 1.0.0
        with pytest.raises(UnsupportedTaskVersion):
            asyncio.run(
                old.rollouts.run(RolloutRequest(task_id="sqli-t0-s000", task_version="1.1.0"))
            )
    finally:
        old.close()
        new.close()


# ------------------------------------------------------------------ Finding 5: concurrent sync


def _race(url: str, bundle: Any, n: int = 8) -> list[Any]:
    from traceforge.infrastructure.database.repositories import TaskRepository
    from traceforge.infrastructure.database.session import Database

    results: list[Any] = [None] * n
    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        db = Database(url)
        try:
            barrier.wait()
            with db.session() as s:
                results[i] = TaskRepository(s).upsert_bundle(bundle, None)
        except Exception as exc:  # recorded and asserted on
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _assert_race_ok(url: str, registry: Any) -> None:
    from traceforge.infrastructure.database import models as m
    from traceforge.infrastructure.database.repositories import TaskRepository
    from traceforge.infrastructure.database.session import Database

    bundle = registry.bundle("authz-t2-s001")
    ids = _race(url, bundle)
    assert all(not isinstance(r, Exception) for r in ids), ids
    assert len(set(ids)) == 1
    db = Database(url)
    with db.session() as s:
        assert s.query(m.Task).filter(m.Task.task_id == "authz-t2-s001").count() == 1
        task = s.query(m.Task).filter(m.Task.task_id == "authz-t2-s001").one()
        assert s.query(m.TaskVersion).filter(m.TaskVersion.task_pk == task.id).count() == 1
    tampered = bundle.model_copy(
        update={"oracle": bundle.oracle.model_copy(update={"fixture_hash": "0" * 64})}
    )
    with pytest.raises(ConflictError, match="immutable"), db.session() as s:
        TaskRepository(s).upsert_bundle(tampered, None)


def test_concurrent_sync_sqlite(tmp_path: Path, registry: Any) -> None:
    from traceforge.infrastructure.database.session import Database

    url = f"sqlite:///{(tmp_path / 'race.db').as_posix()}"
    Database(url).migrate()
    _assert_race_ok(url, registry)


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TRACEFORGE_TEST_POSTGRES_URL"),
    reason="TRACEFORGE_TEST_POSTGRES_URL not set",
)
def test_concurrent_sync_postgres(registry: Any) -> None:
    from traceforge.infrastructure.database.session import Database

    url = os.environ["TRACEFORGE_TEST_POSTGRES_URL"]
    Database(url).migrate()
    _assert_race_ok(url, registry)


# ------------------------------------------------------------------ Finding 6: R2 stays weak


def test_visible_only_baseline_still_fooled_full_profile_rejects(
    registry: Any, sandbox: Any
) -> None:
    from traceforge.application.rollouts import RolloutExecutor

    bundle = registry.bundle("sqli-t1-s000")
    module = str(bundle.spec.public_metadata["module"])
    files = dict(bundle.workspace_files)
    files[module] = universal_equality_source(files[module])
    episode = _episode(bundle, files)
    engine, rewards, policies = VerificationEngine(sandbox), RewardEngine(), PolicyRegistry()
    v2 = engine.verify(bundle, episode, profile=VerifierProfile.VISIBLE_TESTS)
    # documented limitation of the research-only R2 baseline: visible tests are fooled
    assert v2.verdict == Verdict.PASS and v2.authoritative is False
    assert rewards.compute(v2, policies.get("visible-tests-r2")).final_reward >= 0.5
    # ...but it can never become authoritative reward or a verified success
    assert rewards.compute(v2, policies.get("verified-v1")).final_reward == 0.0
    from traceforge.contracts.traces import Trajectory

    t = Trajectory(source_id="x", source_type="synthetic", started_at="2026-11-09T00:00:00Z")
    assert RolloutExecutor.link(t, v2).outcome == Outcome.UNKNOWN
    v3 = VerificationEngine(_NoExec()).verify(bundle, episode, profile=VerifierProfile.FULL)  # type: ignore[arg-type]
    assert v3.verdict == Verdict.FAIL and "comparison_dunder_definition" in v3.failure_reasons


# ------------------------------------------------------------------ Findings 7 and 8: provenance


def test_attack_and_reward_policy_provenance() -> None:
    e = attack_catalog.entry("universal_equality")
    assert e.attack_set == "heldout" and e.known_to_verifier_versions == ("1.1.0",)
    assert e.reward_policies_known == ()  # verified-v1 was not changed in response
    assert "universal_equality" not in attack_catalog.known_at("1.0.0")
    assert VERIFIED_V1.version == "1.0.0"
    for name in attack_catalog.by_set("development"):
        assert attack_catalog.entry(name).reward_policies_known == ("verified-v1",)


def test_verifier_provenance_hash_tracks_static_rules(
    monkeypatch: pytest.MonkeyPatch, platform: Any, tmp_path: Path
) -> None:
    import json

    from traceforge.application.reproducibility import verifier_provenance, write_bundle
    from traceforge.modules.verification import safety

    a, b = verifier_provenance(), verifier_provenance()
    assert a["config_hash"] == b["config_hash"]  # deterministic
    assert a["static_safety"]["forbidden_comparison_dunders"] == sorted(
        safety.FORBIDDEN_COMPARISON_DUNDERS
    )
    assert a["static_safety"]["frame_attrs"] == sorted(safety.FRAME_ATTRS)
    monkeypatch.setattr(safety, "FORBIDDEN_COMPARISON_DUNDERS", frozenset({"__eq__"}))
    assert verifier_provenance()["config_hash"] != a["config_hash"]
    monkeypatch.undo()
    out = write_bundle(platform, tmp_path, experiment="X", document={})
    meta = json.loads((Path(out["dir"]) / "bundle.json").read_text(encoding="utf-8"))
    ver = meta["provenance"]["verifier"]
    # Bundles also record the sandbox identity (SQLite build), which is part of the hash.
    expected = verifier_provenance(platform.sandbox)["config_hash"]
    assert ver["verifier_version"] == "1.1.0" and ver["config_hash"] == expected
    assert ver["sandbox"]["provider"] == platform.sandbox.name
    assert "__eq__" in ver["static_safety"]["forbidden_comparison_dunders"]
