"""
Unit tests for app/blender/client.py and app/blender/state.py.

These tests use a fake in-process socket server — no real Blender required.
Run with: uv run pytest -m unit tests/unit/test_blender_client.py -v
"""

from __future__ import annotations

import json
import socket
import threading
import pytest

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.state import SceneCache, SceneState, _parse_scene_info


# ── fake socket server fixture ─────────────────────────────────────────────


class FakeBlenderServer:
    """
    Minimal TCP server that speaks the blender-mcp wire protocol.
    Responses are configured via the `responses` dict keyed by command type.
    Call close() when done (or use as a context manager).
    """

    def __init__(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))  # OS picks a free port
        self._server.listen(1)
        self.port: int = self._server.getsockname()[1]
        self.host: str = "127.0.0.1"
        self.responses: dict[str, dict] = {}  # cmd_type -> response payload
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._server.accept()
        except OSError:
            return  # server was closed
        with conn:
            buffer = b""
            while True:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                buffer += chunk
                try:
                    request = json.loads(buffer.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                buffer = b""
                cmd_type = request.get("type", "")
                if cmd_type in self.responses:
                    reply = self.responses[cmd_type]
                else:
                    reply = {"status": "error", "message": f"unknown command: {cmd_type}"}
                conn.sendall(json.dumps(reply).encode("utf-8"))

    def close(self) -> None:
        self._server.close()

    def __enter__(self) -> "FakeBlenderServer":
        return self

    def __exit__(self, *_) -> None:
        self.close()


@pytest.fixture
def fake_server():
    """Yield a running FakeBlenderServer; tear it down after the test."""
    with FakeBlenderServer() as srv:
        yield srv


@pytest.fixture
def client(fake_server: FakeBlenderServer):
    """Yield a connected BlenderClient pointed at the fake server."""
    with BlenderClient(fake_server.host, fake_server.port, timeout=5.0) as c:
        yield c, fake_server


# ── BlenderClient tests ────────────────────────────────────────────────────


@pytest.mark.unit
class TestBlenderClientProtocol:
    def test_execute_returns_stdout(self, client):
        c, srv = client
        srv.responses["execute_code"] = {
            "status": "success",
            "result": {"executed": True, "result": "hello\n"},
        }
        out = c.execute("print('hello')")
        assert out == "hello\n"

    def test_get_scene_info(self, client):
        c, srv = client
        scene_payload = {
            "name": "TestScene",
            "object_count": 2,
            "objects": [
                {"name": "Cube", "type": "MESH"},
                {"name": "Armature", "type": "ARMATURE"},
            ],
            "materials_count": 1,
        }
        srv.responses["get_scene_info"] = {"status": "success", "result": scene_payload}
        result = c.get_scene_info()
        assert result["name"] == "TestScene"
        assert result["object_count"] == 2

    def test_error_response_raises_blender_error(self, client):
        c, srv = client
        srv.responses["execute_code"] = {
            "status": "error",
            "message": "SyntaxError: invalid syntax",
        }
        with pytest.raises(BlenderError, match="SyntaxError"):
            c.execute("def (")

    def test_execute_and_extract_returns_post_sentinel_lines(self, client):
        c, srv = client
        output = f"operator noise\n{BLENDER_SENTINEL}\nvalue_line\nextra\n"
        srv.responses["execute_code"] = {
            "status": "success",
            "result": {"executed": True, "result": output},
        }
        lines = c.execute_and_extract("# sentinel code")
        assert lines == ["value_line", "extra"]

    def test_execute_and_extract_missing_sentinel_raises(self, client):
        c, srv = client
        srv.responses["execute_code"] = {
            "status": "success",
            "result": {"executed": True, "result": "no sentinel here\n"},
        }
        with pytest.raises(BlenderError, match="sentinel"):
            c.execute_and_extract("# bad code")

    def test_call_without_connect_raises(self):
        c = BlenderClient("127.0.0.1", 1)  # not connected
        with pytest.raises(RuntimeError, match="not connected"):
            c.call("execute_code")


# ── SceneState / SceneCache tests ──────────────────────────────────────────


@pytest.mark.unit
class TestSceneState:
    def test_parse_scene_info(self):
        raw = {
            "name": "Scene",
            "object_count": 2,
            "objects": [
                {"name": "Armature", "type": "ARMATURE"},
                {"name": "Body", "type": "MESH"},
            ],
            "materials_count": 3,
        }
        state = _parse_scene_info(raw)
        assert state.scene_name == "Scene"
        assert state.object_count == 2
        assert "Armature" in state.object_names
        assert state.materials_count == 3
        assert state.objects["Body"].type == "MESH"

    def test_diff_detects_added_objects(self):
        old = SceneState(object_names=["Cube"], objects={})
        new = SceneState(object_names=["Cube", "NewMesh"], objects={})
        diff = old.diff(new)
        assert "objects_added" in diff
        assert "NewMesh" in diff["objects_added"]

    def test_diff_detects_removed_objects(self):
        old = SceneState(object_names=["Cube", "ToDelete"], objects={})
        new = SceneState(object_names=["Cube"], objects={})
        diff = old.diff(new)
        assert "objects_removed" in diff
        assert "ToDelete" in diff["objects_removed"]

    def test_diff_empty_when_no_change(self):
        state = SceneState(
            scene_name="S", object_count=1, object_names=["Cube"], materials_count=0
        )
        assert state.diff(state) == {}

    def test_is_empty(self):
        assert SceneState().is_empty()
        assert not SceneState(scene_name="Scene").is_empty()


@pytest.mark.unit
class TestSceneCache:
    def test_refresh_populates_state(self, client):
        c, srv = client
        srv.responses["get_scene_info"] = {
            "status": "success",
            "result": {
                "name": "MyScene",
                "object_count": 1,
                "objects": [{"name": "Armature", "type": "ARMATURE"}],
                "materials_count": 0,
            },
        }
        cache = SceneCache(c)
        assert cache.state.is_empty()
        state = cache.refresh()
        assert state.scene_name == "MyScene"
        assert cache.state.scene_name == "MyScene"

    def test_invalidate_resets_state(self, client):
        c, srv = client
        srv.responses["get_scene_info"] = {
            "status": "success",
            "result": {
                "name": "S",
                "object_count": 0,
                "objects": [],
                "materials_count": 0,
            },
        }
        cache = SceneCache(c)
        cache.refresh()
        assert not cache.state.is_empty()
        cache.invalidate()
        assert cache.state.is_empty()
