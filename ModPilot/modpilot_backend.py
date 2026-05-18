"""Pyinstaller entry point for the FastAPI backend.

Runs uvicorn against `app.main:app` with host/port honored from the
APP_HOST / APP_PORT env vars (set by the Tauri shell), falling back to
the same defaults the dev `uvicorn` invocation uses.

Why a separate entry instead of `python -m app`:
  - Pyinstaller resolves the entry script's imports at build time. A
    dedicated module makes the dependency graph explicit and lets the
    .spec file ship a single, named exe.
  - Uvicorn's reload watcher is off here — pointless in a frozen binary.

Logging: a Windows GUI parent detaches the child's stdout/stderr, so any
print/traceback the backend emits vanishes into the void. To diagnose
"won't start" reports without asking the user to run from a console, we
mirror everything to a file under MODPILOT_LOG_DIR (Tauri sets this to
its app_log_dir; falls back to %LOCALAPPDATA%/ModPilot/logs).
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _resolve_log_dir() -> Path:
    """Pick the log directory.

    Priority: MODPILOT_LOG_DIR (set by Tauri shell) > %LOCALAPPDATA%
    > %TEMP%. Last resort keeps us writeable even on weird Windows
    profiles where LOCALAPPDATA isn't set.
    """
    explicit = os.environ.get("MODPILOT_LOG_DIR")
    if explicit:
        return Path(explicit)
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        return Path(local_app) / "ModPilot" / "logs"
    return Path(os.environ.get("TEMP", ".")) / "ModPilot-logs"


def _open_log(log_dir: Path) -> "tuple[Path, object]":
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "backend.log"
    # line-buffered so a crash mid-write still flushes; encoding=utf-8 so
    # tracebacks with non-ascii paths don't choke on the default cp1252.
    fh = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")  # noqa: SIM115
    return log_path, fh


def main() -> int:
    log_dir = _resolve_log_dir()
    try:
        log_path, log_file = _open_log(log_dir)
    except OSError:
        # If we can't even open the log file, fall back to the /dev/null
        # behavior from before so uvicorn doesn't crash on first write.
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")  # noqa: SIM115
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")  # noqa: SIM115
        # No log file → no visibility, but at least it runs.
        return _run_uvicorn()

    # Replace the GUI-detached (or None) std streams so any print() or
    # bare traceback lands in the file. uvicorn's logger gets a proper
    # FileHandler below — this catches everything else.
    sys.stdout = log_file
    sys.stderr = log_file

    handler = logging.StreamHandler(log_file)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # uvicorn configures its own loggers on startup, so add the handler
    # to them explicitly instead of relying on root-propagation.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging.getLogger(name).addHandler(handler)

    log = logging.getLogger("modpilot_backend")
    log.info(
        "=== modpilot-backend boot @ %s (pid %d) — log: %s ===",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        os.getpid(),
        log_path,
    )

    try:
        return _run_uvicorn()
    except SystemExit:
        raise
    except BaseException:
        # Catch *everything* — including the OSError thrown by uvicorn
        # when bind() fails (EADDRINUSE was the historical silent killer).
        log.critical("modpilot-backend crashed during startup:\n%s", traceback.format_exc())
        return 1


def _run_uvicorn() -> int:
    import uvicorn

    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8000"))

    logging.getLogger(__name__).info("modpilot-backend starting on %s:%d", host, port)

    # Import the app explicitly (not as a module string) — uvicorn would try
    # to re-import it for reload otherwise, which doesn't work after freezing.
    from app.main import app

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
