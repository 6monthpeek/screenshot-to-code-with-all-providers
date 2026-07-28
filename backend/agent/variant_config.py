"""Generic per-variant model configuration.

A VariantModelConfig describes exactly one generation slot: which provider to
use, which concrete model id, and the credentials to reach that provider.

Provider families:
  - "openai"    — OpenAI-compatible HTTP (chat/completions). Used for OpenAI,
                  OmniRoute, OpenRouter, Groq, Together, Fireworks, Novita,
                  z.ai, NVIDIA NIM, Ollama, LM Studio, vLLM, SGLang, etc.
  - "anthropic" — Anthropic Messages API.
  - "gemini"    — Google GenAI SDK.
"""

from dataclasses import dataclass
from typing import Any, Literal, Mapping

ProviderFamily = Literal["openai", "anthropic", "gemini"]


@dataclass(frozen=True)
class VariantModelConfig:
    """One variant slot: provider + model id + credentials."""

    # Provider family determines which SDK/HTTP client to build.
    family: ProviderFamily

    # Concrete model id to send to the provider.
    #   OpenAI-compatible: e.g. "gpt-5.5", "antigravity/gemini-3.6-flash-high",
    #                        "openrouter/anthropic/claude-opus-4.8"
    #   Anthropic: e.g. "claude-opus-5", "claude-sonnet-4-6"
    #   Gemini: e.g. "gemini-3.6-flash", "gemini-3.1-pro"
    model_id: str

    # Human-readable label shown in the UI and logs.
    label: str

    # Credentials. For "openai" family both api_key and base_url may be set;
    # base_url=None means "https://api.openai.com/v1".
    api_key: str
    base_url: str | None = None

    # Optional OpenAI-style "reasoning_effort" hint for models that support it.
    reasoning_effort: str | None = None


def parse_variant_model_config(cfg_dict: Mapping[str, Any]) -> VariantModelConfig:
    """Build a VariantModelConfig from an untrusted wire-format dict.

    The frontend sends snake_case keys over the websocket; a stale or buggy
    client may omit fields. Raises ValueError with a human-readable message
    (safe to surface as a variantError) instead of KeyError.
    """
    model_id = str(cfg_dict.get("model_id") or "").strip()
    api_key = str(cfg_dict.get("api_key") or "").strip()
    missing = [
        name
        for name, value in (("model_id", model_id), ("api_key", api_key))
        if not value
    ]
    if missing:
        raise ValueError(
            "Variant model configuration is missing required field(s): "
            f"{', '.join(missing)}. Check the provider/variant settings."
        )

    family = cfg_dict.get("family", "openai")
    if family not in ("openai", "anthropic", "gemini"):
        raise ValueError(
            f"Variant model configuration has unknown provider family '{family}'. "
            "Expected one of: openai, anthropic, gemini."
        )

    base_url = cfg_dict.get("base_url") or None
    reasoning_effort = cfg_dict.get("reasoning_effort") or None
    return VariantModelConfig(
        family=family,
        model_id=model_id,
        label=str(cfg_dict.get("label") or model_id),
        api_key=api_key,
        base_url=str(base_url) if base_url else None,
        reasoning_effort=str(reasoning_effort) if reasoning_effort else None,
    )
