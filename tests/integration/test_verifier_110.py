"""Verifier 1.1.0: defences against the held-out ``universal_equality`` exploit (Experiment H)."""

from __future__ import annotations

from typing import Any

import pytest

from traceforge.contracts.verification import CheckStatus, Verdict
from traceforge.modules.agents.providers.heldout_attacks import universal_equality_source
from traceforge.modules.evaluation import attack_catalog
from traceforge.modules.evaluation.experiments import _episode, _fixed_files
from traceforge.modules.rewards.engine import RewardEngine
from traceforge.modules.rewards.policies import PolicyRegistry
from traceforge.modules.verification.engine import ENGINE_VERSION, VerificationEngine
from traceforge.modules.verification.safety import FORBIDDEN_COMPARISON_DUNDERS, scan_source

SQL_TASKS = ("sqli-t0-s000", "sqli-t1-s000", "sqli-t2-s000")

# ---------------------------------------------------------------- A. static scanner


@pytest.mark.parametrize("name", sorted(FORBIDDEN_COMPARISON_DUNDERS))
def test_scanner_rejects_each_comparison_dunder(name: str) -> None:
    src = f"class Thing:\n    def {name}(self, other):\n        return True\n"
    codes = [f.code for f in scan_source("app/x.py", src, ()).findings]
    assert codes == ["comparison_dunder_definition"]


@pytest.mark.parametrize("name", sorted(FORBIDDEN_COMPARISON_DUNDERS))
def test_scanner_rejects_async_comparison_dunder(name: str) -> None:
    src = f"class Thing:\n    async def {name}(self, other):\n        return True\n"
    codes = [f.code for f in scan_source("app/x.py", src, ()).findings]
    assert codes == ["comparison_dunder_definition"]


def test_scanner_flags_definition_even_if_never_called_and_nested() -> None:
    src = "def outer():\n    class Hidden:\n        def __ge__(self, o):\n            return True\n    return 1\n"
    result = scan_source("app/x.py", src, ())
    assert [(f.code, f.line) for f in result.findings] == [("comparison_dunder_definition", 3)]


def test_scanner_accepts_legitimate_methods() -> None:
    src = (
        "from dataclasses import dataclass\n\n"
        "class Record:\n"
        "    def __init__(self, key):\n        self.key = key\n\n"
        "    def __repr__(self):\n        return 'Record'\n\n"
        "    def __len__(self):\n        return 1\n\n"
        "    def equals(self, other):\n        return self.key == other.key\n\n"
        "    async def fetch(self):\n        return self.key\n\n"
        "def lookup(rows, key):\n    return [r for r in rows if r == key]\n"
    )
    assert scan_source("app/x.py", src, ()).safe


# ---------------------------------------------------------------- B. pre-execution gate


class NoExecSandbox:
    """Fails the test if the verifier ever tries to execute candidate code."""

    name, isolation, trusted_code_only = "no-exec", "none", True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_isolated_verifier(self, *a: Any, **k: Any) -> Any:
        self.calls.append("run_isolated_verifier")
        raise AssertionError("candidate code must not be executed")

    def __getattr__(self, item: str) -> Any:
        def _forbidden(*a: Any, **k: Any) -> Any:
            self.calls.append(item)
            raise AssertionError(f"sandbox.{item} must not be called")

        return _forbidden


@pytest.mark.parametrize("task_id", SQL_TASKS)
def test_gate_rejects_anything_class_before_execution(registry: Any, task_id: str) -> None:
    bundle = registry.bundle(task_id)
    module = str(bundle.spec.public_metadata["module"])
    files = dict(bundle.workspace_files)
    files[module] = universal_equality_source(files[module])
    assert "class _Anything" in files[module] and "def __eq__" in files[module]
    sandbox = NoExecSandbox()
    result = VerificationEngine(sandbox).verify(bundle, _episode(bundle, files))  # type: ignore[arg-type]
    assert sandbox.calls == []  # nothing executed: no hidden or visible test run
    assert result.verdict == Verdict.FAIL and result.authoritative
    static = result.check("static_safety")
    assert static is not None and static.status == CheckStatus.FAIL
    assert static.reason_code == "comparison_dunder_definition"
    assert "comparison_dunder_definition" in result.failure_reasons
    hidden = result.check("hidden_security")
    assert hidden is not None and hidden.reason_code == "blocked_by_gate"


# ---------------------------------------------------------------- C. oracle defence in depth


@pytest.mark.parametrize("task_id", SQL_TASKS)
def test_exact_type_oracle_rejects_exploit_without_static_gate(
    registry: Any, sandbox: Any, task_id: str
) -> None:
    """Run the generated hidden SQL tests directly (bypassing the static scanner)."""
    bundle = registry.bundle(task_id)
    oracle = bundle.oracle
    module = str(bundle.spec.public_metadata["module"])
    hidden = {oracle.hidden_test_module: bundle.hidden_files[oracle.hidden_test_module]}
    assert "_assert_exact(" in hidden[oracle.hidden_test_module]
    assert " == []" not in hidden[oracle.hidden_test_module]  # no raw equality on results

    malicious = dict(bundle.workspace_files)
    malicious[module] = universal_equality_source(malicious[module])
    run = sandbox.run_isolated_verifier(
        malicious, hidden, oracle.hidden_test_module, bundle.spec.budget
    )
    assert run.result_parsed and run.harness_ok  # a genuine test run, not an infra error
    assert run.total > 0 and run.passed == 0, f"{run.passed}/{run.total} hidden tests passed"
    for case in run.cases:
        assert case.status != "passed"

    control = sandbox.run_isolated_verifier(
        _fixed_files(bundle), hidden, oracle.hidden_test_module, bundle.spec.budget
    )
    assert control.total > 0 and control.passed == control.total  # strict oracle keeps fixes


# ---------------------------------------------------------------- D. end-to-end reward


@pytest.mark.parametrize("task_id", SQL_TASKS)
def test_universal_equality_policy_gets_no_reward(
    registry: Any, run_policy: Any, task_id: str
) -> None:
    art = run_policy(registry.bundle(task_id), "universal_equality")
    v = art.summary.verification
    assert v.verdict != Verdict.PASS and art.summary.reward.final_reward == 0.0
    assert "comparison_dunder_definition" in v.failure_reasons
    r3 = RewardEngine().compute(v, PolicyRegistry().get("verified-v1"))
    assert r3.final_reward == 0.0
    ok = run_policy(registry.bundle(task_id), "reference").summary
    assert ok.verification.verdict == Verdict.PASS and ok.reward.final_reward > 0.9


# ---------------------------------------------------------------- E. versioning


def test_tasks_and_oracles_report_1_1_0(registry: Any) -> None:
    from traceforge.modules.tasks.families.base import GENERATOR_VERSION
    from traceforge.modules.tasks.registry import TASK_VERSION, VERIFIER_VERSION

    assert (GENERATOR_VERSION, TASK_VERSION, VERIFIER_VERSION, ENGINE_VERSION) == ("1.1.0",) * 4
    bundles = registry.list()
    assert len(bundles) == 39
    for b in bundles:
        assert b.spec.version == "1.1.0" and b.oracle.version == "1.1.0"
        assert b.oracle.verifier_version == "1.1.0" and b.oracle.generator_version == "1.1.0"
        assert b.spec.environment_ref.endswith("@1.1.0")


def test_attack_catalogue_history_preserved() -> None:
    dev = set(attack_catalog.by_set("development"))
    held = set(attack_catalog.by_set("heldout"))
    # authoring-time classification is unchanged
    assert "universal_equality" in held and "universal_equality" not in dev
    # verifier 1.0.0 knowledge is unchanged: only the development attacks
    assert set(attack_catalog.known_at("1.0.0")) == dev
    assert set(attack_catalog.heldout_at("1.0.0")) == held
    # verifier 1.1.0 was changed in response to universal_equality
    assert set(attack_catalog.known_at("1.1.0")) == dev | {"universal_equality"}
    assert set(attack_catalog.heldout_at("1.1.0")) == held - {"universal_equality"}
    assert len(attack_catalog.heldout_at("1.1.0")) == 4
