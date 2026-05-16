"""
Unit tests for app/llm/ollama_provider.py.

Covers:
  - _to_ollama_messages translates Anthropic-style content blocks into
    Ollama's flat message format (plain text, tool_use → tool_calls,
    tool_result → standalone `tool` message, mixed assistant blocks).
  - _to_ollama_tools maps canonical Tool entries to the function-tool shape.
  - _parse_response extracts text + tool_calls, synthesizes ids, and maps
    done_reason to our normalized stop_reason vocabulary; string-encoded
    arguments are JSON-parsed; broken JSON falls back to {_raw: ...}.
  - OllamaProvider.chat POSTs to /api/chat with Bearer auth and parses the
    real response shape (mocked httpx.Client).
  - Constructor rejects empty api_key.

Run with: uv run pytest -m unit tests/unit/test_ollama_provider.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.llm.ollama_provider import (
    DEFAULT_BASE_URL,
    OllamaProvider,
    _parse_response,
    _to_ollama_messages,
    _to_ollama_tools,
)

# ── message translation ───────────────────────────────────────────────────


@pytest.mark.unit
def test_to_ollama_messages_plain_text_and_system():
    out = _to_ollama_messages(
        [{"role": "user", "content": "hi there"}],
        system="be brief",
    )
    assert out == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi there"},
    ]


@pytest.mark.unit
def test_to_ollama_messages_assistant_blocks_with_tool_use():
    """Anthropic-style assistant message with a text + tool_use block →
    Ollama assistant with content + tool_calls. Tool-call ids are stripped
    because Ollama doesn't round-trip them."""
    msgs = [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": "calling tool"},
            {"type": "tool_use", "id": "tc_1", "name": "physics_classification",
             "input": {"target_armature": "MHWs"}},
        ],
    }]
    out = _to_ollama_messages(msgs, system="")
    assert out == [{
        "role": "assistant",
        "content": "calling tool",
        "tool_calls": [
            {"function": {"name": "physics_classification",
                          "arguments": {"target_armature": "MHWs"}}},
        ],
    }]


@pytest.mark.unit
def test_to_ollama_messages_tool_result_becomes_standalone_tool_role():
    """Anthropic packages tool_result blocks inside a user message; Ollama
    expects them as separate `tool` messages. Plain text fragments in the
    same user message are emitted as a sibling user message."""
    msgs = [{
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tc_1", "content": "Phase 4A ok"},
            {"type": "text", "text": "continue please"},
        ],
    }]
    out = _to_ollama_messages(msgs, system="")
    assert out == [
        {"role": "tool", "content": "Phase 4A ok"},
        {"role": "user", "content": "continue please"},
    ]


@pytest.mark.unit
def test_to_ollama_messages_unknown_block_types_are_dropped():
    """`thinking` and other non-roundtrippable blocks must not leak into
    the outgoing payload — Ollama would reject the unknown shape."""
    msgs = [{
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "ignore me"},
            {"type": "text", "text": "kept"},
        ],
    }]
    out = _to_ollama_messages(msgs, system="")
    assert out == [{"role": "assistant", "content": "kept"}]


@pytest.mark.unit
def test_to_ollama_messages_tool_result_with_list_content():
    """Some agents send tool_result.content as a list of {type: text} blocks.
    Flatten to a plain string before handing to Ollama."""
    msgs = [{
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": "tc_1",
            "content": [{"type": "text", "text": "line A"},
                        {"type": "text", "text": "line B"}],
        }],
    }]
    out = _to_ollama_messages(msgs, system="")
    assert out == [{"role": "tool", "content": "line A line B"}]


# ── tool schema translation ───────────────────────────────────────────────


@pytest.mark.unit
def test_to_ollama_tools_maps_canonical_to_function_schema():
    tools = [{
        "name": "pose_correction",
        "description": "do the thing",
        "input_schema": {"type": "object", "properties": {"x_preset": {"type": "string"}}},
    }]
    assert _to_ollama_tools(tools) == [{
        "type": "function",
        "function": {
            "name": "pose_correction",
            "description": "do the thing",
            "parameters": {"type": "object", "properties": {"x_preset": {"type": "string"}}},
        },
    }]


# ── response parsing ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_parse_response_plain_text():
    payload = {
        "model": "deepseek-v4-flash",
        "done": True,
        "done_reason": "stop",
        "message": {"role": "assistant", "content": "pong"},
    }
    r = _parse_response(payload)
    assert r.content == "pong"
    assert r.tool_calls == []
    assert r.stop_reason == "end_turn"
    assert r.has_tool_calls is False


@pytest.mark.unit
def test_parse_response_with_tool_calls_synthesizes_ids():
    """Ollama's tool_calls don't include ids. The provider must synthesize
    unique client-side ids so the agent loop can match tool_use ↔ tool_result."""
    payload = {
        "done_reason": "stop",
        "message": {
            "content": "",
            "tool_calls": [
                {"function": {"name": "physics_classification",
                              "arguments": {"target_armature": "MHWs"}}},
                {"function": {"name": "material_inspect",
                              "arguments": {"target_object": "Body", "texture_dir": "C:/tex"}}},
            ],
        },
    }
    r = _parse_response(payload)
    assert r.has_tool_calls is True
    assert len(r.tool_calls) == 2
    assert r.tool_calls[0]["name"] == "physics_classification"
    assert r.tool_calls[0]["input"] == {"target_armature": "MHWs"}
    assert r.tool_calls[1]["name"] == "material_inspect"
    # Ids are unique and present.
    ids = {tc["id"] for tc in r.tool_calls}
    assert len(ids) == 2
    assert all(i.startswith("tc_") for i in ids)
    # Done_reason "stop" + non-empty tool_calls → stop_reason promoted to tool_use.
    assert r.stop_reason == "tool_use"


@pytest.mark.unit
def test_parse_response_string_encoded_arguments_are_decoded():
    payload = {
        "done_reason": "tool_calls",
        "message": {"content": "", "tool_calls": [
            {"function": {"name": "x", "arguments": '{"a": 1, "b": 2}'}},
        ]},
    }
    r = _parse_response(payload)
    assert r.tool_calls[0]["input"] == {"a": 1, "b": 2}


@pytest.mark.unit
def test_parse_response_invalid_json_arguments_falls_back():
    payload = {
        "done_reason": "tool_calls",
        "message": {"content": "", "tool_calls": [
            {"function": {"name": "x", "arguments": "not json {"}},
        ]},
    }
    r = _parse_response(payload)
    assert r.tool_calls[0]["input"] == {"_raw": "not json {"}


@pytest.mark.unit
def test_parse_response_length_reason_maps_to_max_tokens():
    payload = {
        "done_reason": "length",
        "message": {"role": "assistant", "content": "truncated…"},
    }
    assert _parse_response(payload).stop_reason == "max_tokens"


# ── chat() integration with mocked httpx ──────────────────────────────────


@pytest.mark.unit
def test_chat_posts_to_api_chat_with_bearer_auth():
    """End-to-end chat() with httpx mocked — verifies URL, Authorization
    header, body shape, and response parsing."""
    p = OllamaProvider(api_key="ok-key", model="deepseek-v4-flash")

    captured: dict = {}

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "model": "deepseek-v4-flash",
        "done": True,
        "done_reason": "stop",
        "message": {"role": "assistant", "content": "pong"},
    }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        captured["content"] = kwargs.get("content")
        return fake_response

    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.post = fake_post

    with patch("app.llm.ollama_provider.httpx.Client", return_value=fake_client):
        r = p.chat(
            [{"role": "user", "content": "ping"}],
            system="be terse",
            tools=[{"name": "x", "description": "d", "input_schema": {"type": "object"}}],
            max_tokens=128,
        )

    assert captured["url"] == f"{DEFAULT_BASE_URL}/api/chat"
    assert captured["headers"]["Authorization"] == "Bearer ok-key"
    sent = json.loads(captured["content"])
    assert sent["model"] == "deepseek-v4-flash"
    assert sent["stream"] is False
    assert sent["options"] == {"temperature": 0, "num_predict": 128}
    assert sent["messages"][0] == {"role": "system", "content": "be terse"}
    assert sent["messages"][1] == {"role": "user", "content": "ping"}
    assert sent["tools"][0]["function"]["name"] == "x"

    assert r.content == "pong"
    assert r.stop_reason == "end_turn"


@pytest.mark.unit
def test_chat_uses_override_base_url():
    """Constructor base_url override is honored (e.g. local Ollama daemon)."""
    p = OllamaProvider(api_key="ok-key", model="m", base_url="http://localhost:11434/")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"message": {"content": ""}, "done_reason": "stop"}

    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    seen_url: dict[str, str] = {}

    def fake_post(url, **kwargs):
        seen_url["u"] = url
        return fake_response

    fake_client.post = fake_post
    with patch("app.llm.ollama_provider.httpx.Client", return_value=fake_client):
        p.chat([{"role": "user", "content": "hi"}])
    # Trailing slash on base_url is stripped before /api/chat is appended.
    assert seen_url["u"] == "http://localhost:11434/api/chat"


@pytest.mark.unit
def test_constructor_rejects_empty_api_key():
    with pytest.raises(ValueError, match="api_key"):
        OllamaProvider(api_key="", model="x")


@pytest.mark.unit
def test_model_name_returns_configured_model():
    p = OllamaProvider(api_key="ok", model="deepseek-v4-pro")
    assert p.model_name() == "deepseek-v4-pro"


# ── LLMClient.from_settings integration ───────────────────────────────────


@pytest.mark.unit
def test_llmclient_from_settings_picks_ollama_branch(monkeypatch):
    """The new "ollama" provider value routes from_settings to OllamaProvider."""
    from app.config import settings
    from app.llm.client import LLMClient

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_api_key", "ok-key")
    monkeypatch.setattr(settings, "llm_model", "deepseek-v4-flash")
    monkeypatch.setattr(settings, "llm_base_url", "")

    client = LLMClient.from_settings()
    assert client.model == "deepseek-v4-flash"


@pytest.mark.unit
def test_llmclient_from_settings_unknown_provider_raises(monkeypatch):
    from app.config import settings
    from app.llm.client import LLMClient

    monkeypatch.setattr(settings, "llm_provider", "bogus-provider")
    monkeypatch.setattr(settings, "llm_api_key", "x")
    monkeypatch.setattr(settings, "llm_model", "x")
    monkeypatch.setattr(settings, "llm_base_url", "")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        LLMClient.from_settings()
