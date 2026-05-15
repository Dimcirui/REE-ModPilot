"""
Unit tests for the confirmation widget POST routes (issue #7).

Covers:
  - POST /agent/widget/classification packages `type__<chain>` form fields
    as a [CONFIRMED_CLASSIFICATIONS]-prefixed JSON message and feeds it to
    loop.step(); blank rows are ignored; an empty submission 422s.
  - POST /agent/widget/material packages `texmap__<slot_idx>__<mat>` form
    fields as a [CONFIRMED_MATERIAL_MAPPING]-prefixed JSON message; bogus
    slot_idx values are silently dropped; empty submissions 422.

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
def test_widget_classification_packages_pairs_and_calls_step(monkeypatch):
    """type__<chain> fields become a JSON dict on a [CONFIRMED_CLASSIFICATIONS]
    prefix that loop.step() receives as the next user message."""
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
                "type__hair_001": "hair_long_straight",
                "type__skirt_002": "cloth_skirt_waist",
            },
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload == {"saved": True, "count": 2}

        loop = app.state.agent_sessions[sid]
        # loop.step appended the user message to global history
        user_msgs = [m["content"] for m in loop._global_history if m["role"] == "user"]
        assert user_msgs, "loop.step never appended a user message"
        last = user_msgs[-1]
        assert last.startswith("[CONFIRMED_CLASSIFICATIONS] ")
        body = json.loads(last[len("[CONFIRMED_CLASSIFICATIONS] "):])
        assert body == {
            "hair_001": "hair_long_straight",
            "skirt_002": "cloth_skirt_waist",
        }


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_classification_skips_blank_rows(monkeypatch):
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
                "type__hair_001": "hair_short",
                "type__skirt_002": "",
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
            json={"session_id": sid},
        )
        assert r.status_code == 422


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_widget_material_packages_nested_mapping(monkeypatch):
    """texmap__<slot_idx>__<mat> fields become {mat: {slot_name: path}}."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app
    from app.phases.material import PRINCIPLED_SLOTS

    sid = "widget-mat-1"
    with TestClient(app) as client:
        app.state.llm = _stub_llm("got it")
        # slot 0 == "Base Color", slot 5 == "Normal"
        r = client.post(
            "/agent/widget/material",
            json={
                "session_id": sid,
                "texmap__0__body_mat": "C:/tex/body_diff.png",
                "texmap__5__body_mat": "C:/tex/body_norm.png",
                "texmap__0__hair_mat": "C:/tex/hair_diff.png",
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
def test_widget_material_drops_invalid_slot_indices(monkeypatch):
    """Out-of-range or non-numeric slot_idx values are silently dropped."""
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
                "texmap__0__body_mat": "C:/tex/ok.png",
                "texmap__99__body_mat": "C:/tex/dropped.png",
                "texmap__abc__body_mat": "C:/tex/also_dropped.png",
            },
        )
        assert r.status_code == 200
        loop = app.state.agent_sessions[sid]
        last = [m["content"] for m in loop._global_history if m["role"] == "user"][-1]
        body = json.loads(last[len("[CONFIRMED_MATERIAL_MAPPING] "):])
        # Only the valid slot survives.
        assert body == {"body_mat": {"Base Color": "C:/tex/ok.png"}}


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
            json={"session_id": sid},
        )
        assert r.status_code == 422
