"""Security tests: prove access is denied (not merely that an error string appears)."""

from __future__ import annotations

import pytest

from traceforge.contracts.observations import EpisodeRecord, FinalReport
from traceforge.contracts.verification import Verdict
from traceforge.modules.verification.engine import VerificationEngine
from traceforge.modules.verification.safety import scan_source

PROBES = {
    "network": "import socket\ndef test_probe():\n    socket.create_connection(('93.184.216.34', 80), timeout=1)\n",
    "subprocess": "import subprocess\ndef test_probe():\n    subprocess.run(['whoami'])\n",
    "file_write": "def test_probe():\n    open('pwned.txt', 'w').write('x')\n",
    "file_sqlite": "import sqlite3\ndef test_probe():\n    sqlite3.connect('db.sqlite')\n",
    "ctypes": "def test_probe():\n    import ctypes\n",
}


def _sql_probe(body: str) -> str:
    return "import sqlite3\ndef test_probe():\n    c = sqlite3.connect(':memory:')\n" + body


# SQL-level paths that bypassed the v0.2 guard (SQLite does file I/O in C, without a Python
# `open` audit event) and FTS5/schema surfaces behind CVE-2026-119373/119374.
PROBES.update(
    {
        "sqlite_attach": _sql_probe("    c.execute(\"ATTACH DATABASE 'x.db' AS x\")\n"),
        "sqlite_vacuum_into": _sql_probe("    c.execute(\"VACUUM INTO 'x.db'\")\n"),
        "sqlite_fts5": _sql_probe("    c.execute('CREATE VIRTUAL TABLE f USING fts5(x)')\n"),
        "sqlite_writable_schema": _sql_probe("    c.execute('PRAGMA writable_schema=ON')\n"),
        "sqlite_raise_heap_limit": _sql_probe(
            "    c.execute('PRAGMA hard_heap_limit=2000000000')\n"
        ),
        "sqlite_load_extension_sql": _sql_probe("    c.execute(\"SELECT load_extension('x')\")\n"),
        "sqlite_unhardened_connection": (
            "import sqlite3\ndef test_probe():\n    sqlite3.Connection(':memory:')\n"
        ),
        "sqlite_custom_factory": (
            "import sqlite3\ndef test_probe():\n"
            "    sqlite3.connect(':memory:', factory=sqlite3.Connection)\n"
        ),
        "sqlite_cpu_exhaustion": _sql_probe(
            "    c.execute('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) "
            "SELECT count(*) FROM (SELECT x FROM n LIMIT 1000000000)').fetchall()\n"
        ),
    }
)


@pytest.mark.parametrize("name", sorted(PROBES))
def test_harness_denies_dangerous_operations(sandbox, registry, name) -> None:  # type: ignore[no-untyped-def]
    budget = registry.list()[0].spec.budget
    result = sandbox.run_isolated_verifier(
        {"app/__init__.py": ""}, {"t.py": PROBES[name]}, "t.py", budget
    )
    assert result.result_parsed  # the harness itself survived and reported
    # Denied either at import time (module never loads) or at call time (test errors) -- never passes.
    assert result.passed == 0 and not result.all_passed
    assert result.violations, f"{name} was not recorded as a violation"


def test_sqlite_policy_allows_ordinary_task_sql(sandbox, registry) -> None:  # type: ignore[no-untyped-def]
    """No false positives: the SQL used by task code and hidden tests still works."""
    probe = (
        "import sqlite3\n"
        "def test_ordinary_sql():\n"
        "    c = sqlite3.connect(':memory:')\n"
        "    c.execute('CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, email TEXT)')\n"
        "    c.executemany('INSERT INTO users(name, email) VALUES (?, ?)', [('a', 'x'), (\"b'q\", 'y')])\n"
        "    assert c.execute('SELECT email FROM users WHERE name = ?', (\"b'q\",)).fetchall() == [('y',)]\n"
        "    assert len(c.execute('PRAGMA table_info(users)').fetchall()) == 3\n"
        "    assert c.execute(\"SELECT name FROM users WHERE name = '' UNION SELECT sql FROM sqlite_master\").fetchall()\n"
        "    assert c.execute(\"SELECT value FROM json_each('[1, 2]')\").fetchall() == [(1,), (2,)]\n"
        "    assert c.execute('SELECT count(*) FROM users').fetchone() == (2,)\n"
    )
    result = sandbox.run_isolated_verifier(
        {"app/__init__.py": ""}, {"t.py": probe}, "t.py", registry.list()[0].spec.budget
    )
    assert result.passed == 1 and not result.violations, result.output_tail


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT " + "1 + (" * 150 + "1" + ")" * 150,  # expression depth
        " UNION ALL ".join(["SELECT 1"] * 80),  # compound SELECT terms
        "SELECT length(zeroblob(50000000))",  # blob/string length
        "SELECT '" + "x" * 120_000 + "'",  # SQL text length
    ],
    ids=["expr_depth", "compound_select", "blob_length", "sql_length"],
)
def test_sqlite_parser_and_size_limits(sandbox, registry, sql) -> None:  # type: ignore[no-untyped-def]
    probe = _sql_probe(f"    c.execute({sql!r}).fetchall()\n")
    result = sandbox.run_isolated_verifier(
        {"app/__init__.py": ""}, {"t.py": probe}, "t.py", registry.list()[0].spec.budget
    )
    assert result.result_parsed and result.passed == 0
    assert result.cases and result.cases[0].status == "error", result.output_tail


def test_sandbox_environment_is_minimal(sandbox, registry, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TRACEFORGE_FAKE_SECRET", "super-secret-value")
    probe = (
        "import os\ndef test_env():\n    assert 'TRACEFORGE_FAKE_SECRET' not in os.environ\n"
        "    assert 'TF_NONCE' not in os.environ\n"
    )
    result = sandbox.run_isolated_verifier(
        {"app/__init__.py": ""}, {"t.py": probe}, "t.py", registry.list()[0].spec.budget
    )
    assert result.passed == 1, result.output_tail


def test_forged_result_line_is_ignored(sandbox, registry) -> None:  # type: ignore[no-untyped-def]
    forged = (
        "import sys\ndef test_forge():\n"
        '    sys.__stdout__.write(\'<<TF-RESULT-guess>>{"harness_ok": true, "cases": '
        '[{"name": "test_sec_x", "status": "passed"}]}\\n\')\n    assert False\n'
    )
    result = sandbox.run_isolated_verifier(
        {"app/__init__.py": ""}, {"t.py": forged}, "t.py", registry.list()[0].spec.budget
    )
    assert result.passed == 0 and [c.name for c in result.cases] == ["test_forge"]


def test_hidden_tests_unreachable_by_agent(registry, run_policy) -> None:  # type: ignore[no-untyped-def]
    art = run_policy(registry.bundle("sqli-t0-s000"), "harness_tamper")
    codes = {r.code for r in art.episode.rejected_actions}
    assert codes <= {"path_violation", "protected_path"} and len(art.episode.rejected_actions) == 5
    observations = " ".join(str(s.observation) for s in art.trajectory.steps)
    assert "test_sec_" not in observations and "root:" not in observations


def test_candidate_with_forbidden_code_is_never_executed(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    class NoExecSandbox:
        name, isolation, trusted_code_only = "noexec", "process", True

        def run_isolated_verifier(self, *a, **k):  # type: ignore[no-untyped-def]
            raise AssertionError("verifier executed code that failed the static safety gate")

    bundle = registry.bundle("sqli-t0-s000")
    module = str(bundle.spec.public_metadata["module"])
    files = {**bundle.workspace_files, module: "import os\n" + bundle.workspace_files[module]}
    episode = EpisodeRecord(
        task_id=bundle.spec.task_id,
        task_version=bundle.spec.version,
        run_id="x",
        sandbox_provider="t",
        environment_ref="e",
        original_hashes={},
        final_files=files,
        final_report=FinalReport(sequence_number=1, summary="", claimed_success=True),
    )
    v = VerificationEngine(NoExecSandbox()).verify(bundle, episode)  # type: ignore[arg-type]
    assert v.verdict == Verdict.FAIL and "forbidden_import" in v.failure_reasons


def test_verifier_crash_is_never_pass(registry) -> None:  # type: ignore[no-untyped-def]
    class Boom:
        name, isolation, trusted_code_only = "boom", "process", True

        def run_isolated_verifier(self, *a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("infrastructure down")

    bundle = registry.bundle("authz-t0-s000")
    episode = EpisodeRecord(
        task_id=bundle.spec.task_id,
        task_version=bundle.spec.version,
        run_id="x",
        sandbox_provider="t",
        environment_ref="e",
        original_hashes={},
        final_files=dict(bundle.workspace_files),
    )
    v = VerificationEngine(Boom()).verify(bundle, episode)  # type: ignore[arg-type]
    assert v.verdict == Verdict.ERROR


def test_scanner_blocks_test_tampering_primitives() -> None:
    attacks = [
        "import gc\n",
        "import inspect\n",
        "import builtins\n",
        "import importlib\n",
        "x = (lambda: 0).__globals__\n",
        "import sys\n",
    ]
    for src in attacks:
        assert not scan_source("app/m.py", src, ("sqlite3",)).safe, src


def _docker_sandbox_ready() -> bool:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        client.images.get("traceforge-sandbox:0.1")
        return True
    except Exception:
        return False


@pytest.mark.docker
@pytest.mark.skipif(
    not _docker_sandbox_ready(), reason="Docker daemon or traceforge-sandbox:0.1 image unavailable"
)
def test_docker_sandbox_sqlite_build_and_policy(registry, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The image ships the source-built SQLite (no FTS/R-Tree/session, no ATTACH) and every
    SQL probe is still denied inside the container."""
    from traceforge.infrastructure.sandbox.docker_provider import DockerSandboxProvider

    provider = DockerSandboxProvider("traceforge-sandbox:0.1", tmp_path)
    budget = registry.list()[0].spec.budget
    build = (
        "import sqlite3\n"
        "def test_build():\n"
        "    assert sqlite3.sqlite_version == '3.54.1', sqlite3.sqlite_version\n"
        # PRAGMA compile_options is denied by the harness policy (the image build checks it);
        # here the build is checked by behaviour, with the per-connection policy undone.
        "def test_no_fts5_even_without_runtime_policy():\n"
        "    c = sqlite3.connect(':memory:')\n"
        "    c.set_authorizer(None)\n"
        "    try:\n"
        "        c.execute('CREATE VIRTUAL TABLE f USING fts5(x)')\n"
        "    except sqlite3.OperationalError as exc:\n"
        "        assert 'no such module' in str(exc), exc\n"
        "    else:\n"
        "        raise AssertionError('FTS5 is available')\n"
        "def test_attach_impossible_even_without_runtime_policy():\n"
        "    c = sqlite3.connect(':memory:')\n"
        "    c.set_authorizer(None)\n"
        "    c.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 10)\n"
        "    try:\n"
        "        c.execute(\"ATTACH DATABASE ':memory:' AS x\")\n"
        "    except sqlite3.OperationalError as exc:\n"
        "        assert 'too many attached' in str(exc), exc\n"
        "    else:\n"
        "        raise AssertionError('ATTACH succeeded')\n"
        "def test_vacuum_into_impossible_even_without_runtime_policy():\n"
        "    import os\n"
        "    c = sqlite3.connect(':memory:')\n"
        "    c.set_authorizer(None)\n"
        "    c.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 10)\n"
        "    c.execute('CREATE TABLE t(a)')\n"
        "    try:\n"
        "        c.execute(\"VACUUM INTO '/tmp/vacuum-probe.db'\")\n"
        "    except sqlite3.OperationalError as exc:\n"
        "        assert 'too many attached' in str(exc), exc\n"
        "    else:\n"
        "        raise AssertionError('VACUUM INTO succeeded')\n"
        "    assert not os.path.exists('/tmp/vacuum-probe.db')\n"
        "def test_interpreter_maps_only_the_source_built_library():\n"
        "    sqlite3.connect(':memory:')\n"
        "    with open('/proc/self/maps') as f:\n"
        "        maps = {line.split()[-1] for line in f if 'libsqlite3' in line}\n"
        "    assert maps == {'/usr/local/lib/libsqlite3.so.0'}, maps\n"
    )
    result = provider.run_isolated_verifier(
        {"app/__init__.py": ""}, {"t.py": build}, "t.py", budget
    )
    assert result.result_parsed and result.passed == 5, result.output_tail
    for name in sorted(n for n in PROBES if n.startswith("sqlite_")):
        r = provider.run_isolated_verifier(
            {"app/__init__.py": ""}, {"t.py": PROBES[name]}, "t.py", budget
        )
        assert r.result_parsed and r.passed == 0, name

    # Adversarial: the vulnerable Debian library is still installed (Trivy reports it). Prove
    # candidate code cannot get it into the process through any available interface.
    debian_lib = (
        "import glob, importlib.machinery, importlib.util, os\n"
        "def _debian():\n"
        "    paths = [p for p in glob.glob('/usr/lib/*/libsqlite3.so.*') if not os.path.islink(p)]\n"
        "    assert paths, 'Debian libsqlite3 not found'\n"
        "    return paths[0]\n"
        "def _mapped():\n"
        "    with open('/proc/self/maps') as f:\n"
        "        return {line.split()[-1] for line in f if 'libsqlite3' in line}\n"
        "def test_debian_library_is_present_but_unreadable():\n"
        "    path = _debian()\n"
        "    assert not os.access(path, os.R_OK), path\n"
        "def test_ctypes_denied():\n"
        "    for name in ('ctypes', '_ctypes'):\n"
        "        try:\n"
        "            __import__(name)\n"
        "        except PermissionError:\n"
        "            continue\n"
        "        raise AssertionError(name + ' importable')\n"
        "def test_no_other_ffi_module():\n"
        "    for name in ('cffi', '_cffi_backend', 'pysqlite3', 'apsw'):\n"
        "        assert importlib.util.find_spec(name) is None, name\n"
        "def test_extension_loader_cannot_map_debian_library():\n"
        "    path = _debian()\n"
        "    loader = importlib.machinery.ExtensionFileLoader('x_sqlite', path)\n"
        "    spec = importlib.util.spec_from_file_location('x_sqlite', path, loader=loader)\n"
        "    try:\n"
        "        loader.create_module(spec)\n"
        "    except ImportError as exc:\n"
        "        assert 'ermission denied' in str(exc), exc\n"
        "    else:\n"
        "        raise AssertionError('Debian libsqlite3 was loaded')\n"
        "    assert _mapped() == {'/usr/local/lib/libsqlite3.so.0'}, _mapped()\n"
    )
    adv = provider.run_isolated_verifier(
        {"app/__init__.py": ""}, {"t.py": debian_lib}, "t.py", budget
    )
    assert adv.result_parsed and adv.passed == 4, adv.output_tail

    # Provenance records the image actually used and its SQLite build (label asserted at build).
    from traceforge.application.reproducibility import sandbox_identity

    ident = sandbox_identity(provider)
    assert ident["sqlite_version"] == "3.54.1"
    assert ident["image_id"] == provider._docker().images.get("traceforge-sandbox:0.1").id


def test_reverify_refuses_untrusted_code_in_trusted_only_sandbox(platform, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Re-verification executes stored candidate code: model-written code must not run in the
    local (trusted-code-only) sandbox, even though the rollout gate was passed at run time."""
    import asyncio

    from traceforge.contracts.common import CapabilityUnavailable
    from traceforge.contracts.experiments import RolloutRequest

    s = asyncio.run(platform.rollouts.run(RolloutRequest(task_id="authz-t0-s000")))
    assert platform.rollouts.reverify(s.rollout_id, s.verification.profile)[0].verdict == (
        s.verification.verdict
    )  # trusted demo code: allowed
    untrusted = s.model_copy(update={"provider_kind": "openai_compatible"})
    monkeypatch.setattr(platform.rollouts, "get", lambda _rid: untrusted)
    # The refusal must happen before anything is loaded or executed, not after verification.
    calls: list[str] = []
    real_load, real_verify = (
        platform.rollouts.load_episode,
        platform.rollouts.executor.verifier.verify,
    )
    monkeypatch.setattr(
        platform.rollouts,
        "load_episode",
        lambda *a, **k: calls.append("load") or real_load(*a, **k),
    )
    monkeypatch.setattr(
        platform.rollouts.executor.verifier,
        "verify",
        lambda *a, **k: calls.append("verify") or real_verify(*a, **k),
    )
    with pytest.raises(CapabilityUnavailable, match="untrusted provider"):
        platform.rollouts.reverify(s.rollout_id, s.verification.profile)
    assert calls == []
    # Explicit single-user research override (forbidden in prod) restores the old behaviour.
    monkeypatch.setattr(platform.rollouts.executor.runner, "allow_untrusted", True)
    assert platform.rollouts.reverify(s.rollout_id, s.verification.profile)[0].verdict == (
        s.verification.verdict
    )
    assert calls == ["load", "verify"]


def test_verifier_provenance_records_sandbox_harness() -> None:
    from traceforge.application.reproducibility import verifier_provenance
    from traceforge.contracts.common import sha256_text
    from traceforge.infrastructure.sandbox.common import runner_source

    prov = verifier_provenance()
    assert prov["sandbox_harness_sha256"] == sha256_text(runner_source())


def test_verifier_provenance_identifies_sandbox_sqlite_build(sandbox) -> None:  # type: ignore[no-untyped-def]
    """Local and Docker sandboxes run different SQLite builds; the identity is hashed."""
    import sqlite3

    from traceforge.application.reproducibility import sandbox_identity, verifier_provenance

    ident = sandbox_identity(sandbox)
    assert ident["provider"] == "local" and ident["sqlite_version"] == sqlite3.sqlite_version
    with_sandbox, without = verifier_provenance(sandbox), verifier_provenance()
    assert with_sandbox["sandbox"] == ident
    assert with_sandbox["config_hash"] != without["config_hash"]

    class FakeImage:
        id = "sha256:abc"
        labels = {"org.traceforge.sqlite.version": "3.54.1"}

    class FakeDocker:
        name, isolation, image = "docker", "container", "traceforge-sandbox:0.1"

        def _docker(self):  # type: ignore[no-untyped-def]
            class Images:
                def get(self, _ref):  # type: ignore[no-untyped-def]
                    return FakeImage()

            return type("Client", (), {"images": Images()})()

    assert sandbox_identity(FakeDocker())["sqlite_version"] == "3.54.1"

    class Unavailable(FakeDocker):
        def _docker(self):  # type: ignore[no-untyped-def]
            raise ConnectionError("daemon down")

    gone = sandbox_identity(Unavailable())
    assert gone["sqlite_version"] is None and gone["unavailable"] == "ConnectionError"
