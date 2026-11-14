"""OpenAI-compatible chat-completions provider (works with vLLM, OpenAI, local gateways).

Secrets are read from the environment variable named in configuration and never logged.
Model output is untrusted: tool calls are passed to the tool router for validation.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from traceforge.contracts.common import CapabilityUnavailable, ErrorCode, TraceForgeError
from traceforge.domain.ports import (
    ChatMessage,
    GeneratedToolCall,
    Generation,
    GenerationConfig,
    ToolSpec,
)


class ProviderError(TraceForgeError):
    code = ErrorCode.PROVIDER_ERROR


class RetryableProviderError(ProviderError):
    retryable = True


class ProviderCallFailed(ProviderError):
    """A generation call that failed after retries, classified for telemetry.

    ``details`` holds ``failure`` (timeout | rate_limited | server_error | http_error |
    transport_error | malformed_response), ``attempts``, ``status`` (last HTTP status or None)
    and ``seconds`` (wall time including retries). Provider failures are infrastructure/model
    access failures, distinct from verifier outcomes.
    """


FAILURE_KINDS = (
    "timeout",
    "rate_limited",
    "server_error",
    "http_error",
    "transport_error",
    "malformed_response",
)


# Sanity cap on a single request's reported token count; larger values are malformed (and would
# otherwise overflow float arithmetic in cost calculation).
MAX_TOKEN_COUNT = 10**9


def _count(usage: dict[str, Any], key: str) -> int | None:
    """A reported token count. None when the field is absent or null (usage not reported);
    ProviderError (classified malformed_response) when present but not an integer in
    [0, MAX_TOKEN_COUNT]."""
    value = usage.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_TOKEN_COUNT:
        raise ProviderError(f"malformed provider response: invalid usage.{key}")
    return value


def _reported_cost(usage: dict[str, Any]) -> tuple[bool, float | None]:
    """Cost stated by the provider itself (some gateways report ``usage.cost`` in USD).

    Returns (present, value). A present but invalid value (non-numeric, negative, non-finite or
    too large for a float) gives (True, None): the cost is unknown. It must not fall back to a
    price-based estimate the provider contradicted, and is never treated as zero.
    """
    value = usage.get("cost")
    if value is None:
        return False, None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return True, None
    try:
        cost = float(value)
    except OverflowError:
        return True, None
    if not math.isfinite(cost) or cost < 0:
        return True, None
    return True, cost


def _validate_prices(prompt: float | None, completion: float | None) -> None:
    """Same rules as ProviderConfig, for providers constructed directly."""
    if (prompt is None) != (completion is None):
        raise ValueError(
            "set both price_per_1k_prompt and price_per_1k_completion (use 0.0 for free), "
            "or neither (pricing unknown)"
        )
    for price in (prompt, completion):
        if price is None:
            continue
        if isinstance(price, bool) or not isinstance(price, int | float):
            raise ValueError("prices must be numbers (USD per 1K tokens)")
        if not math.isfinite(price) or price < 0:
            raise ValueError("prices must be finite and non-negative")


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (RetryableProviderError, httpx.TimeoutException, httpx.TransportError))


def _encode_message(m: ChatMessage) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = list(m.tool_calls)
    if m.tool_call_id:
        out["tool_call_id"] = m.tool_call_id
    if m.name and m.role == "tool":
        out["name"] = m.name
    return out


class OpenAICompatibleProvider:
    kind = "openai_compatible"
    trusted = False

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        api_key_env: str | None = None,
        max_attempts: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
        price_per_1k_prompt: float | None = None,
        price_per_1k_completion: float | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        if not base_url:
            raise CapabilityUnavailable("openai_compatible provider requires base_url")
        _validate_prices(price_per_1k_prompt, price_per_1k_completion)
        headers = {"Content-Type": "application/json"}
        if api_key_env:
            key = os.environ.get(api_key_env)
            if not key:
                raise CapabilityUnavailable(
                    f"environment variable {api_key_env} is not set (required for provider auth)"
                )
            headers["Authorization"] = f"Bearer {key}"
        self.model_id = model_id
        self.model_revision = "remote"
        self._max_attempts = max_attempts
        # Delay between retries (injectable so tests can use a deterministic wait).
        self._retry_wait: wait_base = retry_wait or wait_exponential_jitter(multiplier=0.5, max=8.0)
        # None = unknown (cost reported as unknown), 0.0 = explicitly free.
        self._prices: tuple[float | None, float | None] = (
            price_per_1k_prompt,
            price_per_1k_completion,
        )
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, transport=transport, timeout=60.0
        )

    @property
    def pricing_known(self) -> bool:
        """True when every call's cost can be determined from reported usage."""
        return self._prices[0] is not None and self._prices[1] is not None

    async def generate(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], config: GenerationConfig
    ) -> Generation:
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": [_encode_message(m) for m in messages],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if config.seed is not None:
            body["seed"] = config.seed
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        data: dict[str, Any] = {}
        attempts = 0
        status: int | None = None
        started = time.perf_counter()

        def failed(kind: str, message: str) -> ProviderCallFailed:
            return ProviderCallFailed(
                message,
                details={
                    "failure": kind,
                    "attempts": attempts,
                    "status": status,
                    "seconds": round(time.perf_counter() - started, 6),
                },
            )

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_attempts),
                wait=self._retry_wait,
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    attempts = attempt.retry_state.attempt_number
                    status = None
                    resp = await self._client.post(
                        "/chat/completions", json=body, timeout=config.timeout_seconds
                    )
                    status = resp.status_code
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise RetryableProviderError(f"provider returned HTTP {resp.status_code}")
                    if resp.status_code >= 400:
                        raise ProviderError(f"provider returned HTTP {resp.status_code}")
                    data = resp.json()
        except httpx.TimeoutException as exc:  # before TransportError: it is a subclass
            raise failed(
                "timeout", f"provider request timed out after {attempts} attempt(s)"
            ) from exc
        except httpx.DecodingError as exc:  # body could not be decoded (e.g. bad compression)
            raise failed("malformed_response", "provider response could not be decoded") from exc
        except httpx.RequestError as exc:  # TransportError and the other request-level errors
            raise failed(
                "transport_error", f"provider transport error: {type(exc).__name__}"
            ) from exc
        except RetryableProviderError as exc:
            kind = "rate_limited" if status == 429 else "server_error"
            raise failed(kind, f"{exc.message} after {attempts} attempt(s)") from exc
        except ProviderError as exc:
            raise failed("http_error", exc.message) from exc
        except (ValueError, RecursionError) as exc:  # not JSON, or nested too deeply to parse
            raise failed("malformed_response", "provider response is not valid JSON") from exc
        try:
            gen = self._parse(data)
        except ProviderError as exc:
            raise failed("malformed_response", exc.message) from exc
        # Earlier attempts (timeouts, 5xx, transport errors, even 429s) may have been processed
        # and billed by the provider, and a response only reports its own cost. After any retry
        # the total cost of the call is therefore unknown, never just the last attempt's cost.
        cost = gen.cost_usd if attempts <= 1 else None
        return replace(
            gen,
            cost_usd=cost,
            request_seconds=round(time.perf_counter() - started, 6),
            attempts=attempts,
        )

    def _parse(self, data: dict[str, Any]) -> Generation:
        # Any structural surprise in the body is a malformed response, never an unclassified crash.
        try:
            choice = data["choices"][0]
            message = choice.get("message")
            if not isinstance(message, dict):  # missing, null or not an object
                raise TypeError("choice.message is not an object")
            calls: list[GeneratedToolCall] = []
            for i, tc in enumerate(message.get("tool_calls") or []):
                fn = tc.get("function") or {}
                arguments = fn.get("arguments", "{}")
                # A raw JSON string (validated later by the router) or an object; null, numbers,
                # booleans and lists cannot become a ToolCall and are a malformed response.
                if not isinstance(arguments, str | dict):
                    raise TypeError("tool-call arguments must be a string or an object")
                calls.append(
                    GeneratedToolCall(
                        call_id=str(tc.get("id") or f"call-{i}"),
                        name=str(fn.get("name", "")),
                        arguments=arguments,
                    )
                )
            content = str(message.get("content") or "")
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderError("malformed provider response") from exc
        usage = data.get("usage")
        if usage is None:
            usage = {}
        elif not isinstance(usage, dict):
            raise ProviderError("malformed provider response: usage is not an object")
        pt, ct = _count(usage, "prompt_tokens"), _count(usage, "completion_tokens")
        # Usage counts as reported only when both counts are present; a partial report is
        # discarded entirely (both counts 0), never half-used.
        reported = pt is not None and ct is not None
        return Generation(
            content=content,
            tool_calls=tuple(calls),
            prompt_tokens=pt if reported and pt is not None else 0,
            completion_tokens=ct if reported and ct is not None else 0,
            cost_usd=self._cost(usage, pt if reported else None, ct if reported else None),
            finish_reason=None if finish is None else str(finish),
            usage_reported=reported,
        )

    def _cost(self, usage: dict[str, Any], pt: int | None, ct: int | None) -> float | None:
        """Provider-reported cost if present (None if that value is invalid); else reported
        usage x known prices; else None (unknown). Never a non-finite number."""
        present, reported = _reported_cost(usage)
        if present:
            return reported
        prompt_price, completion_price = self._prices
        if pt is None or ct is None or prompt_price is None or completion_price is None:
            return None
        cost = pt / 1000 * prompt_price + ct / 1000 * completion_price
        return cost if math.isfinite(cost) else None

    async def aclose(self) -> None:
        await self._client.aclose()
