"""OpenAI-compatible chat-completions provider (works with vLLM, OpenAI, local gateways).

Secrets are read from the environment variable named in configuration and never logged.
Model output is untrusted: tool calls are passed to the tool router for validation.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
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
        price_per_1k_prompt: float = 0.0,
        price_per_1k_completion: float = 0.0,
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
        self._prices = (price_per_1k_prompt, price_per_1k_completion)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, transport=transport, timeout=60.0
        )

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
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential_jitter(multiplier=0.5, max=8.0),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.post(
                    "/chat/completions", json=body, timeout=config.timeout_seconds
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RetryableProviderError(f"provider returned HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    raise ProviderError(f"provider returned HTTP {resp.status_code}")
                data = resp.json()
        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> Generation:
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("malformed provider response") from exc
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
        usage = data.get("usage") or {}
        pt, ct = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        cost = pt / 1000 * self._prices[0] + ct / 1000 * self._prices[1]
        return Generation(
            content=str(message.get("content") or ""),
            tool_calls=tuple(calls),
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=cost,
            finish_reason=str(choice.get("finish_reason", "stop")),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
