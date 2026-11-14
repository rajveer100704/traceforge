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


def _count(value: Any) -> int | None:
    """A reported token count, or None when absent or not a non-negative integer."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _reported_cost(usage: dict[str, Any]) -> float | None:
    """Cost stated by the provider itself (some gateways report ``usage.cost`` in USD)."""
    value = usage.get("cost")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


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
    ) -> None:
        if not base_url:
            raise CapabilityUnavailable("openai_compatible provider requires base_url")
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
                wait=wait_exponential_jitter(multiplier=0.5, max=8.0),
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
        except ValueError as exc:  # body is not JSON
            raise failed("malformed_response", "provider response is not valid JSON") from exc
        try:
            gen = self._parse(data)
        except ProviderError as exc:
            raise failed("malformed_response", exc.message) from exc
        return replace(
            gen, request_seconds=round(time.perf_counter() - started, 6), attempts=attempts
        )

    def _parse(self, data: dict[str, Any]) -> Generation:
        # Any structural surprise in the body is a malformed response, never an unclassified crash.
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
            calls: list[GeneratedToolCall] = []
            for i, tc in enumerate(message.get("tool_calls") or []):
                fn = tc.get("function") or {}
                calls.append(
                    GeneratedToolCall(
                        call_id=str(tc.get("id") or f"call-{i}"),
                        name=str(fn.get("name", "")),
                        arguments=fn.get("arguments", "{}"),  # raw string; validated by router
                    )
                )
            content = str(message.get("content") or "")
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderError("malformed provider response") from exc
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        pt, ct = _count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens"))
        reported = pt is not None and ct is not None
        return Generation(
            content=content,
            tool_calls=tuple(calls),
            prompt_tokens=pt if reported and pt is not None else 0,
            completion_tokens=ct if reported and ct is not None else 0,
            cost_usd=self._cost(usage, pt, ct) if reported else _reported_cost(usage),
            finish_reason=None if finish is None else str(finish),
            usage_reported=reported,
        )

    def _cost(self, usage: dict[str, Any], pt: int | None, ct: int | None) -> float | None:
        """Provider-reported cost if present; else reported usage x known prices; else None."""
        reported = _reported_cost(usage)
        if reported is not None:
            return reported
        prompt_price, completion_price = self._prices
        if pt is None or ct is None or prompt_price is None or completion_price is None:
            return None
        return pt / 1000 * prompt_price + ct / 1000 * completion_price

    async def aclose(self) -> None:
        await self._client.aclose()
