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
