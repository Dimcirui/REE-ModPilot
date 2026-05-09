"""
Blender TCP socket client.

Wire protocol (authoritative source: blender-mcp/addon.py):
  Request:  raw JSON, no delimiter — {"type": ..., "params": {...}}
  Response: raw JSON, no delimiter — {"status": "success", "result": {...}}
                                  or {"status": "error", "message": "..."}

execute_code returns stdout only — exec()'s trailing expression is NOT returned.
Always print() what you want back from Blender-side code.

SENTINEL pattern: operators (e.g. modder.*) may emit their own stdout before
our print() calls. Bracket outputs with BLENDER_SENTINEL and use
execute_and_extract() to discard noise above the sentinel.
"""

from __future__ import annotations

import json
import socket
from contextlib import contextmanager
from typing import Generator

BLENDER_SENTINEL = "===MODPILOT_OUT==="
_RECV_CHUNK = 8192


class BlenderError(RuntimeError):
    """Raised when blender-mcp responds with status='error'."""


class BlenderClient:
    """
    Stateful TCP connection to blender-mcp running inside Blender.

    Usage (preferred — automatic cleanup):
        with BlenderClient.connect("127.0.0.1", 9876) as client:
            version = client.execute("import bpy; print(bpy.app.version)")

    Or manually:
        client = BlenderClient("127.0.0.1", 9876)
        client.connect()
        ...
        client.close()
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9876, timeout: float = 30.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None

    # ── connection management ──────────────────────────────────────────────

    def connect(self) -> "BlenderClient":
        """Open the TCP connection. Returns self for chaining."""
        if self._sock is not None:
            return self
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._sock = sock
        return self

    def close(self) -> None:
        """Close the TCP connection if open."""
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "BlenderClient":
        return self.connect()

    def __exit__(self, *_) -> None:
        self.close()

    @classmethod
    @contextmanager
    def connect_ctx(
        cls, host: str = "127.0.0.1", port: int = 9876, timeout: float = 30.0
    ) -> Generator["BlenderClient", None, None]:
        """Context manager that creates, connects, and auto-closes a client."""
        client = cls(host, port, timeout)
        try:
            yield client.connect()
        finally:
            client.close()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def _require_connected(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("BlenderClient is not connected. Call connect() first.")
        return self._sock

    # ── low-level protocol ─────────────────────────────────────────────────

    @staticmethod
    def _recv_response(sock: socket.socket) -> dict:
        """Accumulate bytes until a complete JSON object can be parsed."""
        buffer = b""
        while True:
            chunk = sock.recv(_RECV_CHUNK)
            if not chunk:
                raise ConnectionError("Socket closed before a complete response arrived")
            buffer += chunk
            try:
                return json.loads(buffer.decode("utf-8"))
            except json.JSONDecodeError:
                continue  # partial — keep reading

    def call(self, cmd_type: str, params: dict | None = None) -> dict:
        """
        Send one blender-mcp command and return the unwrapped result payload.

        Raises BlenderError on status='error'.
        """
        sock = self._require_connected()
        payload = json.dumps({"type": cmd_type, "params": params or {}}).encode("utf-8")
        sock.sendall(payload)
        response = self._recv_response(sock)
        if response.get("status") != "success":
            raise BlenderError(response.get("message", "unknown error"))
        return response.get("result", {})

    # ── high-level helpers ─────────────────────────────────────────────────

    def execute(self, code: str) -> str:
        """
        Run Python in Blender's main thread; return captured stdout as a string.

        Note: the last expression of code is NOT auto-returned — always print().
        """
        result = self.call("execute_code", {"code": code})
        return result.get("result", "")

    def execute_and_extract(self, code: str) -> list[str]:
        """
        Run code; return only the lines printed AFTER BLENDER_SENTINEL.

        Use this when a Modding-Toolkit operator may emit its own stdout before
        your print() calls, to avoid sentinel-shift bugs.

        Example code pattern:
            import bpy
            ret = bpy.ops.modder.some_op()
            print(BLENDER_SENTINEL)   # sentinel goes AFTER operator call
            print(ret)
        """
        full = self.execute(code)
        lines = full.splitlines()
        try:
            idx = lines.index(BLENDER_SENTINEL)
        except ValueError:
            raise BlenderError(
                f"sentinel {BLENDER_SENTINEL!r} not found in output.\n"
                f"Captured stdout:\n{full!r}"
            )
        return lines[idx + 1 :]

    def get_scene_info(self) -> dict:
        """Call the built-in get_scene_info handler (faster than execute_code)."""
        return self.call("get_scene_info")

    def get_object_info(self, object_name: str) -> dict:
        """Call the built-in get_object_info handler for a named object."""
        return self.call("get_object_info", {"object_name": object_name})
