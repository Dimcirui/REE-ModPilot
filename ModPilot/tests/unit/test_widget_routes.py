"""
Unit tests for the confirmation widget POST routes (issue #7).

Covers the structured-JSON shape introduced by the React migration:
  - POST /agent/widget/classification accepts a list of
    {chain_name, inferred_type, description, merge_to_parent} and packs them
    into a [CONFIRMED_CLASSIFICATIONS]-prefixed JSON message for loop.step().
  - POST /agent/widget/material accepts a list of {material, slot,
    texture_path} and packs them into [CONFIRMED_MATERIAL_MAPPING] for
    loop.step(). Empty texture paths and unknown slots are dropped.

Real Blender is not required: BlenderClient is monkey-patched to a no-op
stub and LLMClient.from_settings is replaced with a MagicMock factory.

Run with: uv run pytest -m unit tests/unit/test_widget_routes.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore

from app.blender.client import BlenderClient
from app.llm.client import LLMClient


def _patch_blender(monkeypatch):
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


def _stub_llm(reply_text: str = "ok") -> MagicMock:
    llm = MagicMock()
    llm.chat.return_value = MagicMock(
        content=reply_text,
        has_tool_calls=False,
        tool_calls=[],
        content_blocks=[],
    )
    return llm


def _patch_llm_factory(monkeypatch, reply_text: str = "ok") -> MagicMock:
    stub = _stub_llm(reply_text)
    monkeypatch.setattr(LLMClient, "from_settings", classmethod(lambda cls: stub))
    return stub


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_classification_packages_confirmations_and_calls_step(monkeypatch):
    """A structured confirmations[] payload becomes a JSON dict on a
    [CONFIRMED_CLASSIFICATIONS] prefix that loop.step() receives as the next
    user message.
    """
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    sid = "widget-cls-1"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("got it")
        r = client.post(
            "/agent/widget/classification",
            json={
                "session_id": sid,
                "confirmations": [
                    {
                        "chain_name": "hair_001",
                        "inferred_type": "hair_long_straight",
                        "description": "",
                        "merge_to_parent": False,
                    },
                    {
                        "chain_name": "skirt_002",
                        "inferred_type": "cloth_skirt_waist",
                        "description": "pleated front panel",
                        "merge_to_parent": True,
                    },
                ],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"saved": True, "count": 2}

        loop = app.state.agent_sessions[sid]
        user_msgs = [m["content"] for m in loop._global_history if m["role"] == "user"]
        assert user_msgs, "loop.step never appended a user message"
        last = user_msgs[-1]
        assert last.startswith("[CONFIRMED_CLASSIFICATIONS] ")
        body = json.loads(last[len("[CONFIRMED_CLASSIFICATIONS] "):])
        assert body == {
            "inferred_types": {
                "hair_001": "hair_long_straight",
                "skirt_002": "cloth_skirt_waist",
            },
            "descriptions": {"skirt_002": "pleated front panel"},
            "bones_to_merge": ["skirt_002"],
        }


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_classification_skips_blank_rows(monkeypatch):
    """Rows with empty inferred_type are dropped silently — the FE may emit
    placeholder rows the user opted out of."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    sid = "widget-cls-blank"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("got it")
        r = client.post(
            "/agent/widget/classification",
            json={
                "session_id": sid,
                "confirmations": [
                    {"chain_name": "hair_001", "inferred_type": "hair_short"},
                    {"chain_name": "skirt_002", "inferred_type": ""},
                ],
            },
        )
        assert r.status_code == 200
        assert r.json()["count"] == 1


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_classification_rejects_empty_submission(monkeypatch):
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    sid = "widget-cls-empty"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("got it")
        r = client.post(
            "/agent/widget/classification",
            json={"session_id": sid, "confirmations": []},
        )
        assert r.status_code == 422


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_material_packages_nested_mapping(monkeypatch):
    """mappings[] becomes {mat: {slot_name: path}}."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app
    from app.phases.material import PRINCIPLED_SLOTS

    sid = "widget-mat-1"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("got it")
        r = client.post(
            "/agent/widget/material",
            json={
                "session_id": sid,
                "mappings": [
                    {
                        "material": "body_mat",
                        "slot": PRINCIPLED_SLOTS[0],  # "Base Color"
                        "texture_path": "C:/tex/body_diff.png",
                    },
                    {
                        "material": "body_mat",
                        "slot": PRINCIPLED_SLOTS[5],  # "Normal"
                        "texture_path": "C:/tex/body_norm.png",
                    },
                    {
                        "material": "hair_mat",
                        "slot": PRINCIPLED_SLOTS[0],
                        "texture_path": "C:/tex/hair_diff.png",
                    },
                ],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"saved": True, "materials": 2}

        loop = app.state.agent_sessions[sid]
        user_msgs = [m["content"] for m in loop._global_history if m["role"] == "user"]
        last = user_msgs[-1]
        assert last.startswith("[CONFIRMED_MATERIAL_MAPPING] ")
        body = json.loads(last[len("[CONFIRMED_MATERIAL_MAPPING] "):])
        assert body == {
            "body_mat": {
                PRINCIPLED_SLOTS[0]: "C:/tex/body_diff.png",
                PRINCIPLED_SLOTS[5]: "C:/tex/body_norm.png",
            },
            "hair_mat": {PRINCIPLED_SLOTS[0]: "C:/tex/hair_diff.png"},
        }


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_material_drops_invalid_slots_but_preserves_user_clears(monkeypatch):
    """Unknown slot names are dropped silently; empty texture paths for
    valid Principled slots are PRESERVED (issue #19) — they're an explicit
    user-clear signal that the agent must not re-infer."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    sid = "widget-mat-bogus"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("got it")
        r = client.post(
            "/agent/widget/material",
            json={
                "session_id": sid,
                "mappings": [
                    {"material": "body_mat", "slot": "Base Color", "texture_path": "C:/tex/ok.png"},
                    {"material": "body_mat", "slot": "Not A Slot", "texture_path": "C:/tex/dropped.png"},
                    {"material": "body_mat", "slot": "Normal", "texture_path": ""},
                ],
            },
        )
        assert r.status_code == 200
        loop = app.state.agent_sessions[sid]
        last = [m["content"] for m in loop._global_history if m["role"] == "user"][-1]
        body = json.loads(last[len("[CONFIRMED_MATERIAL_MAPPING] "):])
        # Bogus slot dropped; Normal preserved with empty path (user cleared it).
        assert body == {"body_mat": {"Base Color": "C:/tex/ok.png", "Normal": ""}}


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_material_rejects_all_empty_submission(monkeypatch):
    """If every row has an empty path, the user effectively confirmed
    nothing — 422 (issue #19 regression guard)."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    sid = "widget-mat-all-cleared"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("got it")
        r = client.post(
            "/agent/widget/material",
            json={
                "session_id": sid,
                "mappings": [
                    {"material": "body_mat", "slot": "Base Color", "texture_path": ""},
                    {"material": "body_mat", "slot": "Normal", "texture_path": ""},
                ],
            },
        )
        assert r.status_code == 422


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_material_rejects_empty_submission(monkeypatch):
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    sid = "widget-mat-empty"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("got it")
        r = client.post(
            "/agent/widget/material",
            json={"session_id": sid, "mappings": []},
        )
        assert r.status_code == 422


# ── done emit regression (Issue A: widget routes were missing it) ─────────


def _drain_queue(queue) -> list[dict]:
    """Pull every queued event out of the asyncio.Queue synchronously
    (test-only — production uses an async consumer)."""
    out: list[dict] = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_classification_emits_done_event(monkeypatch):
    """Regression: widget Confirm route used to call loop.step() without
    emitting a `done` event afterwards.  Without `done`, the frontend's
    chat-input lockout stayed engaged forever ('thinking' status stuck).
    The /agent/messages route already had done emit; widget routes did not.
    """
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    sid = "widget-cls-done"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("ok")
        import asyncio
        app.state.agent_streams[sid] = asyncio.Queue(maxsize=64)
        r = client.post(
            "/agent/widget/classification",
            json={
                "session_id": sid,
                "confirmations": [
                    {"chain_name": "hair_001", "inferred_type": "hair_short"},
                ],
            },
        )
        assert r.status_code == 200
        events = _drain_queue(app.state.agent_streams[sid])
        types = [e.get("type") for e in events]
        assert "done" in types, f"expected `done` event among {types}"
        assert types[-1] == "done"


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_material_emits_done_event(monkeypatch):
    """Same regression as above, for the material widget route."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    sid = "widget-mat-done"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("ok")
        import asyncio
        app.state.agent_streams[sid] = asyncio.Queue(maxsize=64)
        r = client.post(
            "/agent/widget/material",
            json={
                "session_id": sid,
                "mappings": [
                    {
                        "material": "body_mat",
                        "slot": "Base Color",
                        "texture_path": "C:/tex/diff.png",
                    },
                ],
            },
        )
        assert r.status_code == 200
        events = _drain_queue(app.state.agent_streams[sid])
        types = [e.get("type") for e in events]
        assert "done" in types
        assert types[-1] == "done"
