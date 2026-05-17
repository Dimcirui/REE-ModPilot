"""
Unit tests for the session config form (issue #3).

Covers:
  - SessionConfig Pydantic model validation (at least one body part required).
  - POST /agent/config storing the config in app.state.session_configs.
  - Server-side path-existence checks returning 422 with field_errors per
    offending input.
  - Stored config flowing into AgentLoop._session_config on the next
    POST /agent/messages for the same session_id.

Real Blender is not required: BlenderClient is monkey-patched to a no-op
stub and LLMClient.from_settings is replaced with a MagicMock factory.

Run with: uv run pytest -m unit tests/unit/test_session_config_form.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore

from app.blender.client import BlenderClient
from app.llm.client import LLMClient

# ── helpers (mirror tests/unit/test_sse_routes.py) ─────────────────────────


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


def _valid_config_payload(tmp_path) -> dict:
    """Build a SessionConfigRequest body that points at real paths so the
    server-side existence checks pass."""
    model_file = tmp_path / "model.fbx"
    model_file.write_bytes(b"")
    tex_dir = tmp_path / "textures"
    tex_dir.mkdir()
    mod_dir = tmp_path / "mod_root"
    mod_dir.mkdir()
    return {
        "session_id": "cfg-test",
        "config": {
            "model_path": str(model_file),
            "model_type": "MMD",
            "texture_dir": str(tex_dir),
            "mod_root": str(mod_dir),
            "author": "Acme",
            "character_name": "Hero",
            "use_bone_system": True,
            "body_parts": ["1", "2"],
            # Issue #10: hunter type + equipment now part of the global config.
            "armor_variant": "ff",
            "armor_id": "pl001",
        },
    }


# ── tests ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_config_stores_in_app_state(monkeypatch, tmp_path):
    """POST a valid config — server stores it in app.state.session_configs."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    payload = _valid_config_payload(tmp_path)
    with TestClient(app) as client:
        r = client.post("/agent/config", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body == {"session_id": "cfg-test", "saved": True}

        stored = app.state.session_configs["cfg-test"]
        assert stored.model_path == payload["config"]["model_path"]
        assert stored.model_type == "MMD"
        assert stored.use_bone_system is True
        assert stored.body_parts == ["1", "2"]
        # Issue #10: hunter type + equipment now live here.
        assert stored.armor_variant == "ff"
        assert stored.armor_id == "pl001"


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_config_rejects_missing_model_file(monkeypatch, tmp_path):
    """Non-existent model_path -> 422 with field_errors.model_path."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    payload = _valid_config_payload(tmp_path)
    payload["config"]["model_path"] = str(tmp_path / "does_not_exist.fbx")
    with TestClient(app) as client:
        r = client.post("/agent/config", json=payload)
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "field_errors" in detail
        assert detail["field_errors"]["model_path"] == "File not found"


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_config_rejects_missing_texture_dir(monkeypatch, tmp_path):
    """Non-existent texture_dir -> 422 with field_errors.texture_dir."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    payload = _valid_config_payload(tmp_path)
    payload["config"]["texture_dir"] = str(tmp_path / "no_such_dir")
    with TestClient(app) as client:
        r = client.post("/agent/config", json=payload)
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["field_errors"]["texture_dir"] == "Directory not found"


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_config_requires_at_least_one_body_part(monkeypatch, tmp_path):
    """Pydantic rejects an empty body_parts list (min_length=1)."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    payload = _valid_config_payload(tmp_path)
    payload["config"]["body_parts"] = []
    with TestClient(app) as client:
        r = client.post("/agent/config", json=payload)
        # FastAPI surfaces Pydantic validation as 422 with a list of errors.
        assert r.status_code == 422
        # Some pydantic error mentions body_parts in its loc trail.
        body = r.json()
        flat = str(body)
        assert "body_parts" in flat


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_session_config_passed_to_agent_loop(monkeypatch, tmp_path):
    """Config saved before /agent/messages must reach the constructed AgentLoop."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    payload = _valid_config_payload(tmp_path)
    sid = payload["session_id"]
    with TestClient(app) as client:
        app.state.llm = _stub_llm("ack")
        r = client.post("/agent/config", json=payload)
        assert r.status_code == 200

        r = client.post(
            "/agent/messages",
            json={"message": "go", "session_id": sid},
        )
        assert r.status_code == 200

        loop = app.state.agent_sessions[sid]
        # Internal state — accessed for white-box assertion.
        assert loop._session_config["model_path"] == payload["config"]["model_path"]
        assert loop._session_config["model_type"] == "MMD"
        assert loop._session_config["use_bone_system"] is True
        # And the rendered system prompt contains the pre-collected block.
        assert "Pre-collected session parameters" in loop._system_prompt
        assert payload["config"]["author"] in loop._system_prompt
        assert payload["config"]["character_name"] in loop._system_prompt
        # Issue #10: hunter type + equipment must appear in the system prompt
        # so phase 6 can read them instead of asking the user.
        assert "armor_variant" in loop._system_prompt
        assert "ff" in loop._system_prompt
        assert "pl001" in loop._system_prompt


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_config_rejects_unknown_armor_id(monkeypatch, tmp_path):
    """Issue #10: an armor_id not in the shipped catalog returns 422."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    payload = _valid_config_payload(tmp_path)
    payload["config"]["armor_id"] = "pl9999_does_not_exist"
    with TestClient(app) as client:
        r = client.post("/agent/config", json=payload)
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "field_errors" in detail
        assert "armor_id" in detail["field_errors"]


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_post_config_rejects_invalid_armor_variant(monkeypatch, tmp_path):
    """Issue #10: armor_variant outside {ff,fm,mf,mm} fails Pydantic validation."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    payload = _valid_config_payload(tmp_path)
    payload["config"]["armor_variant"] = "xx"
    with TestClient(app) as client:
        r = client.post("/agent/config", json=payload)
        assert r.status_code == 422
        assert "armor_variant" in str(r.json())


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_armor_sets_endpoint_returns_catalog(monkeypatch):
    """GET /app/armor_sets returns the shipped armor catalog."""
    _patch_blender(monkeypatch)
    _patch_llm_factory(monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/app/armor_sets")
        assert r.status_code == 200
        body = r.json()
        assert "armor_sets" in body
        ids = {entry["id"] for entry in body["armor_sets"]}
        # Sanity: at least the default pl001 ("希望 α") and a known late-set are present.
        assert "pl001" in ids
        assert "pl105" in ids
