"""Pyinstaller entry point for the FastAPI backend.

Runs uvicorn against `app.main:app` with host/port honored from the
APP_HOST / APP_PORT env vars (set by the Tauri shell), falling back to
the same defaults the dev `uvicorn` invocation uses.

Why a separate entry instead of `python -m app`:
  - Pyinstaller resolves the entry script's imports at build time. A
    dedicated module makes the dependency graph explicit and lets the
    .spec file ship a single, named exe.
  - Uvicorn's reload watcher is off here — pointless in a frozen binary.
"""

from __future__ import annotations

import logging
import os
import sys


def main() -> int:
    # When frozen by pyinstaller, the bundled tree gets unpacked into
    # _MEIPASS. We don't need to mutate sys.path — the spec puts `app/`
    # on the analyzer's pathex already — but stdout/stderr can be None
    # under a Windows GUI launcher; wire them to /dev/null-equivalent so
    # uvicorn's logger doesn't crash on first write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")  # noqa: SIM115

    import uvicorn

    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8000"))

    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info("modpilot-backend starting on %s:%d", host, port)

    # Import the app explicitly (not as a module string) — uvicorn would try
    # to re-import it for reload otherwise, which doesn't work after freezing.
    from app.main import app

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
