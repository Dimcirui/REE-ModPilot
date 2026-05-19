"""Unit tests for GET /app/toolkit_status (toolkit preflight route).

Covers:
  - 200 path: tools list serialized correctly, ok flag respects critical-status gate
  - 503 path: Blender unreachable (BlenderError / OSError from probe)
  - ok=false when a critical tool is missing/disabled
  - ok=true when only non-critical tools are missing (no current case, kept as guard)

The probe is patched at `app.main.probe_toolkit` so we never touch a real Blender
socket. Standard `_patch_blender` no-op stub keeps `_get_client` happy.

Run with: uv run pytest -m unit tests/unit/test_toolkit_status_route.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore

from app.blender.client import BlenderClient, BlenderError
from app.toolkit_probe import ToolStatus


def _patch_blender(monkeypatch):
    monkeypatch.setattr(BlenderClient, "connect", lambda self: None)
    monkeypatch.setattr(BlenderClient, "close", lambda self: None)


def _patch_probe(monkeypatch, return_value=None, side_effect=None):
    stub = MagicMock()
    if side_effect is not None:
        stub.side_effect = side_effect
    else:
        stub.return_value = return_value
    monkeypatch.setattr("app.main.probe_toolkit", stub)
    return stub


def _all_present_tools() -> list[ToolStatus]:
    return [
        ToolStatus(id="mbt", label="Modding-Toolkit", status="present", critical=True),
        ToolStatus(id="mhws", label="MHWs Plugin", status="present", critical=True),
        ToolStatus(id="re_mesh", label="RE Mesh Editor", status="present", critical=True),
        ToolStatus(id="re_chain", label="RE Chain Editor", status="present", critical=True),
    ]


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_returns_ok_when_all_present(monkeypatch):
    _patch_blender(monkeypatch)
    _patch_probe(monkeypatch, return_value=_all_present_tools())
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/app/toolkit_status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["tools"]) == 4
    assert {t["id"] for t in body["tools"]} == {"mbt", "mhws", "re_mesh", "re_chain"}
    for tool in body["tools"]:
        assert set(tool.keys()) == {"id", "label", "status", "critical"}
        assert tool["status"] == "present"
        assert tool["critical"] is True


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_ok_false_when_critical_missing(monkeypatch):
    tools = _all_present_tools()
    tools[2] = ToolStatus(  # re_mesh missing
        id="re_mesh", label="RE Mesh Editor", status="missing", critical=True,
    )
    _patch_blender(monkeypatch)
    _patch_probe(monkeypatch, return_value=tools)
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/app/toolkit_status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    statuses = {t["id"]: t["status"] for t in body["tools"]}
    assert statuses["re_mesh"] == "missing"
    assert statuses["mbt"] == "present"


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_ok_false_when_critical_disabled(monkeypatch):
    """A disabled-but-installed addon is still not usable — gate the Start button."""
    tools = _all_present_tools()
    tools[1] = ToolStatus(
        id="mhws", label="MHWs Plugin", status="disabled", critical=True,
    )
    _patch_blender(monkeypatch)
    _patch_probe(monkeypatch, return_value=tools)
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/app/toolkit_status")
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is False


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_ok_ignores_non_critical_missing(monkeypatch):
    """If a tool is ever marked non-critical, missing-ness shouldn't fail the gate."""
    tools = _all_present_tools()
    tools[3] = ToolStatus(
        id="re_chain", label="RE Chain Editor", status="missing", critical=False,
    )
    _patch_blender(monkeypatch)
    _patch_probe(monkeypatch, return_value=tools)
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/app/toolkit_status")
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is True


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_503_when_blender_unreachable_blender_error(monkeypatch):
    _patch_blender(monkeypatch)
    _patch_probe(monkeypatch, side_effect=BlenderError("connection lost"))
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/app/toolkit_status")
    assert r.status_code == 503
    assert "connection lost" in r.json()["detail"]


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_503_when_blender_unreachable_os_error(monkeypatch):
    _patch_blender(monkeypatch)
    _patch_probe(monkeypatch, side_effect=OSError("socket closed"))
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/app/toolkit_status")
    assert r.status_code == 503
    assert "socket closed" in r.json()["detail"]
