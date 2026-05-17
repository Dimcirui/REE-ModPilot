"""
Wiring tests for issue #4: the /app/x_presets route, the relaxed
SessionConfig.model_type validation, and the AgentLoop's emission of the
`model_type_inferred` SSE event when InferModelType succeeds.

These exercise the FastAPI + AgentLoop layer, not the phase tool's own
logic (covered in test_infer_model_type.py).

Run with: uv run pytest -m unit tests/unit/test_infer_model_type_routing.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore

from app.blender.client import BlenderClient
from app.blender.preset_catalog import PresetMeta
from app.llm.client import LLMClient
from app.phases.base import X_PRESETS, update_x_presets


def _patch_blender(monkeypatch):
    monkeypatch.setattr(BlenderClient, "connect", lambda self: None)
    monkeypatch.setattr(BlenderClient, "connected", property(lambda self: True))
    monkeypatch.setattr(BlenderClient, "close", lambda self: None)


def _patch_llm(monkeypatch):
    monkeypatch.setattr(LLMClient, "from_settings", classmethod(lambda cls: MagicMock()))


# ── /app/x_presets ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_x_presets_returns_catalog_when_present(monkeypatch):
    """When the lifespan populated app.state.x_preset_catalog, the route
    surfaces the live data including slot counts."""
    _patch_blender(monkeypatch)
    _patch_llm(monkeypatch)

    from app.main import app

    with TestClient(app) as client:
        # Inject a fake catalog onto app.state to exercise the populated path.
        app.state.x_preset_catalog = {
            "FixtureA": PresetMeta(
                name="FixtureA",
                path=Path("a.json"),
                mappings={"s1": {"main": ["X"]}, "s2": {"main": ["Y"]}},
                exclude=[],
                description="fixture desc",
            ),
        }
        resp = client.get("/app/x_presets")
    assert resp.status_code == 200
    body = resp.json()
    names = [p["name"] for p in body["presets"]]
    assert "FixtureA" in names
    entry = next(p for p in body["presets"] if p["name"] == "FixtureA")
    assert entry["slot_count"] == 2
    assert entry["description"] == "fixture desc"


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_x_presets_falls_back_to_shipped_when_catalog_empty(monkeypatch):
    """No live catalog (e.g. Blender unreachable at boot) → route returns
    the shipped-preset names so the dropdown still populates."""
    _patch_blender(monkeypatch)
    _patch_llm(monkeypatch)

    from app.main import app

    with TestClient(app) as client:
        app.state.x_preset_catalog = {}
        resp = client.get("/app/x_presets")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()["presets"]}
    assert "MMD" in names
    assert "VRChat" in names


# ── SessionConfig.model_type relaxed validation ───────────────────────────


def _valid_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Make three on-disk paths that pass the route's existence checks."""
    f = tmp_path / "model.fbx"
    f.write_text("")
    td = tmp_path / "tex"
    td.mkdir()
    mod = tmp_path / "mod"
    mod.mkdir()
    return f, td, mod


def _post_config(client, *, session_id: str, paths, model_type: str):
    f, td, mod = paths
    return client.post(
        "/agent/config",
        json={
            "session_id": session_id,
            "config": {
                "model_path": str(f),
                "model_type": model_type,
                "texture_dir": str(td),
                "mod_root": str(mod),
                "author": "test",
                "character_name": "Demo",
                "use_bone_system": False,
                "body_parts": ["1"],
            },
        },
    )


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_session_config_accepts_auto_detect(monkeypatch, tmp_path):
    _patch_blender(monkeypatch)
    _patch_llm(monkeypatch)
    paths = _valid_paths(tmp_path)

    from app.main import app

    with TestClient(app) as client:
        resp = _post_config(client, session_id="s1", paths=paths, model_type="Auto-detect")
    assert resp.status_code == 200
    assert resp.json()["saved"] is True


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_session_config_accepts_runtime_preset(monkeypatch, tmp_path):
    """Any name in the runtime X_PRESETS set is accepted — no Literal lock-in."""
    _patch_blender(monkeypatch)
    _patch_llm(monkeypatch)
    paths = _valid_paths(tmp_path)
    update_x_presets({"MMD", "VRChat", "怪猎荒野"})  # simulate post-startup state

    from app.main import app

    with TestClient(app) as client:
        resp = _post_config(client, session_id="s2", paths=paths, model_type="怪猎荒野")
    assert resp.status_code == 200


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_session_config_rejects_unknown_preset(monkeypatch, tmp_path):
    _patch_blender(monkeypatch)
    _patch_llm(monkeypatch)
    paths = _valid_paths(tmp_path)
    update_x_presets({"MMD", "VRChat"})

    from app.main import app

    with TestClient(app) as client:
        resp = _post_config(client, session_id="s3", paths=paths, model_type="MadeUpPreset")
    assert resp.status_code == 422
    field_errors = resp.json()["detail"]["field_errors"]
    assert "model_type" in field_errors


# ── AgentLoop emits model_type_inferred ───────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_widget_if_inspector_emits_model_type_inferred():
    """Direct unit test of the inspector hook: a state_diff carrying an
    `inferred_preset` + `decision` should produce a `model_type_inferred`
    event without touching Blender or the LLM."""
    from app.agent.loop import AgentLoop

    events: list[dict] = []

    loop = AgentLoop.__new__(AgentLoop)  # bypass __init__ — only exercising one method
    loop._event_sink = lambda e: events.append(e)
    loop.state = MagicMock()
    loop.state.value = "running_phase"
    # current_phase is a property over _PHASE_SEQUENCE[_phase_idx]; setting
    # _phase_idx=1 makes current_phase resolve to "setup_infer" (the slot
    # we inserted in Wave 2).
    loop._phase_idx = 1

    state_diff = {
        "inferred_preset": "MMD",
        "coverage": 1.0,
        "decision": "exact",
        "candidates": [{"preset": "MMD", "coverage": 1.0}],
        "uncovered_slots": [],
    }
    await loop._emit_widget_if_inspector("setup_infer_model_type", state_diff)

    matched = [e for e in events if e["type"] == "model_type_inferred"]
    assert len(matched) == 1
    evt = matched[0]
    assert evt["preset"] == "MMD"
    assert evt["coverage"] == 1.0
    assert evt["decision"] == "exact"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_widget_skips_when_state_diff_missing_keys():
    from app.agent.loop import AgentLoop

    events: list[dict] = []
    loop = AgentLoop.__new__(AgentLoop)
    loop._event_sink = lambda e: events.append(e)
    loop.state = MagicMock()
    loop.state.value = "running_phase"
    loop._phase_idx = 1

    # No inferred_preset → don't emit.
    await loop._emit_widget_if_inspector("setup_infer_model_type", {"coverage": 0.5})
    assert not any(e["type"] == "model_type_inferred" for e in events)
