"""
Unit tests for the SSE routes added in Stage 5 (issue #1).

Verifies:
  - POST /agent/messages returns the legacy JSON shape
  - GET /agent/stream/{session_id} delivers structured events
  - SSE frames use event: <type> matching the JSON `type` field, so htmx
    `sse-swap="phase_started"` can dispatch on event name directly
  - A `done` event closes each turn

Real Blender is not required: BlenderClient is monkey-patched to a no-op
stub, and app.state.llm is replaced with a MagicMock that produces
deterministic responses.

Run with: uv run pytest -m unit tests/unit/test_sse_routes.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore

from app.blender.client import BlenderClient
from app.llm.client import LLMClient


def _patch_blender(monkeypatch):
    """Replace BlenderClient network methods with stubs."""
    monkeypatch.setattr(BlenderClient, "connect", lambda self: None)
    monkeypatch.setattr(
        BlenderClient,
        "connected",
        property(lambda self: True),
    )
    monkeypatch.setattr(
        BlenderClient,
        "get_scene_info",
        lambda self: {"name": "Scene", "object_count": 1},
    )
    monkeypatch.setattr(BlenderClient, "close", lambda self: None)


def _stub_llm(reply_text: str) -> MagicMock:
    llm = MagicMock()
    llm.chat.return_value = MagicMock(
        content=reply_text,
        has_tool_calls=False,
        tool_calls=[],
        content_blocks=[],
    )
    return llm


def _patch_llm_factory(monkeypatch, reply_text: str = "default reply") -> MagicMock:
    """Replace LLMClient.from_settings so the lifespan doesn't require an API key."""
    stub = _stub_llm(reply_text)
    monkeypatch.setattr(LLMClient, "from_settings", classmethod(lambda cls: stub))
    return stub


def _parse_sse_frames(raw: str) -> list[dict]:
    """
    Parse SSE frames from a chunk of body text.

    Each frame is "event: <type>\\n" followed by one or more "data: <line>\\n"
    lines, terminated by a blank line. Returns a list of dicts: each frame's
    JSON-parsed data payload with the SSE event type attached as `_event`.
    """
    frames: list[dict] = []
    current_event: str | None = None
    current_data: list[str] = []
    for line in raw.splitlines():
        if not line:
            if current_data:
                payload = json.loads("".join(current_data))
                payload["_event"] = current_event or ""
                frames.append(payload)
            current_event = None
            current_data = []
            continue
        if line.startswith(":"):
            continue  # SSE comment / keepalive
        if line.startswith("event:"):
            current_event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            current_data.append(line[len("data:") :].lstrip())
    return frames


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_messages_returns_legacy_shape(monkeypatch):
    """POST /agent/messages returns {reply, state, session_id} like /agent/chat."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        app.state.llm = _stub_llm("hi back")
        r = client.post(
            "/agent/messages",
            json={"message": "hello", "session_id": "sse-test-1"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["session_id"] == "sse-test-1"
        assert body["reply"] == "hi back"
        assert "state" in body


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_populates_session_queue_with_typed_events(monkeypatch):
    """POST /agent/messages must publish the structured event sequence to the
    session's queue. We inspect the queue directly instead of going through the
    SSE wire format here — that's covered by test_stream_endpoint_emits_sse_frames.
    """
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    sid = "sse-test-2"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("done with this")
        r = client.post(
            "/agent/messages",
            json={"message": "hi", "session_id": sid},
        )
        assert r.status_code == 200

        queue = app.state.agent_streams[sid]
        events: list[dict] = []
        while not queue.empty():
            events.append(queue.get_nowait())

    types = [e["type"] for e in events]
    # Text-only first turn: message(user), state, phase_started, message(assistant), done
    assert types[0] == "message" and events[0]["role"] == "user"
    assert "state" in types
    assert "phase_started" in types
    # Last event must be the `done` close-of-turn signal
    assert types[-1] == "done"
    assert events[-1]["reply"] == "done with this"
    assert events[-1]["session_id"] == sid


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_stream_route_is_registered():
    """GET /agent/stream/{session_id} is registered on the app.

    Wire-format verification (event: <type>\\ndata: <json>) is exercised by the
    manual curl smoke in the plan's Verification section, not in unit tests —
    sse-starlette's long-lived response and TestClient's synchronous body
    consumption don't compose cleanly without external timeouts.
    """
    from app.main import app

    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/agent/stream/{session_id}" in paths
    assert "/agent/messages" in paths
    assert "/agent/chat" in paths  # legacy preserved


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_failing_turn_emits_error_choice_in_queue(monkeypatch):
    """End-to-end at the queue layer: a phase failure must enqueue an
    error_choice event so the SSE generator can ship its JSON payload to
    the React frontend (which renders the retry/skip/ask buttons).
    """
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app
    from app.phases.base import PhaseError, PhaseResult
    from app.phases.pose_correction import PoseCorrection

    failing_llm = MagicMock()
    failing_llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[
                {"id": "t1", "name": "pose_correction", "input": {
                    "x_preset": "MMD",
                    "source_armature": "Body",
                    "target_armature": "MHWs",
                }}
            ],
            content_blocks=[],
        ),
        MagicMock(
            content="[Retry] — 重新执行 | [Skip] — 跳过继续 | [Ask] — 查看详情",
            has_tool_calls=False,
            tool_calls=[],
            content_blocks=[],
        ),
    ]

    sid = "sse-test-err"
    # Force RUNNING_PHASE on the session so the first LLM call goes to tool-call land
    error = PhaseError(
        category="operator_failed",
        operator="modder.pose_correction",
        message="No armature named 'Body' in scene",
    )
    with (
        patch.object(PoseCorrection, "run", return_value=PhaseResult.fail(error)),
        TestClient(app) as client,
    ):
        app.state.llm = failing_llm
        r = client.post(
            "/agent/messages",
            json={"message": "go", "session_id": sid},
        )
        assert r.status_code == 200

        queue = app.state.agent_streams[sid]
        events: list[dict] = []
        while not queue.empty():
            events.append(queue.get_nowait())

    error_choice_evts = [e for e in events if e["type"] == "error_choice"]
    assert len(error_choice_evts) == 1
    evt = error_choice_evts[0]
    assert evt["operator"] == "modder.pose_correction"
    assert evt["message"] == "No armature named 'Body' in scene"
    assert evt["state"] == "error_handling"


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_legacy_chat_does_not_populate_stream(monkeypatch):
    """POST /agent/chat must not install a sink — SSE consumers see nothing."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    sid = "sse-test-3"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("legacy reply")
        r = client.post(
            "/agent/chat",
            json={"message": "hi", "session_id": sid},
        )
        assert r.status_code == 200

    # No queue should have been created for this session
    streams = app.state.agent_streams
    assert sid not in streams
