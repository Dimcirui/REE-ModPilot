"""
Provider-agnostic LLM client (design decision C10).

Supports three backends:
  - "anthropic"         → AnthropicProvider  (Claude Sonnet / Haiku)
  - "openai_compatible" → OpenAIProvider     (DeepSeek V4 direct, Qwen, etc.)
  - "ollama"            → OllamaProvider     (Ollama Cloud / local Ollama)

Callers import LLMClient and call chat() — provider routing is transparent.

Message format follows the Anthropic convention (role/content dicts) since
that is the richer of the two schemas. The provider adapters translate as
needed.

Tool-call shape (for future agent loop integration):
    tools=[{"name": ..., "description": ..., "input_schema": {...}}]
The adapters translate these to the provider-specific format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# ── shared types ───────────────────────────────────────────────────────────

Message = dict[str, Any]  # {"role": "user"|"assistant", "content": str | list}
Tool = dict[str, Any]  # {"name": str, "description": str, "input_schema": {...}}


class LLMResponse:
    """
    Normalized response from any provider.

    Attributes:
        content     Plain-text reply (first text block).
        tool_calls  List of tool-use requests, each:
                    {"name": str, "id": str, "input": dict}
        raw         The raw provider response object (for debugging).
        stop_reason One of: "end_turn", "tool_use", "max_tokens", "stop".
    """

    def __init__(
        self,
        content: str,
        tool_calls: list[dict],
        stop_reason: str,
        raw: Any,
        content_blocks: list[dict] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.stop_reason = stop_reason
        self.raw = raw
        # Serialized list of all content blocks (text / tool_use / thinking / …).
        # Populated by providers that need to round-trip opaque blocks (e.g. thinking).
        # Empty list means the provider did not supply block-level detail.
        self.content_blocks: list[dict] = content_blocks or []

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    def __repr__(self) -> str:
        return (
            f"LLMResponse(stop={self.stop_reason!r}, "
            f"tool_calls={len(self.tool_calls)}, "
            f"content={self.content[:60]!r})"
        )


# ── abstract base ──────────────────────────────────────────────────────────


class BaseProvider(ABC):
    """Interface all LLM providers must implement."""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[Tool] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send messages, return a normalized LLMResponse."""
        ...

    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier string used by this provider."""
        ...


# ── facade ─────────────────────────────────────────────────────────────────


class LLMClient:
    """
    Thin facade that delegates to the configured provider.

    Instantiate via LLMClient.from_settings() for normal use, or pass a
    BaseProvider directly (useful in tests).
    """

    def __init__(self, provider: BaseProvider) -> None:
        self._provider = provider

    @classmethod
    def from_settings(cls) -> LLMClient:
        """
        Build an LLMClient from app.config.settings.
        Import is deferred so unit tests can construct providers without
        touching settings / environment.
        """
        from app.config import settings
        from app.llm.anthropic_provider import AnthropicProvider
        from app.llm.ollama_provider import OllamaProvider
        from app.llm.openai_provider import OpenAIProvider

        if settings.llm_provider == "anthropic":
            # base_url is optional — leave None for real Anthropic endpoint,
            # or set to "https://api.deepseek.com/anthropic" for DeepSeek V4.
            provider: BaseProvider = AnthropicProvider(
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                base_url=settings.llm_base_url or None,
            )
        elif settings.llm_provider == "openai_compatible":
            provider = OpenAIProvider(
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                base_url=settings.llm_base_url,
            )
        elif settings.llm_provider == "ollama":
            # base_url is optional — leave empty for Ollama Cloud (https://ollama.com),
            # or set to http://localhost:11434 for a local Ollama daemon.
            provider = OllamaProvider(
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                base_url=settings.llm_base_url or None,
            )
        else:
            raise ValueError(
                f"Unknown LLM_PROVIDER={settings.llm_provider!r}. "
                "Expected 'anthropic', 'openai_compatible', or 'ollama'."
            )
        return cls(provider)

    def chat(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[Tool] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Delegate to the underlying provider."""
        return self._provider.chat(
            messages, system=system, tools=tools, max_tokens=max_tokens
        )

    @property
    def model(self) -> str:
        return self._provider.model_name()
