"""
Anthropic SDK provider adapter.

Covers two backends via the same SDK:
  1. Real Claude (Sonnet / Haiku) — leave base_url=None (default Anthropic endpoint).
  2. DeepSeek V4 via Anthropic-compatible API — base_url="https://api.deepseek.com/anthropic".
     DeepSeek implements the Anthropic messages protocol at this endpoint, so tool_use
     format and prompt caching semantics are identical to real Claude.

Prompt caching is wired on the system prompt by default — set the
`cache_system` flag to False only if you are explicitly testing without it.
Anthropic's cache TTL is 5 minutes; the agent loop should be designed to
re-use the same LLMClient instance across turns to benefit from caching.
"""

from __future__ import annotations

from typing import Any

import anthropic

from app.llm.client import BaseProvider, LLMResponse, Message, Tool


class AnthropicProvider(BaseProvider):
    """
    Wraps the official Anthropic Python SDK.

    Args:
        api_key:      API key (Anthropic key, or DeepSeek key when using their Anthropic endpoint).
        model:        Model string, e.g. "claude-sonnet-4-6" or "deepseek-chat".
        base_url:     Override the API endpoint. None = default Anthropic.
                      DeepSeek Anthropic-compatible: "https://api.deepseek.com/anthropic"
        cache_system: Whether to attach cache_control to the system prompt.
                      Defaults to True (always cache system prompt).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        base_url: str | None = None,
        cache_system: bool = True,
    ) -> None:
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**client_kwargs)
        self._model = model
        self._cache_system = cache_system

    def model_name(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[Tool] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        # System prompt with optional prompt caching
        if system:
            if self._cache_system:
                kwargs["system"] = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                kwargs["system"] = system

        # Tool definitions
        if tools:
            kwargs["tools"] = _to_anthropic_tools(tools)

        response = self._client.messages.create(**kwargs)
        return _parse_response(response)


# ── helpers ────────────────────────────────────────────────────────────────


def _to_anthropic_tools(tools: list[Tool]) -> list[dict]:
    """
    Convert our canonical Tool format to Anthropic's tool schema.
    Our format already matches Anthropic's — just pass through.
    """
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t["input_schema"],
        }
        for t in tools
    ]


def _parse_response(response: Any) -> LLMResponse:
    """Extract content and tool calls from an Anthropic Messages response."""
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    content_blocks: list[dict] = []

    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
            content_blocks.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            tool_calls.append({"name": block.name, "id": block.id, "input": block.input})
            content_blocks.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            )
        elif block.type == "thinking":
            # Thinking blocks must be passed back verbatim on subsequent turns.
            content_blocks.append({"type": "thinking", "thinking": block.thinking})
        else:
            # Unknown block type — pass through opaquely so the API doesn't reject it.
            content_blocks.append({"type": block.type})

    # Normalize stop_reason to our vocabulary
    stop_map = {
        "end_turn": "end_turn",
        "tool_use": "tool_use",
        "max_tokens": "max_tokens",
        "stop_sequence": "stop",
    }
    stop_reason = stop_map.get(response.stop_reason or "", response.stop_reason or "end_turn")

    return LLMResponse(
        content=" ".join(text_parts),
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        raw=response,
        content_blocks=content_blocks,
    )
