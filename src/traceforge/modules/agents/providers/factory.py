"""Provider factory (one fresh provider instance per episode)."""

from __future__ import annotations

from traceforge.contracts.common import CapabilityUnavailable
from traceforge.contracts.experiments import ProviderConfig
from traceforge.domain.ports import ModelProvider
from traceforge.modules.agents.providers.demo import ScriptedDemoProvider
from traceforge.modules.agents.providers.openai_compatible import OpenAICompatibleProvider


def build_provider(config: ProviderConfig) -> ModelProvider:
    if config.kind == "demo":
        return ScriptedDemoProvider(config.policy)
    if config.kind == "openai_compatible":
        if not config.base_url:
            raise CapabilityUnavailable("provider.base_url is required for openai_compatible")
        return OpenAICompatibleProvider(
            base_url=config.base_url, model_id=config.model_id, api_key_env=config.api_key_env
        )
    raise CapabilityUnavailable(f"unknown provider kind '{config.kind}'")
