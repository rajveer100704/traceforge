"""PR 1A: provider pricing propagation and generation telemetry integrity.

Unknown values must stay unknown (None / not reported), never become zero; provider failures
are recorded as provider failures (not verifier outcomes); a configured cost budget fails closed
when cost cannot be measured; existing benchmark config hashes are unchanged.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
import respx
import yaml

from traceforge.application.rollouts import generation_stats
from traceforge.contracts.common import CapabilityUnavailable
from traceforge.contracts.experiments import BenchmarkConfig, ProviderConfig
from traceforge.domain.ports import (
    ChatMessage,
    GeneratedToolCall,
    Generation,
    GenerationConfig,
    ToolSpec,
)
from traceforge.modules.agents.budget import BudgetManager
from traceforge.modules.agents.providers.demo import ScriptedDemoProvider
from traceforge.modules.agents.providers.factory import build_provider
from traceforge.modules.agents.providers.openai_compatible import (
    OpenAICompatibleProvider,
    ProviderCallFailed,
    ProviderError,
)
from traceforge.modules.agents.runner import AgentRunner

URL = "http://llm.test/v1"
MSGS = [ChatMessage(role="user", content="hi")]
TOOLS = [ToolSpec(name="read_file", description="d", parameters={"type": "object"})]


def _ok(usage: dict[str, Any] | None = None, finish: str | None = "stop") -> httpx.Response:
    choice: dict[str, Any] = {"message": {"content": "done"}}
    if finish is not None:
        choice["finish_reason"] = finish
    body: dict[str, Any] = {"choices": [choice]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def _provider(**kw: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(base_url=URL, model_id="m", **kw)


async def _generate(p: OpenAICompatibleProvider) -> Generation:
    try:
        return await p.generate(MSGS, TOOLS, GenerationConfig(timeout_seconds=5))
    finally:
        await p.aclose()


# ------------------------------------------------------------------ config and pricing propagation


def test_pricing_propagates_from_config_through_factory() -> None:
    cfg = ProviderConfig(
        kind="openai_compatible",
        base_url=URL,
        model_id="m",
        price_per_1k_prompt=0.5,
        price_per_1k_completion=1.5,
    )
    p = build_provider(cfg)
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.pricing_known and cfg.pricing_known
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(
            return_value=_ok({"prompt_tokens": 1000, "completion_tokens": 2000})
        )
        gen = asyncio.run(_generate(p))
    assert gen.usage_reported and gen.cost_usd == pytest.approx(0.5 + 3.0)


def test_unknown_pricing_is_none_not_zero() -> None:
    cfg = ProviderConfig(kind="openai_compatible", base_url=URL, model_id="m")
    p = build_provider(cfg)
    assert isinstance(p, OpenAICompatibleProvider)
    assert not cfg.pricing_known and not p.pricing_known
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(
            return_value=_ok({"prompt_tokens": 10, "completion_tokens": 5})
        )
        gen = asyncio.run(_generate(p))
    assert gen.usage_reported and gen.prompt_tokens == 10 and gen.cost_usd is None


def test_explicitly_free_pricing_is_zero() -> None:
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(
            return_value=_ok({"prompt_tokens": 10, "completion_tokens": 5})
        )
        gen = asyncio.run(
            _generate(_provider(price_per_1k_prompt=0.0, price_per_1k_completion=0.0))
        )
    assert gen.cost_usd == 0.0


def test_partial_pricing_is_rejected() -> None:
    with pytest.raises(ValueError, match="set both"):
        ProviderConfig(kind="openai_compatible", base_url=URL, price_per_1k_prompt=1.0)
    with pytest.raises(ValueError):
        ProviderConfig(price_per_1k_prompt=-1.0, price_per_1k_completion=0.0)


@pytest.mark.parametrize(
    "bad", [float("inf"), float("-inf"), float("nan")], ids=["inf", "-inf", "nan"]
)
def test_non_finite_config_prices_are_rejected(bad: float) -> None:
    """Review #2: an infinite or NaN price could yield a NaN cost that never trips a budget."""
    with pytest.raises(ValueError):
        ProviderConfig(price_per_1k_prompt=bad, price_per_1k_completion=0.0)
    with pytest.raises(ValueError):
        ProviderConfig(price_per_1k_prompt=0.0, price_per_1k_completion=bad)


@pytest.mark.parametrize(
    ("prompt", "completion"),
    [
        (-1.0, 0.0),
        (0.0, -0.01),
        (float("inf"), 0.0),
        (0.0, float("nan")),
        (1.0, None),
        (None, 1.0),
        (True, 1.0),
    ],
    ids=[
        "negative-prompt",
        "negative-completion",
        "inf",
        "nan",
        "prompt-only",
        "completion-only",
        "bool",
    ],
)
def test_direct_provider_construction_validates_prices(prompt: Any, completion: Any) -> None:
    """Review #8: the constructor enforces the same rules as ProviderConfig."""
    with pytest.raises(ValueError):
        _provider(price_per_1k_prompt=prompt, price_per_1k_completion=completion)


def test_direct_provider_construction_accepts_valid_prices() -> None:
    assert _provider(price_per_1k_prompt=0.0, price_per_1k_completion=0.0).pricing_known
    assert _provider(price_per_1k_prompt=0.5, price_per_1k_completion=1.5).pricing_known
    assert not _provider().pricing_known


def test_provider_reported_cost_is_authoritative() -> None:
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(
            return_value=_ok({"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0123})
        )
        gen = asyncio.run(
            _generate(_provider(price_per_1k_prompt=9.0, price_per_1k_completion=9.0))
        )
    assert gen.cost_usd == pytest.approx(0.0123)


def test_absent_usage_is_not_reported_and_cost_unknown() -> None:
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(return_value=_ok(None))
        gen = asyncio.run(
            _generate(_provider(price_per_1k_prompt=1.0, price_per_1k_completion=1.0))
        )
    assert not gen.usage_reported and gen.prompt_tokens == 0 and gen.cost_usd is None


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": 10},
        {"completion_tokens": 7},
        {"prompt_tokens": 10, "completion_tokens": None},
        {},
    ],
    ids=["missing-completion", "missing-prompt", "null-completion", "empty-usage"],
)
def test_partial_usage_is_not_reported_and_both_counts_are_discarded(
    usage: dict[str, Any],
) -> None:
    """Review #16: a partial report is discarded entirely (both counts 0), never half-used."""
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(return_value=_ok(usage))
        gen = asyncio.run(
            _generate(_provider(price_per_1k_prompt=1.0, price_per_1k_completion=1.0))
        )
    assert not gen.usage_reported and gen.cost_usd is None
    assert gen.prompt_tokens == 0 and gen.completion_tokens == 0


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": "10", "completion_tokens": 5},
        {"prompt_tokens": 10.0, "completion_tokens": 5},
        {"prompt_tokens": True, "completion_tokens": 5},
        {"prompt_tokens": -1, "completion_tokens": 5},
        {"prompt_tokens": 10, "completion_tokens": 10**9 + 1},
        {"prompt_tokens": 10**400, "completion_tokens": 5},
        ["not", "an", "object"],
    ],
    ids=["string", "float", "bool", "negative", "over-cap", "overflow", "usage-not-object"],
)
def test_invalid_usage_values_are_malformed_responses(usage: Any) -> None:
    """Review #10: present-but-invalid or overflowing counts are classified, never a crash."""
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": "x"}}], "usage": usage}
            )
        )
        with pytest.raises(ProviderCallFailed) as err:
            asyncio.run(_generate(_provider(price_per_1k_prompt=1.0, price_per_1k_completion=1.0)))
    assert err.value.details["failure"] == "malformed_response"


@pytest.mark.parametrize(
    "cost",
    ["0.01", -0.5, 10**400, True],
    ids=["string", "negative", "overflow-int", "bool"],
)
def test_invalid_reported_cost_makes_cost_unknown(cost: Any) -> None:
    """Review #11: an invalid usage.cost is handled safely: the call succeeds, the cost is
    unknown (no fallback to a price estimate the provider contradicted), never zero."""
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(
            return_value=_ok({"prompt_tokens": 10, "completion_tokens": 5, "cost": cost})
        )
        gen = asyncio.run(
            _generate(_provider(price_per_1k_prompt=1.0, price_per_1k_completion=1.0))
        )
    assert gen.usage_reported and gen.prompt_tokens == 10 and gen.cost_usd is None


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
def test_non_finite_reported_cost_makes_cost_unknown(literal: str) -> None:
    """Review #11: non-standard JSON literals (accepted by Python's parser) in usage.cost."""
    body = (
        '{"choices": [{"message": {"content": "x"}}], '
        f'"usage": {{"prompt_tokens": 10, "completion_tokens": 5, "cost": {literal}}}}}'
    )
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, content=body.encode(), headers={"content-type": "application/json"}
            )
        )
        gen = asyncio.run(
            _generate(_provider(price_per_1k_prompt=1.0, price_per_1k_completion=1.0))
        )
    assert gen.usage_reported and gen.cost_usd is None


def test_finish_reason_absent_is_none_and_length_is_kept() -> None:
    with respx.mock:
        route = respx.post(f"{URL}/chat/completions")
        route.side_effect = [_ok(None, finish=None), _ok(None, finish="length")]
        p = _provider()
        a = asyncio.run(p.generate(MSGS, TOOLS, GenerationConfig()))
        b = asyncio.run(p.generate(MSGS, TOOLS, GenerationConfig()))
        asyncio.run(p.aclose())
    assert a.finish_reason is None and b.finish_reason == "length"


# ------------------------------------------------------------------ retries and classified failures


def test_retry_then_success_records_attempts_duration_and_unknown_cost() -> None:
    """Review #15: with an injected, deterministic 0.3 s retry delay the recorded duration must
    include it (a regression measuring only the final attempt would fail). Review #3: after a
    retry the total cost is unknown, even with known prices and reported usage."""
    from tenacity import wait_fixed

    with respx.mock:
        route = respx.post(f"{URL}/chat/completions")
        route.side_effect = [httpx.Response(429), _ok({"prompt_tokens": 1, "completion_tokens": 1})]
        gen = asyncio.run(
            _generate(
                _provider(
                    max_attempts=3,
                    retry_wait=wait_fixed(0.3),
                    price_per_1k_prompt=1.0,
                    price_per_1k_completion=1.0,
                )
            )
        )
    assert route.call_count == 2 and gen.attempts == 2
    assert gen.request_seconds is not None and gen.request_seconds >= 0.3
    assert gen.usage_reported and gen.prompt_tokens == 1  # this response's usage is still kept
    assert gen.cost_usd is None  # earlier attempt may have been billed


def test_single_attempt_keeps_known_cost() -> None:
    """Counterpart to review #3: without a retry, the known cost is kept."""
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(
            return_value=_ok({"prompt_tokens": 1000, "completion_tokens": 0})
        )
        gen = asyncio.run(
            _generate(_provider(price_per_1k_prompt=0.5, price_per_1k_completion=0.5))
        )
    assert gen.attempts == 1 and gen.cost_usd == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("responses", "kind", "status", "attempts"),
    [
        ([httpx.Response(429), httpx.Response(429)], "rate_limited", 429, 2),
        ([httpx.Response(503), httpx.Response(503)], "server_error", 503, 2),
        ([httpx.Response(400)], "http_error", 400, 1),
        ([httpx.Response(200, text="not json")], "malformed_response", 200, 1),
        ([httpx.Response(200, json={"choices": []})], "malformed_response", 200, 1),
        ([httpx.Response(200, json={"choices": [{"message": "x"}]})], "malformed_response", 200, 1),
        (
            [httpx.Response(200, json={"choices": [{"message": {"tool_calls": ["x"]}}]})],
            "malformed_response",
            200,
            1,
        ),
        ([httpx.Response(200, json=["not", "an", "object"])], "malformed_response", 200, 1),
        ([httpx.Response(200, json={"choices": [{}]})], "malformed_response", 200, 1),
        (
            [httpx.Response(200, json={"choices": [{"message": None}]})],
            "malformed_response",
            200,
            1,
        ),
    ],
    ids=[
        "429-exhausted",
        "503-exhausted",
        "400",
        "non-json",
        "no-choices",
        "message-not-object",
        "tool-call-not-object",
        "body-is-list",
        "message-missing",
        "message-null",
    ],
)
def test_failures_are_classified(
    responses: list[httpx.Response], kind: str, status: int, attempts: int
) -> None:
    with respx.mock:
        respx.post(f"{URL}/chat/completions").side_effect = responses
        with pytest.raises(ProviderCallFailed) as err:
            asyncio.run(_generate(_provider(max_attempts=2)))
    d = err.value.details
    assert (d["failure"], d["status"], d["attempts"]) == (kind, status, attempts)
    assert d["seconds"] >= 0
    assert isinstance(
        err.value, ProviderError
    )  # existing callers catching ProviderError still work


def test_transport_timeout_is_classified_as_timeout() -> None:
    with respx.mock:
        respx.post(f"{URL}/chat/completions").side_effect = httpx.ReadTimeout("slow")
        with pytest.raises(ProviderCallFailed) as err:
            asyncio.run(_generate(_provider(max_attempts=2)))
    assert err.value.details["failure"] == "timeout" and err.value.details["attempts"] == 2


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (httpx.ConnectError("refused"), "transport_error"),
        (httpx.TooManyRedirects("loop"), "transport_error"),
        (httpx.DecodingError("bad gzip"), "malformed_response"),
    ],
    ids=["connect-error", "too-many-redirects", "decoding-error"],
)
def test_request_errors_are_classified(error: Exception, kind: str) -> None:
    with respx.mock:
        respx.post(f"{URL}/chat/completions").side_effect = error
        with pytest.raises(ProviderCallFailed) as err:
            asyncio.run(_generate(_provider(max_attempts=1)))
    assert err.value.details["failure"] == kind and err.value.details["attempts"] == 1


# ------------------------------------------------------------------ benchmark config hash stability

# Computed on main 67a6fae (before v0.3 provider fields existed).
GOLDEN_HASHES = {
    "default": "21893a6296afd3057de7d70360a39934b956980e77f1ab8c21517c5a70ada85d",
    "openai": "06a43333b0cd8d8f777121b0d31d13a3386bc2d80e09e71cfa90a30c4ce50972",
    "seeds": "025daf5a147ec2f84fb392b0098c0326656af8a7bfdd9594ffb35dd50f1f9662",
}


def test_existing_benchmark_config_hashes_are_unchanged() -> None:
    from pathlib import Path

    openai_cfg = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "configs" / "benchmark_openai.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert BenchmarkConfig().config_hash == GOLDEN_HASHES["default"]
    assert BenchmarkConfig.model_validate(openai_cfg).config_hash == GOLDEN_HASHES["openai"]
    seeded = BenchmarkConfig(seeds=(0, 1, 2), provider=ProviderConfig(policy="reference"))
    assert seeded.config_hash == GOLDEN_HASHES["seeds"]
    priced = BenchmarkConfig.model_validate(
        {
            **openai_cfg,
            "provider": {
                **openai_cfg["provider"],
                "price_per_1k_prompt": 0.0,
                "price_per_1k_completion": 0.0,
            },
        }
    )
    assert priced.config_hash != GOLDEN_HASHES["openai"]  # pricing is part of the identity


# ------------------------------------------------------------------ budget and runner telemetry


def test_budget_fails_closed_on_unknown_cost() -> None:
    from traceforge.contracts.tasks import ResourceBudget

    b = BudgetManager(ResourceBudget(max_cost_usd=1.0))
    b.charge_generation(10, 0.1)
    assert b.exhausted() is None
    b.charge_generation(10, None)
    assert b.exhausted() == "max_cost_unverifiable"
    unlimited = BudgetManager(ResourceBudget())  # max_cost_usd = 0 disables the cost budget
    unlimited.charge_generation(10, None)
    assert unlimited.exhausted() is None and unlimited.cost_exhausted() is None


def test_unknown_cost_takes_precedence_over_token_limit() -> None:
    """Review #9: when one generation both exhausts the token limit and has unknown cost under an
    active cost budget, the stop reason is the unverifiable cost, not the token limit."""
    from traceforge.contracts.tasks import ResourceBudget

    b = BudgetManager(ResourceBudget(max_cost_usd=1.0, max_tokens=100))
    b.charge_generation(500, None)
    assert b.exhausted() == "max_cost_unverifiable"
    assert b.cost_exhausted() == "max_cost_unverifiable"
    no_cost_budget = BudgetManager(ResourceBudget(max_tokens=100))
    no_cost_budget.charge_generation(500, None)
    assert no_cost_budget.exhausted() == "max_tokens"  # unchanged without a cost budget
    known = BudgetManager(ResourceBudget(max_cost_usd=1.0, max_tokens=100))
    known.charge_generation(500, 0.1)
    assert known.exhausted() == "max_tokens"  # known cost below the limit: token stop as before


class _Fake:
    kind = "fake"
    model_id = "fake-model"
    model_revision = "r0"
    trusted = True

    def __init__(
        self,
        gens: list[Generation | BaseException | float],
        pricing_known: bool = True,
        trusted: bool = True,
    ):
        self._items = list(gens)
        self.pricing_known = pricing_known
        self.trusted = trusted
        self.closed = False

    async def generate(self, messages: Any, tools: Any, config: Any) -> Generation:
        item = self._items.pop(0)
        if isinstance(item, float):
            await asyncio.sleep(item)
            raise AssertionError("runner deadline should have fired")
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        self.closed = True


def _episode(
    registry: Any, sandbox: Any, provider: _Fake, *, max_cost: float = 0.0, timeout: float = 5.0
) -> Any:
    bundle = registry.bundle("authz-t0-s000")
    spec = bundle.spec.model_copy(
        update={"budget": bundle.spec.budget.model_copy(update={"max_cost_usd": max_cost})}
    )
    return asyncio.run(
        AgentRunner(sandbox).run_episode(
            spec,
            dict(bundle.workspace_files),
            provider,  # type: ignore[arg-type]
            run_id="t",
            correlation_id="t",
            generation=GenerationConfig(timeout_seconds=timeout),
        )
    )


def _assistant(result: Any) -> list[Any]:
    return [s for s in result.trajectory.steps if s.role.value == "assistant"]


READ = (GeneratedToolCall(call_id="c1", name="read_file", arguments={"path": "TASK.md"}),)


def test_runner_records_success_telemetry_and_stats(registry: Any, sandbox: Any) -> None:
    gens: list[Generation | BaseException | float] = [
        Generation(
            "",
            READ,
            100,
            20,
            0.01,
            "tool_calls",
            usage_reported=True,
            request_seconds=0.5,
            attempts=2,
        ),
        Generation(
            "done", (), 0, 0, None, None, usage_reported=False, request_seconds=0.25, attempts=1
        ),
    ]
    result = _episode(registry, sandbox, _Fake(gens))
    first, second = _assistant(result)
    assert first.token_usage is not None and first.metadata["attempts"] == 2
    assert first.metadata["cost_usd"] == 0.01 and first.metadata["provider_failure"] is None
    assert second.token_usage is None  # usage not reported: absent, not zero
    assert second.metadata["finish_reason"] is None and second.metadata["usage_reported"] is False
    stats = generation_stats(result.trajectory)
    assert stats["n_generation_requests"] == 2 and stats["n_requests_usage_reported"] == 1
    assert stats["prompt_tokens"] == 100 and stats["completion_tokens"] == 20
    assert stats["n_requests_cost_known"] == 1 and stats["cost_usd"] is None  # one cost unknown
    assert stats["generation_request_seconds"] == [0.5, 0.25]
    assert stats["provider_attempts"] == 3 and stats["provider_retries"] == 1
    assert stats["finish_reasons"] == {"tool_calls": 1, "not_reported": 1}
    assert stats["provider_failure"] is None


def test_runner_records_runner_deadline_timeout(registry: Any, sandbox: Any) -> None:
    result = _episode(registry, sandbox, _Fake([5.0]), timeout=0.2)
    (step,) = _assistant(result)
    assert result.episode.termination_reason == "provider_timeout"
    md = step.metadata
    assert md["provider_failure"] == "timeout" and md["failure_source"] == "runner_deadline"
    assert md["attempts"] is None and md["cost_usd"] is None
    # asyncio schedules the 0.2 s deadline on time.monotonic, which is coarse on some platforms
    # (15.625 ms on Windows), while request_seconds is measured with the high-resolution
    # perf_counter. The deadline can therefore fire up to one monotonic tick before 0.2 s of
    # perf_counter time has elapsed. The recorded value must be the deadline wait, within that
    # platform-reported tick, and nowhere near the provider's 5 s sleep.
    tick = time.get_clock_info("monotonic").resolution
    assert 0.2 - tick <= md["request_seconds"] < 1.0
    stats = generation_stats(result.trajectory)
    assert stats["provider_failure"] == "timeout" and stats["cost_usd"] is None


def test_runner_records_classified_provider_failure(registry: Any, sandbox: Any) -> None:
    exc = ProviderCallFailed(
        "provider returned HTTP 429 after 4 attempt(s)",
        details={"failure": "rate_limited", "attempts": 4, "status": 429, "seconds": 3.0},
    )
    result = _episode(registry, sandbox, _Fake([exc]))
    (step,) = _assistant(result)
    assert result.episode.termination_reason == "provider_error"
    md = step.metadata
    assert (md["provider_failure"], md["attempts"], md["http_status"]) == ("rate_limited", 4, 429)
    assert md["failure_source"] == "provider" and md["error"].startswith("ProviderCallFailed")
    assert generation_stats(result.trajectory)["provider_failure_source"] == "provider"


def test_unclassified_provider_exception_is_other(registry: Any, sandbox: Any) -> None:
    result = _episode(registry, sandbox, _Fake([RuntimeError("boom")]))
    (step,) = _assistant(result)
    assert step.metadata["provider_failure"] == "other"


def test_cost_budget_refused_without_known_pricing(registry: Any, sandbox: Any) -> None:
    provider = _Fake([], pricing_known=False)
    with pytest.raises(CapabilityUnavailable, match="cannot be enforced"):
        _episode(registry, sandbox, provider, max_cost=1.0)
    assert provider._items == []  # nothing was generated
    assert provider.closed  # review #4: the refused provider's client is released


def test_trust_refusal_closes_provider(registry: Any, sandbox: Any) -> None:
    """Review #4: the trust preflight refusal also releases the provider."""
    assert sandbox.trusted_code_only
    provider = _Fake([], trusted=False)
    with pytest.raises(CapabilityUnavailable, match="trusted demo policies"):
        _episode(registry, sandbox, provider)
    assert provider.closed


def _tools(result: Any) -> list[Any]:
    return [s for s in result.trajectory.steps if s.role.value == "tool"]


def test_cost_budget_stops_episode_when_a_cost_is_unknown(registry: Any, sandbox: Any) -> None:
    gens: list[Generation | BaseException | float] = [
        Generation("", READ, 10, 5, None, "tool_calls", usage_reported=True),
        Generation("never", (), 0, 0, 0.0, "stop"),
    ]
    result = _episode(registry, sandbox, _Fake(gens), max_cost=1.0)
    assert result.episode.termination_reason == "budget:max_cost_unverifiable"
    assert len(_assistant(result)) == 1
    assert _tools(result) == []  # review #1: its tool call never ran


def test_cost_overrun_stops_before_tool_calls_run(registry: Any, sandbox: Any) -> None:
    """Review #1: a generation that crosses the cost limit stops the episode before its tool
    calls execute (previously they ran and the stop only happened at the next turn)."""
    gens: list[Generation | BaseException | float] = [
        Generation("", READ, 10, 5, 0.05, "tool_calls", usage_reported=True),
        Generation("never", (), 0, 0, 0.0, "stop"),
    ]
    result = _episode(registry, sandbox, _Fake(gens), max_cost=0.03)
    assert result.episode.termination_reason == "budget:max_cost"
    assert len(_assistant(result)) == 1 and _tools(result) == []


def test_cost_stop_is_not_masked_by_no_tool_calls(registry: Any, sandbox: Any) -> None:
    """Review #1: a final answer with unknown cost under a cost budget is a budget stop, not
    a normal 'no_tool_calls' finish."""
    gens: list[Generation | BaseException | float] = [
        Generation("done", (), 10, 5, None, "stop", usage_reported=True),
    ]
    result = _episode(registry, sandbox, _Fake(gens), max_cost=1.0)
    assert result.episode.termination_reason == "budget:max_cost_unverifiable"


def test_cost_is_charged_once_per_generation(registry: Any, sandbox: Any) -> None:
    """0.02 per call against a 0.03 budget: the budget trips after the 2nd call, not the 1st
    (which would mean double accumulation) and not later (which would mean no accumulation)."""
    gens: list[Generation | BaseException | float] = [
        Generation("", READ, 10, 5, 0.02, "tool_calls", usage_reported=True),
        Generation("", READ, 10, 5, 0.02, "tool_calls", usage_reported=True),
        Generation("never", (), 0, 0, 0.0, "stop"),
    ]
    result = _episode(registry, sandbox, _Fake(gens), max_cost=0.03)
    assert result.episode.termination_reason == "budget:max_cost"
    assert len(_assistant(result)) == 2
    assert generation_stats(result.trajectory)["cost_usd"] == pytest.approx(0.04)


def test_explicitly_free_provider_runs_under_a_cost_budget(registry: Any, sandbox: Any) -> None:
    gens: list[Generation | BaseException | float] = [
        Generation("done", (), 10, 5, 0.0, "stop", usage_reported=True),
    ]
    result = _episode(registry, sandbox, _Fake(gens), max_cost=0.01)
    assert result.episode.termination_reason == "no_tool_calls"
    assert generation_stats(result.trajectory)["cost_usd"] == 0.0


def test_generation_stats_reads_pre_v03_trajectories(registry: Any, sandbox: Any) -> None:
    """Trajectories recorded before PR 1A lack the new step metadata: usage is inferred from the
    attached token counts, while cost, attempts and finish reason are reported as unknown."""
    gens: list[Generation | BaseException | float] = [
        Generation("done", (), 7, 3, 0.5, "stop", usage_reported=True, attempts=1),
    ]
    result = _episode(registry, sandbox, _Fake(gens))
    old_steps = [
        s.model_copy(update={"metadata": {"tool_calls": []}}) if s.role.value == "assistant" else s
        for s in result.trajectory.steps
    ]
    old = result.trajectory.model_copy(update={"steps": old_steps})
    stats = generation_stats(old)
    assert stats["prompt_tokens"] == 7 and stats["n_requests_usage_reported"] == 1
    assert stats["cost_usd"] is None and stats["n_requests_cost_known"] == 0
    assert stats["provider_attempts"] is None and stats["finish_reasons"] == {"not_reported": 1}
    assert len(stats["generation_request_seconds"]) == 1  # falls back to step timing


def test_provider_raised_timeout_is_not_a_runner_deadline(registry: Any, sandbox: Any) -> None:
    """Review #5: a TimeoutError raised by the provider before the runner's deadline expires is
    recorded as a provider failure (failure_source=provider), not as the runner deadline."""
    result = _episode(
        registry, sandbox, _Fake([TimeoutError("provider-side timeout")]), timeout=5.0
    )
    (step,) = _assistant(result)
    assert result.episode.termination_reason == "provider_timeout"  # unchanged reason
    assert step.metadata["provider_failure"] == "timeout"
    assert step.metadata["failure_source"] == "provider"
    assert step.metadata["request_seconds"] < 5.0


@pytest.mark.parametrize("details", ["oops", ["attempts", 3], 7], ids=["str", "list", "int"])
def test_non_dict_exception_details_are_ignored(registry: Any, sandbox: Any, details: Any) -> None:
    """Review #12: a provider exception with a non-dict `details` attribute must not break
    failure handling; the failure step is still recorded."""
    exc = RuntimeError("weird provider error")
    exc.details = details  # type: ignore[attr-defined]
    result = _episode(registry, sandbox, _Fake([exc]))
    (step,) = _assistant(result)
    assert result.episode.termination_reason == "provider_error"
    assert step.metadata["provider_failure"] == "other"
    assert step.metadata["attempts"] is None and step.metadata["http_status"] is None


def test_attempt_totals_are_unknown_when_any_request_count_is_unknown(
    registry: Any, sandbox: Any
) -> None:
    """Review #13: a known count (2 attempts) followed by a runner-deadline timeout (attempts
    unknown) must not yield a total that looks complete."""
    gens: list[Generation | BaseException | float] = [
        Generation("", READ, 1, 1, 0.0, "tool_calls", usage_reported=True, attempts=2),
        5.0,
    ]
    stats = generation_stats(_episode(registry, sandbox, _Fake(gens), timeout=0.2).trajectory)
    assert stats["n_generation_requests"] == 2 and stats["n_requests_attempts_known"] == 1
    assert stats["provider_attempts"] is None and stats["provider_retries"] is None


def test_generation_infers_usage_reported_for_older_providers() -> None:
    """Review #14: providers written before v0.3 that set token counts but not usage_reported
    keep their usage; zero counts alone are not 'reported'; an explicit value always wins."""
    assert Generation("x", (), 5, 3).usage_reported is True
    assert Generation("x", (), 0, 2).usage_reported is True
    assert Generation("x", ()).usage_reported is False
    assert Generation("x", (), 5, 3, usage_reported=False).usage_reported is False
    assert Generation("x", (), 0, 0, usage_reported=True).usage_reported is True


def test_older_provider_usage_reaches_the_trajectory(registry: Any, sandbox: Any) -> None:
    """Review #14, end to end: counts from a provider that never sets usage_reported are kept."""
    gens: list[Generation | BaseException | float] = [Generation("done", (), 12, 4, 0.0, "stop")]
    result = _episode(registry, sandbox, _Fake(gens))
    (step,) = _assistant(result)
    assert step.token_usage is not None and step.token_usage.prompt_tokens == 12
    assert generation_stats(result.trajectory)["prompt_tokens"] == 12


def test_model_provider_protocol_declares_pricing_known() -> None:
    """Review #7: pricing_known is part of the provider contract; both in-repo providers
    implement it."""
    from traceforge.domain.ports import ModelProvider

    assert "pricing_known" in dir(ModelProvider)
    assert ScriptedDemoProvider("reference").pricing_known is True
    assert _provider().pricing_known is False


def test_demo_provider_is_free_and_reports_no_usage() -> None:
    p = ScriptedDemoProvider("reference")
    assert p.pricing_known
    gen = asyncio.run(p.generate([], [], GenerationConfig()))
    assert gen.cost_usd == 0.0 and not gen.usage_reported


def test_untrusted_exception_details_are_sanitised(registry: Any, sandbox: Any) -> None:
    exc = ProviderError("odd", details={"attempts": "three", "status": {"x": 1}})
    result = _episode(registry, sandbox, _Fake([exc]))
    (step,) = _assistant(result)
    assert step.metadata["attempts"] is None and step.metadata["http_status"] is None
    assert step.metadata["provider_failure"] == "other"


def test_classified_failure_kinds_are_canonical() -> None:
    from traceforge.modules.agents.providers.openai_compatible import FAILURE_KINDS

    assert set(FAILURE_KINDS) == {
        "timeout",
        "rate_limited",
        "server_error",
        "http_error",
        "transport_error",
        "malformed_response",
    }


def test_generation_seconds_counts_each_assistant_step_exactly_once(
    registry: Any, sandbox: Any
) -> None:
    """Fixed timestamps: three assistant steps lasting 1.5 s, 2.25 s and 0.25 s must give
    generation_seconds == 4.0 (not 8.0), while non-assistant steps are excluded and
    generation_request_seconds keeps one value per request (measured value when recorded,
    otherwise the step's own span)."""
    from datetime import UTC, datetime, timedelta

    gens: list[Generation | BaseException | float] = [
        Generation("", READ, 1, 1, 0.0, "tool_calls", usage_reported=True),
        Generation("", READ, 1, 1, 0.0, "tool_calls", usage_reported=True),
        Generation("done", (), 1, 1, 0.0, "stop", usage_reported=True),
    ]
    result = _episode(registry, sandbox, _Fake(gens))
    base = datetime(2026, 11, 14, tzinfo=UTC)
    durations = iter([1.5, 2.25, 0.25])
    measured = iter([0.9, 2.0, None])  # third request has no provider-measured duration
    steps = []
    for i, s in enumerate(result.trajectory.steps):
        start = base + timedelta(seconds=10 * i)
        if s.role.value == "assistant":
            d = next(durations)
            md = {**s.metadata, "request_seconds": next(measured)}
            steps.append(
                s.model_copy(
                    update={
                        "started_at": start,
                        "completed_at": start + timedelta(seconds=d),
                        "metadata": md,
                    }
                )
            )
        else:  # system/user/tool steps get long spans that must not be counted
            steps.append(
                s.model_copy(
                    update={"started_at": start, "completed_at": start + timedelta(seconds=7)}
                )
            )
    assert sum(s.role.value == "assistant" for s in steps) == 3
    assert sum(s.role.value != "assistant" for s in steps) >= 2
    stats = generation_stats(result.trajectory.model_copy(update={"steps": steps}))
    assert stats["generation_seconds"] == 4.0  # 1.5 + 2.25 + 0.25, each counted once
    assert stats["n_generation_requests"] == 3
    assert stats["generation_request_seconds"] == [0.9, 2.0, 0.25]


# ------------------------------------------------------------------ final review fixes (PR #6)


@pytest.mark.parametrize(
    "arguments",
    [None, 5, 1.5, True, [1, 2]],
    ids=["null", "int", "float", "bool", "list"],
)
def test_invalid_tool_call_arguments_are_malformed(arguments: Any) -> None:
    """Null/number/bool/list arguments cannot become a ToolCall; previously they passed _parse
    and crashed the runner with an unclassified ValidationError after the call was charged."""
    body = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "read_file", "arguments": arguments}}
                    ]
                }
            }
        ]
    }
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(ProviderCallFailed) as err:
            asyncio.run(_generate(_provider(max_attempts=1)))
    assert err.value.details["failure"] == "malformed_response"


@pytest.mark.parametrize(
    "arguments",
    ['{"path": "TASK.md"}', {"path": "TASK.md"}, "not json at all"],
    ids=["json-string", "object", "raw-string"],
)
def test_string_and_object_tool_call_arguments_are_accepted(arguments: Any) -> None:
    """Strings (validated later by the router, even if not valid JSON) and objects pass."""
    body = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "read_file", "arguments": arguments}}
                    ]
                }
            }
        ]
    }
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(200, json=body))
        gen = asyncio.run(_generate(_provider(max_attempts=1)))
    assert gen.tool_calls[0].arguments == arguments


def test_missing_tool_call_arguments_default_to_empty_object() -> None:
    body = {"choices": [{"message": {"tool_calls": [{"id": "c1", "function": {"name": "x"}}]}}]}
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(200, json=body))
        gen = asyncio.run(_generate(_provider(max_attempts=1)))
    assert gen.tool_calls[0].arguments == "{}"


def test_deeply_nested_json_is_malformed_not_recursion_error() -> None:
    with respx.mock:
        respx.post(f"{URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, content=b"[" * 100_000, headers={"content-type": "application/json"}
            )
        )
        with pytest.raises(ProviderCallFailed) as err:
            asyncio.run(_generate(_provider(max_attempts=3)))
    d = err.value.details
    assert d["failure"] == "malformed_response" and d["attempts"] == 1 and d["status"] == 200


@pytest.mark.parametrize(
    ("prompt", "completion"),
    [(True, 0.0), (0.0, False), (True, True)],
    ids=["prompt-true", "completion-false", "both"],
)
def test_boolean_config_prices_are_rejected(prompt: Any, completion: Any) -> None:
    with pytest.raises(ValueError, match="not booleans"):
        ProviderConfig(
            kind="openai_compatible",
            base_url=URL,
            price_per_1k_prompt=prompt,
            price_per_1k_completion=completion,
        )


def test_integer_and_float_config_prices_are_preserved() -> None:
    cfg = ProviderConfig(
        kind="openai_compatible", base_url=URL, price_per_1k_prompt=1, price_per_1k_completion=0.25
    )
    assert cfg.price_per_1k_prompt == 1.0 and cfg.price_per_1k_completion == 0.25
    assert cfg.pricing_known


class _LegacyProvider:
    """A provider written before the pricing_known contract: no such attribute at all."""

    kind = "legacy"
    model_id = "legacy-model"
    model_revision = "r0"
    trusted = True

    def __init__(self) -> None:
        self.generated = False
        self.closed = False

    async def generate(self, messages: Any, tools: Any, config: Any) -> Generation:
        self.generated = True
        return Generation("done", ())

    async def aclose(self) -> None:
        self.closed = True


def test_legacy_provider_without_pricing_known_is_refused_under_cost_budget(
    registry: Any, sandbox: Any
) -> None:
    provider = _LegacyProvider()
    assert not hasattr(provider, "pricing_known")
    with pytest.raises(CapabilityUnavailable, match="cannot be enforced"):
        _episode(registry, sandbox, provider, max_cost=1.0)  # type: ignore[arg-type]
    assert not provider.generated and provider.closed


def test_legacy_provider_runs_without_a_cost_budget(registry: Any, sandbox: Any) -> None:
    """The getattr fallback only matters under an active cost budget."""
    provider = _LegacyProvider()
    result = _episode(registry, sandbox, provider)  # type: ignore[arg-type]
    assert result.episode.termination_reason == "no_tool_calls" and provider.closed


def test_real_provider_retry_under_cost_budget_stops_before_tools(
    registry: Any, sandbox: Any
) -> None:
    """End to end with the real OpenAI-compatible provider: a 429 then a successful tool-call
    response with known pricing and reported usage. The retry makes the call's cost unknown,
    so under an active cost budget the episode stops with budget:max_cost_unverifiable before
    the returned tool call executes. (The single-user override lets the untrusted provider use
    the local sandbox; no tool or candidate code runs, as asserted below.)"""
    from tenacity import wait_fixed

    bundle = registry.bundle("authz-t0-s000")
    spec = bundle.spec.model_copy(
        update={"budget": bundle.spec.budget.model_copy(update={"max_cost_usd": 1.0})}
    )
    tool_call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "TASK.md"}'},
    }
    success = httpx.Response(
        200,
        json={
            "choices": [{"message": {"tool_calls": [tool_call]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        },
    )
    provider = _provider(
        max_attempts=3,
        retry_wait=wait_fixed(0),
        price_per_1k_prompt=0.001,
        price_per_1k_completion=0.002,
    )
    assert provider.pricing_known
    with respx.mock:
        route = respx.post(f"{URL}/chat/completions")
        route.side_effect = [httpx.Response(429), success]
        result = asyncio.run(
            AgentRunner(sandbox, allow_untrusted_local_execution=True).run_episode(
                spec,
                dict(bundle.workspace_files),
                provider,
                run_id="t",
                correlation_id="t",
                generation=GenerationConfig(timeout_seconds=5),
            )
        )
    assert route.call_count == 2
    assert result.episode.termination_reason == "budget:max_cost_unverifiable"
    (step,) = _assistant(result)
    assert step.metadata["attempts"] == 2 and step.metadata["cost_usd"] is None
    assert step.token_usage is not None and step.token_usage.prompt_tokens == 100
    assert _tools(result) == []  # the returned tool call never executed
    stats = generation_stats(result.trajectory)
    assert stats["cost_usd"] is None and stats["provider_retries"] == 1
