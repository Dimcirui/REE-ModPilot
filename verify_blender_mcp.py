"""
Stage 0 — verify the full pipeline:
    Python  ->  blender-mcp TCP socket  ->  bpy.ops.modder.*

Wire protocol (read directly from blender-mcp/addon.py:130-173):
- Request:  raw JSON object, no delimiter / no length prefix.
            {"type": "execute_code", "params": {"code": "..."}}
- Response: raw JSON object, no delimiter.
            {"status": "success", "result": {"executed": true, "result": "<stdout>"}}
            or {"status": "error", "message": "..."}
- execute_code captures stdout only — exec()'s trailing expression is NOT
  returned. Always print() what you want back.
- Client must keep recv()-ing and retry json.loads on each accumulation;
  the response is one JSON blob, not line-delimited.
"""

import json
import socket
import sys

HOST = "localhost"
PORT = 9876
TIMEOUT = 30
RECV_CHUNK = 8192


def _recv_response(sock: socket.socket) -> dict:
    """Accumulate bytes until a complete JSON object is parseable."""
    buffer = b""
    while True:
        chunk = sock.recv(RECV_CHUNK)
        if not chunk:
            raise ConnectionError("Socket closed before a complete response arrived")
        buffer += chunk
        try:
            return json.loads(buffer.decode("utf-8"))
        except json.JSONDecodeError:
            continue  # partial — keep reading


def call(sock: socket.socket, cmd_type: str, params: dict | None = None) -> dict:
    """Send one command, return the unwrapped result payload."""
    payload = json.dumps({"type": cmd_type, "params": params or {}}).encode("utf-8")
    sock.sendall(payload)
    response = _recv_response(sock)
    if response.get("status") != "success":
        raise RuntimeError(f"blender-mcp error: {response.get('message')!r}")
    return response.get("result", {})


def execute_code(sock: socket.socket, code: str) -> str:
    """Run Python in Blender's main thread; return captured stdout."""
    result = call(sock, "execute_code", {"code": code})
    return result.get("result", "")


# Operators may emit their own stdout (e.g. init_editor prints progress).
# We bracket OUR prints with this sentinel so noise above it cannot shift
# line indices in the parsed output.
SENTINEL = "===STAGE0_OUT==="


def execute_and_extract(sock: socket.socket, code: str) -> list[str]:
    """Run code, return only the lines printed AFTER the SENTINEL marker."""
    full = execute_code(sock, code)
    lines = full.splitlines()
    try:
        idx = lines.index(SENTINEL)
    except ValueError:
        raise RuntimeError(
            f"sentinel not found in output. Captured stdout was:\n{full!r}"
        )
    return lines[idx + 1 :]


def _run_check(label: str, fn) -> bool:
    print(f"[{label}]")
    try:
        fn()
        return True
    except Exception as e:
        print(f"    FAIL: {e}\n")
        return False


def main() -> int:
    print(f"Connecting to blender-mcp at {HOST}:{PORT} ...")
    try:
        sock = socket.create_connection((HOST, PORT), timeout=TIMEOUT)
    except ConnectionRefusedError:
        print("FAIL: connection refused.")
        print("      In Blender: N-panel -> BlenderMCP -> Connect to Claude (start server)")
        return 1
    except OSError as e:
        print(f"FAIL: {e}")
        return 1

    print("OK   connected.\n")
    failed = 0

    with sock:
        sock.settimeout(TIMEOUT)

        # 1. Stdout-capture sanity (proves the protocol is correct).
        def t1():
            out = execute_code(sock, "print(1 + 1)").strip()
            assert out == "2", f"unexpected stdout: {out!r}"
            print(f"    eval 1+1 -> {out}\n")
        if not _run_check("1/5 stdout capture", t1):
            failed += 1

        # 2. Blender version — confirms we're really inside Blender.
        def t2():
            out = execute_code(
                sock, "import bpy; print('.'.join(map(str, bpy.app.version)))"
            ).strip()
            print(f"    bpy.app.version -> {out}\n")
        if not _run_check("2/5 Blender version", t2):
            failed += 1

        # 3. Built-in scene info handler — different code path than execute_code.
        def t3():
            scene = call(sock, "get_scene_info")
            print(
                f"    scene='{scene.get('name')}' "
                f"objects={scene.get('object_count')} "
                f"materials={scene.get('materials_count')}\n"
            )
        if not _run_check("3/5 get_scene_info", t3):
            failed += 1

        # 4. Modding-Toolkit operator namespace must exist.
        def t4():
            code = (
                "import bpy\n"
                "names = [n for n in dir(bpy.ops.modder) if not n.startswith('_')]\n"
                f"print({SENTINEL!r})\n"
                "print(len(names))\n"
                "print(','.join(sorted(names)[:6]))\n"
            )
            lines = execute_and_extract(sock, code)
            count = int(lines[0])
            sample = lines[1] if len(lines) > 1 else ""
            if count == 0:
                raise RuntimeError(
                    "bpy.ops.modder is empty — is the Modding-Toolkit addon enabled?"
                )
            print(f"    {count} modder.* operators registered. sample: {sample}\n")
        if not _run_check("4/5 Modding-Toolkit registered", t4):
            failed += 1

        # 5. Actually invoke a side-effect-free Modding-Toolkit operator.
        #    modder.init_editor has no preconditions per docs/plugin_api.md
        #    and merely populates context.scene.mhw_preset_editor.slots
        #    with the 58 standard bone slots. (It prints its own progress,
        #    hence the SENTINEL bracketing.)
        def t5():
            code = (
                "import bpy\n"
                "ret = bpy.ops.modder.init_editor()\n"
                "slots = bpy.context.scene.mhw_preset_editor.slots\n"
                f"print({SENTINEL!r})\n"
                "print(ret)\n"
                "print(len(slots))\n"
            )
            lines = execute_and_extract(sock, code)
            ret, slot_count = lines[0], int(lines[1])
            if "FINISHED" not in ret:
                raise RuntimeError(f"operator did not finish: {ret}")
            if slot_count < 50:
                raise RuntimeError(f"expected ~58 slots, got {slot_count}")
            print(f"    modder.init_editor -> {ret}, slots={slot_count}\n")
        if not _run_check("5/5 invoke modder.init_editor", t5):
            failed += 1

    if failed:
        print(f"=== {failed} check(s) FAILED ===")
        return 2
    print("=== Stage 0 PASSED. Pipeline is alive. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
