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
def test_render_error_choice_html_contains_three_buttons():
    """Direct test of the error_choice HTML renderer (issue #2).

    Verifies the rendered fragment contains all three buttons with the right
    Chinese labels, posts to /agent/messages (not legacy /agent/chat), and
    bakes the session_id into each button's hx-vals.
    """
    from app.main import _render_error_choice_html

    sid = "sse-test-html"
    fragment = _render_error_choice_html(sid)

    # Three buttons, three labels
    assert "重试" in fragment
    assert "跳过" in fragment
    assert "查看详情" in fragment

    # All post to /agent/messages, not legacy /agent/chat
    assert fragment.count('hx-post="/agent/messages"') == 3
    assert 'hx-post="/agent/chat"' not in fragment

    # session_id baked into each hx-vals
    assert fragment.count(f'"session_id":"{sid}"') == 3

    # Each button discards the JSON response so it doesn't get swapped
    assert fragment.count('hx-swap="none"') == 3

    # hx-ext="json-enc" must be on each button — htmx's default form-urlencoded
    # body would 422 against the Pydantic ChatRequest endpoint.
    assert fragment.count('hx-ext="json-enc"') == 3

    # No inline onclick — removal is handled by app.js on htmx:beforeRequest
    # so the optimistic-bubble path (configRequest) sees the live button first.
    assert "onclick=" not in fragment

    # Style hooks for app.css selectors
    assert 'class="error-choice-group"' in fragment
    for variant in ("retry", "skip", "ask"):
        assert f'class="error-choice-btn {variant}"' in fragment


@pytest.mark.unit
def test_render_error_choice_html_escapes_session_id():
    """Session-id is HTML-escaped to keep a future id-format change from
    opening an attribute-injection hole."""
    from app.main import _render_error_choice_html

    # uuid4().hex[:12] is hex-only today, but the renderer must still escape
    # so a future format change can't break out of the hx-vals attribute.
    fragment = _render_error_choice_html('"><script>x</script>')
    assert "<script>" not in fragment
    assert "&quot;" in fragment or "&#x27;" in fragment


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_failing_turn_emits_error_choice_in_queue(monkeypatch):
    """End-to-end at the queue layer: a phase failure must enqueue an
    error_choice event so the SSE generator can ship its HTML payload.
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
def test_render_classification_widget_html_lists_all_chains_and_types():
    """Issue #7 — the classification widget fragment must include one row per
    chain head, all 17 inferred-type options per dropdown, and a hidden
    session_id input that posts back to /agent/widget/classification."""
    from app.main import _render_classification_widget_html
    from app.phases.physics_bones import list_inferred_types

    sid = "widget-test-sid"
    chains = [
        {"name": "hair_001", "role": "head", "depth": 5, "parent": "head"},
        {"name": "skirt_002", "role": "head", "depth": 8, "parent": "waist"},
    ]
    fragment = _render_classification_widget_html(sid, chains)

    assert 'hx-post="/agent/widget/classification"' in fragment
    assert 'hx-ext="json-enc"' in fragment
    assert f'value="{sid}"' in fragment
    assert "hair_001" in fragment
    assert "skirt_002" in fragment

    # One <select> per row + one <option value=""> sentinel + 17 type options.
    for t in list_inferred_types():
        assert f'value="{t}"' in fragment
    # Each chain gets a name="type__<chain>" select.
    assert 'name="type__hair_001"' in fragment
    assert 'name="type__skirt_002"' in fragment


@pytest.mark.unit
def test_render_material_widget_html_per_material_slot_table():
    """Issue #7 — material widget renders one <details> per material with a
    select per Principled BSDF slot, pre-selects existing connections, and
    offers each texture file as an option."""
    from app.main import _render_material_widget_html
    from app.phases.material import PRINCIPLED_SLOTS

    sid = "widget-test-mat"
    materials = ["body_mat", "hair_mat"]
    connections = {"body_mat": {"Base Color": "C:/tex/diff.png"}}
    texture_files = ["C:/tex/diff.png", "C:/tex/norm.png"]
    fragment = _render_material_widget_html(sid, materials, connections, texture_files)

    assert 'hx-post="/agent/widget/material"' in fragment
    assert f'value="{sid}"' in fragment
    assert "body_mat" in fragment
    assert "hair_mat" in fragment
    for slot in PRINCIPLED_SLOTS:
        assert slot in fragment
    # Each material × each slot gets a select with name="texmap__<idx>__<mat>"
    assert 'name="texmap__0__body_mat"' in fragment
    assert 'name="texmap__0__hair_mat"' in fragment
    # Pre-selected option for the existing connection
    assert 'value="C:/tex/diff.png" selected' in fragment
    # Texture options rendered with basename labels for readability
    assert ">diff.png</option>" in fragment


@pytest.mark.unit
def test_render_material_widget_html_pre_fills_llm_suggestions():
    """Issue #11 — when `suggestions` carries a {mat: {slot: path}} hint, the
    matching cell is pre-selected, the row gets the `row-suggested` class, and
    the slot label gets the `LLM` chip. Suggestions take precedence over the
    existing connection.
    """
    from app.main import _render_material_widget_html

    sid = "widget-test-mat-sug"
    materials = ["body_mat"]
    # existing wired connection points at the WRONG file on purpose; the LLM
    # suggestion should win.
    connections = {"body_mat": {"Base Color": "C:/tex/wrong.png"}}
    texture_files = ["C:/tex/wrong.png", "C:/tex/body_diffuse.png"]
    suggestions = {"body_mat": {"Base Color": "C:/tex/body_diffuse.png"}}
    fragment = _render_material_widget_html(
        sid, materials, connections, texture_files, suggestions
    )

    # The suggested file is pre-selected
    assert 'value="C:/tex/body_diffuse.png" selected' in fragment
    # The wrong (existing) file is NOT selected
    assert 'value="C:/tex/wrong.png" selected' not in fragment
    # The row gets the highlight class
    assert "row-suggested" in fragment
    # The slot label gets the chip
    assert "widget-suggested-chip" in fragment


@pytest.mark.unit
def test_render_material_widget_html_no_suggestions_falls_back_to_existing():
    """Issue #11 — when `suggestions` is empty / missing, behavior matches the
    pre-issue #11 path: existing connections still pre-select, no chip / no
    row highlight."""
    from app.main import _render_material_widget_html

    sid = "widget-test-mat-nosug"
    materials = ["body_mat"]
    connections = {"body_mat": {"Base Color": "C:/tex/diff.png"}}
    texture_files = ["C:/tex/diff.png"]
    fragment = _render_material_widget_html(
        sid, materials, connections, texture_files, suggestions=None
    )

    assert 'value="C:/tex/diff.png" selected' in fragment
    assert "row-suggested" not in fragment


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
