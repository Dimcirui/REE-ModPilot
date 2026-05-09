"""
Unit tests for app/llm/client.py, anthropic_provider.py, openai_provider.py.

All tests use mock provider responses — no real API calls are made.
Run with: uv run pytest -m unit tests/unit/test_llm_client.py -v
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.llm.client import BaseProvider, LLMClient, LLMResponse, Tool
from app.llm.anthropic_provider import AnthropicProvider, _parse_response as anthropic_parse
from app.llm.openai_provider import OpenAIProvider, _parse_response as openai_parse


# ── helpers / stubs ────────────────────────────────────────────────────────


class EchoProvider(BaseProvider):
    """Minimal provider that echoes the last user message as content."""

    def __init__(self, model: str = "echo-v1") -> None:
        self._model = model

    def model_name(self) -> str:
        return self._model

    def chat(self, messages, *, system="", tools=None, max_tokens=4096) -> LLMResponse:
        last = messages[-1]["content"] if messages else ""
        return LLMResponse(content=f"echo: {last}", tool_calls=[], stop_reason="end_turn", raw=None)


def _make_anthropic_response(
    text: str = "hello",
    stop_reason: str = "end_turn",
    tool_calls: list[dict] | None = None,
) -> Any:
    """Build a minimal fake Anthropic Messages response object."""
    blocks = []

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    blocks.append(text_block)

    for tc in tool_calls or []:
        tb = MagicMock()
        tb.type = "tool_use"
        tb.name = tc["name"]
        tb.id = tc["id"]
        tb.input = tc["input"]
        blocks.append(tb)

    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = stop_reason
    return resp


def _make_openai_response(
    text: str = "hello",
    finish_reason: str = "stop",
    tool_calls_data: list[dict] | None = None,
) -> Any:
    """Build a minimal fake OpenAI ChatCompletion response object."""
    import json

    message = MagicMock()
    message.content = text

    if tool_calls_data:
        tcs = []
        for td in tool_calls_data:
            tc = MagicMock()
            tc.id = td["id"]
            tc.function.name = td["name"]
            tc.function.arguments = json.dumps(td["input"])
            tcs.append(tc)
        message.tool_calls = tcs
    else:
        message.tool_calls = None

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    response = MagicMock()
    response.choices = [choice]
    return response


# ── LLMClient / BaseProvider ───────────────────────────────────────────────


@pytest.mark.unit
class TestLLMClient:
    def test_delegates_to_provider(self):
        client = LLMClient(EchoProvider())
        resp = client.chat([{"role": "user", "content": "test input"}])
        assert resp.content == "echo: test input"
        assert resp.stop_reason == "end_turn"
        assert not resp.has_tool_calls

    def test_model_property(self):
        client = LLMClient(EchoProvider("my-model"))
        assert client.model == "my-model"

    def test_response_has_tool_calls_false_when_empty(self):
        resp = LLMResponse(content="hi", tool_calls=[], stop_reason="end_turn", raw=None)
        assert not resp.has_tool_calls

    def test_response_has_tool_calls_true(self):
        resp = LLMResponse(
            content="",
            tool_calls=[{"name": "do_thing", "id": "1", "input": {}}],
            stop_reason="tool_use",
            raw=None,
        )
        assert resp.has_tool_calls

    def test_from_settings_raises_on_unknown_provider(self):
        # settings is imported lazily inside from_settings(); patch at source
        with patch("app.config.settings") as mock_settings:
            mock_settings.llm_provider = "unknown_provider"
            with pytest.raises(ValueError, match="unknown_provider"):
                LLMClient.from_settings()

    def test_from_settings_builds_anthropic_provider(self):
        with patch("app.config.settings") as mock_settings:
            mock_settings.llm_provider = "anthropic"
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "claude-sonnet-4-6"
            mock_settings.llm_base_url = ""  # empty → real Anthropic endpoint
            with patch("app.llm.anthropic_provider.anthropic.Anthropic"):
                client = LLMClient.from_settings()
                assert isinstance(client._provider, AnthropicProvider)

    def test_from_settings_anthropic_with_deepseek_base_url(self):
        """AnthropicProvider should forward base_url to the SDK when set."""
        with patch("app.config.settings") as mock_settings:
            mock_settings.llm_provider = "anthropic"
            mock_settings.llm_api_key = "ds-key"
            mock_settings.llm_model = "deepseek-chat"
            mock_settings.llm_base_url = "https://api.deepseek.com/anthropic"
            with patch("app.llm.anthropic_provider.anthropic.Anthropic") as MockAnthropic:
                LLMClient.from_settings()
                call_kwargs = MockAnthropic.call_args.kwargs
                assert call_kwargs.get("base_url") == "https://api.deepseek.com/anthropic"

    def test_from_settings_builds_openai_provider(self):
        with patch("app.config.settings") as mock_settings:
            mock_settings.llm_provider = "openai_compatible"
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_model = "deepseek-chat"
            mock_settings.llm_base_url = "https://api.deepseek.com/v1"
            with patch("app.llm.openai_provider.OpenAI"):
                client = LLMClient.from_settings()
                assert isinstance(client._provider, OpenAIProvider)


# ── AnthropicProvider parsing ──────────────────────────────────────────────


@pytest.mark.unit
class TestAnthropicProviderParsing:
    def test_parse_text_response(self):
        raw = _make_anthropic_response(text="Hello world", stop_reason="end_turn")
        resp = anthropic_parse(raw)
        assert resp.content == "Hello world"
        assert resp.stop_reason == "end_turn"
        assert not resp.has_tool_calls

    def test_parse_tool_use_response(self):
        raw = _make_anthropic_response(
            text="",
            stop_reason="tool_use",
            tool_calls=[{"name": "run_phase", "id": "tc_1", "input": {"phase": "skeleton_align"}}],
        )
        resp = anthropic_parse(raw)
        assert resp.stop_reason == "tool_use"
        assert resp.has_tool_calls
        assert resp.tool_calls[0]["name"] == "run_phase"
        assert resp.tool_calls[0]["input"]["phase"] == "skeleton_align"

    def test_parse_max_tokens(self):
        raw = _make_anthropic_response(stop_reason="max_tokens")
        resp = anthropic_parse(raw)
        assert resp.stop_reason == "max_tokens"

    def test_chat_sends_system_with_cache_control(self):
        with patch("app.llm.anthropic_provider.anthropic.Anthropic") as MockAnthropic:
            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_anthropic_response()

            provider = AnthropicProvider(api_key="key", model="claude-haiku-4-5", cache_system=True)
            provider.chat([{"role": "user", "content": "hi"}], system="You are a guide.")

            call_kwargs = mock_client.messages.create.call_args.kwargs
            system_arg = call_kwargs["system"]
            assert isinstance(system_arg, list)
            assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

    def test_chat_no_cache_when_disabled(self):
        with patch("app.llm.anthropic_provider.anthropic.Anthropic") as MockAnthropic:
            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_anthropic_response()

            provider = AnthropicProvider(api_key="key", cache_system=False)
            provider.chat([{"role": "user", "content": "hi"}], system="sys")

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["system"] == "sys"  # plain string, not list


# ── OpenAIProvider parsing ─────────────────────────────────────────────────


@pytest.mark.unit
class TestOpenAIProviderParsing:
    def test_parse_text_response(self):
        raw = _make_openai_response(text="Hi there", finish_reason="stop")
        resp = openai_parse(raw)
        assert resp.content == "Hi there"
        assert resp.stop_reason == "end_turn"
        assert not resp.has_tool_calls

    def test_parse_tool_call_response(self):
        raw = _make_openai_response(
            finish_reason="tool_calls",
            tool_calls_data=[
                {"name": "run_phase", "id": "call_1", "input": {"phase": "pose_correction"}}
            ],
        )
        resp = openai_parse(raw)
        assert resp.stop_reason == "tool_use"
        assert resp.has_tool_calls
        assert resp.tool_calls[0]["name"] == "run_phase"
        assert resp.tool_calls[0]["input"]["phase"] == "pose_correction"

    def test_parse_max_tokens(self):
        raw = _make_openai_response(finish_reason="length")
        resp = openai_parse(raw)
        assert resp.stop_reason == "max_tokens"

    def test_chat_prepends_system_message(self):
        with patch("app.llm.openai_provider.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.return_value = _make_openai_response()

            provider = OpenAIProvider(api_key="key", model="deepseek-chat")
            provider.chat([{"role": "user", "content": "hello"}], system="Be helpful.")

            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            messages = call_kwargs["messages"]
            assert messages[0]["role"] == "system"
            assert messages[0]["content"] == "Be helpful."
            assert messages[1]["role"] == "user"

    def test_tools_translated_to_openai_format(self):
        with patch("app.llm.openai_provider.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.return_value = _make_openai_response()

            provider = OpenAIProvider(api_key="key")
            tools: list[Tool] = [
                {
                    "name": "run_phase",
                    "description": "Run a mod pipeline phase",
                    "input_schema": {
                        "type": "object",
                        "properties": {"phase": {"type": "string"}},
                        "required": ["phase"],
                    },
                }
            ]
            provider.chat([{"role": "user", "content": "go"}], tools=tools)

            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            oai_tools = call_kwargs["tools"]
            assert oai_tools[0]["type"] == "function"
            assert oai_tools[0]["function"]["name"] == "run_phase"
