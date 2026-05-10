#!/usr/bin/env python3
"""
ModPilot interactive CLI — minimal shell for end-to-end testing.

Prerequisites:
  1. Blender open, blender-mcp addon enabled, "Connect to Claude" clicked (port 9876).
  2. Backend running:
       cd ModPilot && uv run uvicorn app.main:app --reload

Usage:
  python cli.py
  python cli.py --url http://localhost:8000 --session my-session

Slash commands:
  /health   check Blender connectivity
  /scene    dump current scene object list
  /reset    start a fresh session (clears agent conversation history)
  /session  show current session id
  /help     show this command list
  /quit     exit
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import uuid

try:
    import httpx
except ImportError:
    print("httpx not found. Run:  uv run python cli.py  OR  pip install httpx")
    sys.exit(1)

# ── ANSI colour helpers (degrade gracefully on Windows without VT) ────────────

_USE_COLOUR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text


def cyan(t: str) -> str:   return _c("36", t)
def green(t: str) -> str:  return _c("32", t)
def yellow(t: str) -> str: return _c("33", t)
def red(t: str) -> str:    return _c("31", t)
def dim(t: str) -> str:    return _c("2",  t)
def bold(t: str) -> str:   return _c("1",  t)


# ── Spinner for blocking requests ────────────────────────────────────────────

class _Spinner:
    _FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, label: str = "thinking") -> None:
        self._label = label
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = self._FRAMES[i % len(self._FRAMES)]
            print(f"\r{dim(frame + ' ' + self._label + '...')}  ", end="", flush=True)
            i += 1
            time.sleep(0.08)
        print("\r" + " " * (len(self._label) + 8) + "\r", end="", flush=True)

    def __enter__(self) -> "_Spinner":
        if _USE_COLOUR:
            self._thread.start()
        return self

    def __exit__(self, *_) -> None:
        self._stop.set()
        if _USE_COLOUR and self._thread.is_alive():
            self._thread.join(timeout=0.5)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(client: httpx.Client, path: str) -> dict:
    r = client.get(path, timeout=15)
    r.raise_for_status()
    return r.json()


def _post(client: httpx.Client, path: str, body: dict, timeout: float = 180) -> dict:
    r = client.post(path, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ── State label ───────────────────────────────────────────────────────────────

_STATE_COLOUR = {
    "IDLE":       dim,
    "EXECUTING":  yellow,
    "ASKING":     cyan,
    "NEGOTIATING": cyan,
    "ERROR":      red,
    "DONE":       green,
}


def _state_label(state: str) -> str:
    colour = _STATE_COLOUR.get(state.upper(), dim)
    return colour(f"[{state}]")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ModPilot CLI")
    parser.add_argument("--url",     default="http://localhost:8000",
                        help="Backend base URL (default: http://localhost:8000)")
    parser.add_argument("--session", default=None,
                        help="Session ID (default: random)")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    session_id = args.session or str(uuid.uuid4())[:8]

    print(bold("\nModPilot CLI"))
    print(dim(f"backend : {base_url}"))
    print(dim(f"session : {session_id}"))
    print(dim("type /help for commands, Ctrl+C to exit\n"))

    http = httpx.Client(base_url=base_url)

    # Quick connectivity check on startup
    try:
        data = _get(http, "/health")
        scene = data.get("blender", {}).get("scene", "?")
        objs  = data.get("blender", {}).get("objects", "?")
        print(green("✓ Blender connected") + dim(f"  scene={scene!r}  objects={objs}") + "\n")
    except httpx.HTTPStatusError as e:
        print(yellow(f"⚠ Backend reachable but Blender not connected ({e.response.status_code})"))
        print(dim("  Start Blender → enable blender-mcp → click 'Connect to Claude'\n"))
    except httpx.ConnectError:
        print(red(f"✗ Cannot reach backend at {base_url}"))
        print(dim("  Start with: cd ModPilot && uv run uvicorn app.main:app --reload\n"))
        sys.exit(1)

    while True:
        # Prompt
        try:
            raw = input(cyan("You: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(dim("\nbye"))
            break

        if not raw:
            continue

        # ── slash commands ─────────────────────────────────────────────────
        if raw.startswith("/"):
            cmd = raw.lower().split()[0]

            if cmd in ("/quit", "/exit", "/q"):
                print(dim("bye"))
                break

            elif cmd == "/help":
                print(dim(
                    "  /health  — Blender connectivity check\n"
                    "  /scene   — list scene objects\n"
                    "  /reset   — new session\n"
                    "  /session — show session id\n"
                    "  /help    — this list\n"
                    "  /quit    — exit"
                ))

            elif cmd == "/health":
                try:
                    data = _get(http, "/health")
                    b = data.get("blender", {})
                    print(green("✓ ok") + dim(
                        f"  scene={b.get('scene')!r}"
                        f"  objects={b.get('objects')}"
                    ))
                except httpx.HTTPStatusError as e:
                    print(red(f"✗ {e.response.status_code} — {e.response.json().get('detail')}"))
                except Exception as e:
                    print(red(f"✗ {e}"))

            elif cmd == "/scene":
                try:
                    data = _get(http, "/scene_info")
                    objects = data.get("objects", [])
                    print(dim(f"  {len(objects)} object(s):"))
                    for obj in objects[:20]:
                        name = obj.get("name", "?")
                        otype = obj.get("type", "?")
                        print(dim(f"    {name}  ({otype})"))
                    if len(objects) > 20:
                        print(dim(f"    … and {len(objects) - 20} more"))
                except Exception as e:
                    print(red(f"✗ {e}"))

            elif cmd == "/reset":
                session_id = args.session or str(uuid.uuid4())[:8]
                print(dim(f"  new session: {session_id}"))

            elif cmd == "/session":
                print(dim(f"  {session_id}"))

            else:
                print(yellow(f"  unknown command {cmd!r} — type /help"))

            continue

        # ── agent chat ─────────────────────────────────────────────────────
        try:
            with _Spinner("thinking"):
                data = _post(
                    http,
                    "/agent/chat",
                    {"message": raw, "session_id": session_id},
                )
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = e.response.json().get("detail", "")
            except Exception:
                pass
            print(red(f"✗ HTTP {e.response.status_code}") + (f" — {detail}" if detail else ""))
            continue
        except httpx.TimeoutException:
            print(red("✗ request timed out — Blender may be busy"))
            continue
        except Exception as e:
            print(red(f"✗ {e}"))
            continue

        reply   = data.get("reply", "")
        state   = data.get("state", "")
        label   = _state_label(state) if state else ""

        print(f"\n{bold('Agent')} {label}")
        # Wrap long lines for readability
        for line in reply.splitlines():
            print(f"  {line}")
        print()


if __name__ == "__main__":
    main()
