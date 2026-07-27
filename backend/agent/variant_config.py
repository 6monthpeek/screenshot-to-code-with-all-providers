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
from typing import Literal

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
