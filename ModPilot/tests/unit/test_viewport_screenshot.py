"""
Unit tests for the viewport-screenshot helper and the /viewport_screenshot route.

Covers:
  - BlenderClient.get_viewport_screenshot: happy path returns the bytes the
    addon wrote, cleans up the temp file, and forwards max_size + format.
  - BlenderClient.get_viewport_screenshot: in-band result["error"] is
    translated into a BlenderError (the addon catches its own exceptions
    and returns status="success" with error in result).
  - GET /viewport_screenshot returns image/png 200 with a no-store header
    on success, and 503 when Blender is unreachable.

Run with: uv run pytest -m unit tests/unit/test_viewport_screenshot.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore

from app.blender.client import BlenderClient, BlenderError
from app.llm.client import LLMClient


# 8-byte PNG signature; fine as a fake payload for round-tripping bytes.
_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"deadbeef" * 4


# ── BlenderClient.get_viewport_screenshot ─────────────────────────────────


@pytest.mark.unit
def test_get_viewport_screenshot_returns_bytes_and_forwards_params(monkeypatch):
    """Happy path: helper writes a temp file, addon fills it, helper reads back."""
    captured: dict = {}

    def fake_call(self, cmd_type, params):
        captured["cmd_type"] = cmd_type
        captured["params"] = params
        # Simulate the addon writing to the path our helper supplied.
        Path(params["filepath"]).write_bytes(_FAKE_PNG)
        return {
            "success": True,
            "width": 600,
            "height": 257,
            "filepath": params["filepath"],
        }

    monkeypatch.setattr(BlenderClient, "call", fake_call)

    client = BlenderClient()
    data = client.get_viewport_screenshot(max_size=600, format="png")

    assert data == _FAKE_PNG
    assert captured["cmd_type"] == "get_viewport_screenshot"
    assert captured["params"]["max_size"] == 600
    assert captured["params"]["format"] == "png"
    # And the temp file should have been cleaned up after read-back.
    assert not Path(captured["params"]["filepath"]).exists()


@pytest.mark.unit
def test_get_viewport_screenshot_translates_inband_error(monkeypatch):
    """The addon returns status=success with result.error on failure — the
    helper must raise BlenderError so callers don't silently get empty bytes."""

    def fake_call(self, cmd_type, params):
        # Do NOT write the file; mirror addon's "no 3D viewport found" path.
        return {"error": "No 3D viewport found"}

    monkeypatch.setattr(BlenderClient, "call", fake_call)

    client = BlenderClient()
    with pytest.raises(BlenderError, match="No 3D viewport found"):
        client.get_viewport_screenshot()


@pytest.mark.unit
def test_get_viewport_screenshot_cleans_up_on_error(monkeypatch):
    """Even on error, the temp file (if Blender wrote something before failing)
    must be removed. We assert by capturing the path the helper picked."""
    captured_path: dict = {}

    def fake_call(self, cmd_type, params):
        # Pretend the addon wrote a stub file *and* then reported an error.
        # Realistic-ish: an exception after writing.
        Path(params["filepath"]).write_bytes(b"partial")
        captured_path["path"] = params["filepath"]
        return {"error": "boom"}

    monkeypatch.setattr(BlenderClient, "call", fake_call)

    client = BlenderClient()
    with pytest.raises(BlenderError):
        client.get_viewport_screenshot()
    assert not Path(captured_path["path"]).exists()


# ── /viewport_screenshot route ────────────────────────────────────────────


def _patch_blender_connected(monkeypatch):
    monkeypatch.setattr(BlenderClient, "connect", lambda self: None)
    monkeypatch.setattr(BlenderClient, "connected", property(lambda self: True))
    monkeypatch.setattr(BlenderClient, "close", lambda self: None)


def _patch_llm_factory(monkeypatch):
    """Lifespan calls LLMClient.from_settings(); stub it so no API key is needed."""
    monkeypatch.setattr(LLMClient, "from_settings", classmethod(lambda cls: MagicMock()))


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_returns_png_bytes_and_no_store_header(monkeypatch):
    _patch_blender_connected(monkeypatch)
    _patch_llm_factory(monkeypatch)
    monkeypatch.setattr(
        BlenderClient,
        "get_viewport_screenshot",
        lambda self, max_size=800, format="png": _FAKE_PNG,
    )

    from app.main import app  # late import so monkeypatch takes effect on lifespan

    with TestClient(app) as client:
        resp = client.get("/viewport_screenshot")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.content == _FAKE_PNG


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_forwards_max_size(monkeypatch):
    _patch_blender_connected(monkeypatch)
    _patch_llm_factory(monkeypatch)
    seen: dict = {}

    def fake_shot(self, max_size=800, format="png"):
        seen["max_size"] = max_size
        return _FAKE_PNG

    monkeypatch.setattr(BlenderClient, "get_viewport_screenshot", fake_shot)

    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/viewport_screenshot?max_size=400")
    assert resp.status_code == 200
    assert seen["max_size"] == 400


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_rejects_out_of_range_max_size(monkeypatch):
    _patch_blender_connected(monkeypatch)
    _patch_llm_factory(monkeypatch)
    monkeypatch.setattr(
        BlenderClient,
        "get_viewport_screenshot",
        lambda self, max_size=800, format="png": _FAKE_PNG,
    )

    from app.main import app

    with TestClient(app) as client:
        # ge=64 / le=2048 in the Query — anything outside should 422.
        too_small = client.get("/viewport_screenshot?max_size=10")
        too_big = client.get("/viewport_screenshot?max_size=9999")
    assert too_small.status_code == 422
    assert too_big.status_code == 422


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_returns_503_when_blender_raises(monkeypatch):
    _patch_blender_connected(monkeypatch)
    _patch_llm_factory(monkeypatch)

    def fake_shot(self, max_size=800, format="png"):
        raise BlenderError("No 3D viewport found")

    monkeypatch.setattr(BlenderClient, "get_viewport_screenshot", fake_shot)

    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/viewport_screenshot")
    assert resp.status_code == 503
    assert "No 3D viewport found" in resp.json()["detail"]


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_route_returns_503_when_blender_disconnected(monkeypatch):
    """If the socket call itself errors (Blender quit mid-session), the route
    should close the client and surface a 503, matching the /health pattern."""
    _patch_llm_factory(monkeypatch)
    # Simulate a freshly-disconnected client that fails to reconnect.
    monkeypatch.setattr(BlenderClient, "connected", property(lambda self: False))
    monkeypatch.setattr(
        BlenderClient,
        "connect",
        lambda self: (_ for _ in ()).throw(OSError("connection refused")),
    )

    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/viewport_screenshot")
    assert resp.status_code == 503
