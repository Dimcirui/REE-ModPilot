"""
Ollama Cloud provider adapter (issue #9 follow-up).

Ollama Cloud is NOT OpenAI-compatible at /api/chat — its wire format is:

  Request body  : {"model", "messages", "tools"?, "stream", "options"}
  Response body : {"model", "done", "message": {"role", "content", "tool_calls"?}, ...}

Where each tool_call entry is `{"function": {"name": str, "arguments": dict}}`
WITHOUT a server-side id — we synthesize a client-side uuid so the agent
loop can pair tool_use blocks with tool_result blocks across turns.

Auth: Bearer token via the `Authorization` header.

Why not the `ollama` Python SDK: it targets the local Ollama daemon's
streaming protocol and pulls in another dependency we don't otherwise need.
httpx is already in the FastAPI dep tree.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from app.llm.client import BaseProvider, LLMResponse, Message, Tool

DEFAULT_BASE_URL = "https://ollama.com"


class OllamaProvider(BaseProvider):
    """
    Wraps Ollama Cloud's POST /api/chat endpoint.

    Args:
        api_key:    OLLAMA_API_KEY (Authorization: Bearer <key>).
        model:      Model id, e.g. "deepseek-v4-pro" or "deepseek-v4-flash".
        base_url:   Override base URL. Defaults to https://ollama.com.
        timeout:    Per-request timeout in seconds. Long because chain-of-thought
                    models can take a while.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str | None = DEFAULT_BASE_URL,
        timeout: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("Ollama Cloud requires an api_key.")
        self._api_key = api_key
        self._model = model
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout = timeout

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
        body: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "messages": _to_ollama_messages(messages, system=system),
            "options": {
                "temperature": 0,
                "num_predict": max_tokens,
            },
        }
        if tools:
            body["tools"] = _to_ollama_tools(tools)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                f"{self._base_url}/api/chat",
                headers=headers,
                content=json.dumps(body).encode("utf-8"),
            )
            response.raise_for_status()
            return _parse_response(response.json())


# ── message translation ───────────────────────────────────────────────────


def _to_ollama_messages(messages: list[Message], *, system: str) -> list[dict]:
    """Translate Anthropic-style messages into Ollama's flat format.

    Inbound shapes we must handle:
      {"role": "user", "content": "string"}                                 # plain text
      {"role": "user", "content": [tool_result_blocks...]}                  # tool replies
      {"role": "assistant", "content": "string"}                            # plain reply
      {"role": "assistant", "content": [text/tool_use blocks...]}           # mixed reply

    Outbound shapes Ollama accepts:
      {"role": "system" | "user" | "assistant" | "tool", "content": str,
       "tool_calls"?: [{"function": {"name", "arguments"}}]}

    Tool-call ids are NOT included in the assistant message — Ollama doesn't
    persist them. tool messages are matched positionally by Ollama Cloud.
    """
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            out.append({"role": role, "content": str(content)})
            continue

        # Anthropic-style block list. Split into text + tool_use / tool_result.
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []

        for blk in content:
            if not isinstance(blk, dict):
                text_parts.append(str(blk))
                continue
            btype = blk.get("type", "")
            if btype == "text":
                text_parts.append(blk.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append({
                    "function": {
                        "name": blk.get("name", ""),
                        "arguments": blk.get("input", {}) or {},
                    },
                })
            elif btype == "tool_result":
                # Ollama expects tool results as standalone {"role": "tool", ...} messages.
                raw_content = blk.get("content", "")
                if isinstance(raw_content, list):
                    raw_content = " ".join(
                        c.get("text", "") if isinstance(c, dict) else str(c)
                        for c in raw_content
                    )
                tool_results.append({"role": "tool", "content": str(raw_content)})
            # Unknown block types (e.g. "thinking") are dropped — Ollama doesn't
            # round-trip them and they would otherwise surface as parse errors.

        if role == "assistant":
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts)}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            out.append(assistant_msg)
        elif role == "user":
            # tool_results sit inside what Anthropic calls a user message; emit
            # them as separate "tool" messages so Ollama can match them.
            if tool_results:
                out.extend(tool_results)
            if text_parts:
                out.append({"role": "user", "content": "\n".join(text_parts)})
        else:
            out.append({"role": role, "content": "\n".join(text_parts)})

    return out


def _to_ollama_tools(tools: list[Tool]) -> list[dict]:
    """Convert canonical Tool entries to Ollama's `tools` schema (OpenAI-shape)."""
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


# ── response parsing ──────────────────────────────────────────────────────


def _parse_response(payload: dict) -> LLMResponse:
    """Extract content + tool_calls from an Ollama /api/chat response.

    Tool-call ids are synthesized client-side because Ollama doesn't return
    them — the agent loop pairs tool_use ↔ tool_result via these ids, so
    they must be unique within a single chat() turn.
    """
    msg = payload.get("message") or {}
    text_content: str = msg.get("content", "") or ""

    tool_calls: list[dict] = []
    raw_tool_calls = msg.get("tool_calls") or []
    for tc in raw_tool_calls:
        fn = (tc or {}).get("function") or {}
        name = fn.get("name", "")
        args = fn.get("arguments", {})
        # Sometimes models return arguments as a JSON-encoded string instead of
        # an object. Both shapes are valid per Ollama's docs; normalize here.
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        tool_calls.append({
            "name": name,
            "id": f"tc_{uuid.uuid4().hex[:12]}",
            "input": args if isinstance(args, dict) else {"value": args},
        })

    # Map Ollama's done_reason → our normalized stop_reason vocabulary.
    done_reason = (payload.get("done_reason") or "").lower()
    stop_reason_map = {
        "stop":          "end_turn",
        "tool_calls":    "tool_use",
        "tool_use":      "tool_use",
        "length":        "max_tokens",
        "load":          "end_turn",
    }
    stop_reason = stop_reason_map.get(done_reason, "end_turn")
    if tool_calls and stop_reason == "end_turn":
        stop_reason = "tool_use"

    return LLMResponse(
        content=text_content,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        raw=payload,
    )
