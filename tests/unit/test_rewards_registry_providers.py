from __future__ import annotations

import itertools
import json
import uuid

import httpx
import pytest
import respx

from traceforge.contracts.common import CapabilityUnavailable, ConflictError
from traceforge.contracts.verification import (
    CheckCategory,
    CheckStatus,
    Verdict,
    VerificationCheck,
    VerificationResult,
    VerifierProfile,
)
from traceforge.domain.ports import ChatMessage, GenerationConfig, ToolSpec
from traceforge.infrastructure.sandbox.docker_provider import (
    DockerSandboxProvider,
    container_kwargs,
)
from traceforge.modules.agents.providers.openai_compatible import (
    OpenAICompatibleProvider,
    ProviderError,
)
from traceforge.modules.rewards.engine import RewardEngine
from traceforge.modules.rewards.policies import PolicyRegistry
from traceforge.modules.tasks.registry import (
    FAMILIES,
    TaskRegistry,
    build_bundle,
    validate_registry,
)

POL = PolicyRegistry()


def vr(
    verdict: Verdict = Verdict.PASS,
    statuses: dict[str, CheckStatus] | None = None,
    authoritative: bool = True,
    tool_calls: int = 5,
) -> VerificationResult:
    names = {
        "state": CheckCategory.STATE,
        "static_safety": CheckCategory.SAFETY,
        "hidden_security": CheckCategory.EXECUTION,
        "regression": CheckCategory.REGRESSION,
        "behaviour_safety": CheckCategory.SAFETY,
        "evidence_integrity": CheckCategory.EVIDENCE,
        "evidence_quality": CheckCategory.EVIDENCE,
    }
    statuses = statuses or {}
    checks = tuple(
        VerificationCheck(
            name=n,
            category=c,
            status=statuses.get(n, CheckStatus.PASS),
            reason_code="x",
            metrics={"pass_fraction": 1.0, "score": 1.0},
        )
        for n, c in names.items()
    )
    return VerificationResult(
        task_id="t",
        task_version="1.0.0",
        verdict=verdict,
        verifier_id="v",
        verifier_version="1",
        config_hash="h",
        checks=checks,
        authoritative=authoritative,
        profile=VerifierProfile.FULL if authoritative else VerifierProfile.VISIBLE_TESTS,
        stats={"tool_calls": tool_calls, "max_actions": 20},
    )


def test_reward_full_pass_and_breakdown() -> None:
    r = RewardEngine().compute(vr(), POL.get("verified-v1"))
    assert r.final_reward == pytest.approx(0.7 + 0.1 + 0.1 + 0.1 * 0.75)
    assert all(g.passed for g in r.gates) and len(r.components) == 4
    assert r.policy_hash == POL.get("verified-v1").policy_hash


@pytest.mark.parametrize(
    "gate_check", ["static_safety", "behaviour_safety", "regression", "evidence_integrity"]
)
def test_hard_gates_zero_reward(gate_check: str) -> None:
    r = RewardEngine().compute(vr(statuses={gate_check: CheckStatus.FAIL}), POL.get("verified-v1"))
    assert r.final_reward == 0.0


def test_non_pass_verdicts_and_non_authoritative_zero() -> None:
    for verdict in (Verdict.FAIL, Verdict.PARTIAL, Verdict.ERROR, Verdict.INCONCLUSIVE):
        assert RewardEngine().compute(vr(verdict), POL.get("verified-v1")).final_reward == 0.0
    assert (
        RewardEngine().compute(vr(authoritative=False), POL.get("verified-v1")).final_reward == 0.0
    )


def test_missing_check_fails_closed() -> None:
    v = vr()
    v = v.model_copy(update={"checks": tuple(c for c in v.checks if c.name != "behaviour_safety")})
    assert RewardEngine().compute(v, POL.get("verified-v1")).final_reward == 0.0


def test_reward_deterministic_and_repetition_never_helps() -> None:
    v = vr()
    a, b = (
        RewardEngine().compute(v, POL.get("verified-v1")),
        RewardEngine().compute(v, POL.get("verified-v1")),
    )
    assert a.deterministic_view() == b.deterministic_view()
    rewards = [
        RewardEngine().compute(vr(tool_calls=n), POL.get("verified-v1")).final_reward
        for n in range(1, 40)
    ]
    assert all(x >= y for x, y in itertools.pairwise(rewards))


def test_policy_registry_immutability() -> None:
    reg = PolicyRegistry()
    changed = POL.get("verified-v1").model_copy(update={"description": "changed"})
    with pytest.raises(ValueError, match="immutable"):
        reg.add(changed)
    assert POL.get("self-report-r1").research_only and POL.get("visible-tests-r2").research_only


# ----------------------------------------------------------------------------- registry


def test_generation_deterministic_and_varied() -> None:
    fam = FAMILIES["sql_injection"]
    a, b = build_bundle(fam, "t0", 3), build_bundle(fam, "t0", 3)
    assert a == b
    assert build_bundle(fam, "t0", 4).oracle.fixture_hash != a.oracle.fixture_hash


def test_registry_immutability_and_validation(registry: TaskRegistry) -> None:
    assert len(registry) == 39 and validate_registry(registry) == []
    bundle = registry.list()[0]
    tampered = bundle.model_copy(
        update={"oracle": bundle.oracle.model_copy(update={"fixture_hash": "0" * 64})}
    )
    with pytest.raises(ConflictError):
        registry.register(tampered)


def test_hidden_material_absent_from_workspace(registry: TaskRegistry) -> None:
    for b in registry.list():
        blob = json.dumps(b.workspace_files) + b.spec.model_dump_json()
        assert "test_sec_" not in blob and "test_hidden" not in blob
        assert b.hidden_files[b.oracle.hidden_test_module] not in blob


# ----------------------------------------------------------------------------- docker config (no daemon needed)


def test_docker_container_security_flags(registry: TaskRegistry) -> None:
    kw = container_kwargs(
        "img@sha256:" + "a" * 64,
        registry.list()[0].spec.budget,
        {"/w": {"bind": "/workspace", "mode": "ro"}},
        ["python"],
        "n",
        {},
    )
    assert kw["network_mode"] == "none" and kw["read_only"] is True and kw["cap_drop"] == ["ALL"]
    assert (
        "no-new-privileges:true" in kw["security_opt"]
        and kw["user"] != "0"
        and not kw["privileged"]
    )
    assert kw["pids_limit"] > 0 and kw["mem_limit"].endswith("m") and kw["nano_cpus"] > 0
    assert all(v["mode"] == "ro" for v in kw["volumes"].values())
    assert "/var/run/docker.sock" not in json.dumps(kw)


def test_docker_unavailable_reports_reason(tmp_path) -> None:  # type: ignore[no-untyped-def]
    class Down:
        def ping(self) -> None:
            raise ConnectionError("no daemon")

    ok, why = DockerSandboxProvider("img", tmp_path, client=Down()).available()
    assert not ok and "not reachable" in why


# ----------------------------------------------------------------------------- openai-compatible provider


MSGS = [ChatMessage(role="user", content="hi")]
TOOLS = [ToolSpec(name="read_file", description="d", parameters={"type": "object"})]


@respx.mock
async def test_openai_provider_parses_tool_calls_and_retries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TF_TEST_KEY", "sk-test")
    route = respx.post("http://llm.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": '{"path":"TASK.md"}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            ),
        ]
    )
    p = OpenAICompatibleProvider(
        base_url="http://llm.test/v1", model_id="m", api_key_env="TF_TEST_KEY"
    )
    gen = await p.generate(MSGS, TOOLS, GenerationConfig(seed=1))
    await p.aclose()
    assert route.call_count == 2
    assert (
        gen.tool_calls[0].name == "read_file"
        and gen.tool_calls[0].arguments == '{"path":"TASK.md"}'
    )
    assert gen.prompt_tokens == 10
    sent = json.loads(route.calls[-1].request.content)
    assert sent["tools"][0]["function"]["name"] == "read_file" and sent["seed"] == 1
    assert route.calls[-1].request.headers["Authorization"] == "Bearer sk-test"


@respx.mock
async def test_openai_provider_non_retryable_error() -> None:
    route = respx.post("http://llm.test/v1/chat/completions").mock(return_value=httpx.Response(400))
    p = OpenAICompatibleProvider(base_url="http://llm.test/v1", model_id="m")
    with pytest.raises(ProviderError):
        await p.generate(MSGS, TOOLS, GenerationConfig())
    await p.aclose()
    assert route.call_count == 1


def test_openai_provider_missing_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("TF_MISSING", raising=False)
    with pytest.raises(CapabilityUnavailable):
        OpenAICompatibleProvider(base_url="http://x", model_id="m", api_key_env="TF_MISSING")


def test_unknown_policy_raises() -> None:
    from traceforge.modules.agents.providers.demo import ScriptedDemoProvider

    with pytest.raises(ValueError):
        ScriptedDemoProvider("nope")
    assert uuid.UUID(int=0)
