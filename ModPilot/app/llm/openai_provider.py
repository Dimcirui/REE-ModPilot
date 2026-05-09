"""
OpenAI-compatible provider adapter.

Handles any API that speaks the OpenAI chat-completions protocol:
  - DeepSeek V4   (base_url="https://api.deepseek.com/v1")
  - Qwen3         (base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
  - Local Ollama  (base_url="http://localhost:11434/v1")
  - OpenAI itself (leave base_url=None)

Tool-call translation:
  Our canonical Tool format → OpenAI "function" tool shape.
  The openai SDK normalises both direction.
"""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from app.llm.client import BaseProvider, LLMResponse, Message, Tool


class OpenAIProvider(BaseProvider):
    """
    Wraps the official OpenAI Python SDK with a custom base_url for
    OpenAI-compatible providers (DeepSeek V4, etc.).

    Args:
        api_key:   Provider API key.
        model:     Model string, e.g. "deepseek-chat".
        base_url:  API base URL. None → default OpenAI endpoint.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str | None = "https://api.deepseek.com/v1",
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

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
        # Prepend system message if provided
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
        }

        if tools:
            kwargs["tools"] = _to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"

        response = self._client.chat.completions.create(**kwargs)
        return _parse_response(response)


# ── helpers ────────────────────────────────────────────────────────────────


def _to_openai_tools(tools: list[Tool]) -> list[dict]:
    """Convert our canonical Tool format to OpenAI's function-tool schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def _parse_response(response: Any) -> LLMResponse:
    """Extract content and tool calls from an OpenAI ChatCompletion response."""
    choice = response.choices[0]
    message = choice.message

    text_content = message.content or ""
    tool_calls: list[dict] = []

    if message.tool_calls:
        import json

        for tc in message.tool_calls:
            try:
                input_data = json.loads(tc.function.arguments)
            except Exception:
                input_data = {"_raw": tc.function.arguments}
            tool_calls.append(
                {
                    "name": tc.function.name,
                    "id": tc.id,
                    "input": input_data,
                }
            )

    # Normalize finish_reason to our vocabulary
    finish_map = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "content_filter": "stop",
    }
    stop_reason = finish_map.get(choice.finish_reason or "", "end_turn")

    return LLMResponse(
        content=text_content,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        raw=response,
    )
