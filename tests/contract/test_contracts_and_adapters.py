"""Contract tests: external schemas and the TRL GRPO environment protocol (no model download)."""

from __future__ import annotations

import inspect

import pytest

from traceforge.contracts.common import CapabilityUnavailable
from traceforge.contracts.experiments import BenchmarkConfig, RolloutSummary
from traceforge.contracts.rewards import RewardBreakdown
from traceforge.contracts.traces import Trajectory
from traceforge.contracts.verification import VerificationResult
from traceforge.modules.agents.providers.fixers import reference_edits
from traceforge.modules.agents.tool_registry import TOOLS
from traceforge.modules.rewards.policies import PolicyRegistry
from traceforge.modules.training.grpo.adapter import (
    EnvironmentDeps,
    TraceForgeToolEnv,
    make_environment_factory,
    rl_prompt_rows,
)
from traceforge.modules.verification.engine import VerificationEngine


@pytest.mark.parametrize(
    "model", [Trajectory, VerificationResult, RewardBreakdown, RolloutSummary, BenchmarkConfig]
)
def test_persisted_contracts_export_json_schema(model) -> None:  # type: ignore[no-untyped-def]
    schema = model.model_json_schema()
    assert schema["type"] == "object" and schema["properties"]


def test_tool_schemas_forbid_unknown_fields() -> None:
    for tool in TOOLS.values():
        schema = tool.spec().parameters
        assert schema.get("additionalProperties") is False, tool.name


def _deps(registry, sandbox, allow=True):  # type: ignore[no-untyped-def]
    return EnvironmentDeps(
        registry=registry,
        sandbox=sandbox,
        verifier=VerificationEngine(sandbox),
        policies=PolicyRegistry(),
        allow_untrusted_local_execution=allow,
    )


def test_environment_matches_trl_protocol(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    env = make_environment_factory(_deps(registry, sandbox))()
    members = dict(inspect.getmembers(type(env), predicate=inspect.isfunction))
    assert "reset" in members and "get_reward" in members
    tools = sorted(n for n in members if n not in ("reset", "get_reward") and not n.startswith("_"))
    assert tools == sorted(
        [
            "list_files",
            "read_file",
            "apply_patch",
            "run_visible_tests",
            "inspect_test_output",
            "submit_evidence",
            "submit_final_report",
        ]
    )
    transformers_utils = pytest.importorskip(
        "transformers.utils",
        reason="TRL tool-schema compatibility requires the optional 'ml' dependencies",
    )
    get_json_schema = transformers_utils.get_json_schema

    for name in tools:  # each tool must produce a valid function schema (TRL renders these)
        schema = get_json_schema(getattr(env, name))
        assert schema["function"]["name"] == name


def test_environment_reward_comes_from_verification(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    deps = _deps(registry, sandbox)
    env = TraceForgeToolEnv(deps)
    prompt = env.reset(task_id="sqli-t0-s000")
    assert prompt and "sqli-t0-s000" in prompt and "test_sec_" not in prompt
    module = str(registry.bundle("sqli-t0-s000").spec.public_metadata["module"])
    import json

    src = json.loads(env.read_file(module))["output"]["content"]
    for e in reference_edits(src):
        assert json.loads(env.apply_patch(module, e.old, e.new))["ok"]
    run = json.loads(env.run_visible_tests())["output"]
    env.submit_evidence("test_run", run["run_id"], "visible tests pass")
    env.submit_final_report("fixed", True)
    reward = env.get_reward()
    assert reward > 0.9 and deps.records[-1].verdict == "PASS"
    # Re-using the pooled instance: reset must give a fresh, unpatched workspace.
    env.reset(task_id="sqli-t0-s000")
    again = json.loads(env.read_file(module))["output"]["content"]
    assert again == registry.bundle("sqli-t0-s000").workspace_files[module]
    env.submit_final_report("claim without fix", True)
    assert env.get_reward() == 0.0
    assert sandbox.live_sandboxes() == []


def test_environment_tool_calls_go_through_router(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    env = TraceForgeToolEnv(_deps(registry, sandbox))
    env.reset(task_id="ssrf-t0-s000")
    import json

    out = json.loads(env.read_file("../hidden/test_hidden.py"))
    assert not out["ok"] and out["error"]["code"] == "path_violation"
    env.get_reward()


def test_untrusted_local_execution_refused(registry, sandbox) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(CapabilityUnavailable):
        TraceForgeToolEnv(_deps(registry, sandbox, allow=False))


def test_rl_prompts_exclude_held_out(registry) -> None:  # type: ignore[no-untyped-def]
    rows = rl_prompt_rows(registry, ("train", "public_test", "private_test"))
    splits = {registry.bundle(r["task_id"]).oracle.split.value for r in rows}
    assert splits <= {"train", "validation"} and rows


def test_grpo_compatibility_check_reports_versions() -> None:
    from traceforge.modules.training.capabilities import grpo_environment_support

    ok, why = grpo_environment_support()
    assert isinstance(ok, bool) and why
