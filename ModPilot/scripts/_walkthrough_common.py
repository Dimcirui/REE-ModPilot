"""Shared bits for the per-stage Playwright walkthrough recorders.

Each `record_phaseN_walkthrough.py` script imports `EVENT_SOURCE_STUB`,
`push`, `beat`, and `record` from here and supplies a `walk(page)` callable
that drives the synthetic SSE sequence for that stage.

Run any walkthrough with:
    cd ModPilot
    .venv\\Scripts\\python.exe scripts\\record_phaseN_walkthrough.py
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "artifacts" / "ui_walkthroughs"
URL = "http://localhost:5173"

# Fake EventSource installed before any page script runs. Exposes
# `window.__pushSse(type, payload)` so Python drives synthetic SSE events.
EVENT_SOURCE_STUB = r"""
(() => {
  if (window.__sseInstalled) return;
  window.__sseInstalled = true;

  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.CONNECTING = 0; this.OPEN = 1; this.CLOSED = 2;
      this._listeners = {};
      this.onopen = null; this.onmessage = null; this.onerror = null;
      window.__fakeSse = window.__fakeSse || [];
      window.__fakeSse.push(this);
      setTimeout(() => {
        this.readyState = 1;
        const ev = new Event('open');
        if (this.onopen) this.onopen(ev);
        (this._listeners['open'] || []).forEach((l) => l(ev));
      }, 50);
    }
    addEventListener(type, listener) {
      (this._listeners[type] ||= []).push(listener);
    }
    removeEventListener(type, listener) {
      const ls = this._listeners[type];
      if (!ls) return;
      const i = ls.indexOf(listener);
      if (i >= 0) ls.splice(i, 1);
    }
    close() { this.readyState = 2; }
    _dispatch(type, data) {
      const ev = new MessageEvent(type, { data: JSON.stringify(data) });
      if (type === 'message' && this.onmessage) this.onmessage(ev);
      (this._listeners[type] || []).forEach((l) => l(ev));
    }
  }

  window.EventSource = FakeEventSource;
  window.__pushSse = (type, payload) => {
    for (const inst of (window.__fakeSse || [])) {
      if (inst.readyState === 1) inst._dispatch(type, payload || {});
    }
  };
})();
"""


def beat(page: Page, ms: int = 800) -> None:
    page.wait_for_timeout(ms)


def push(page: Page, type_: str, payload: dict[str, Any]) -> None:
    """Dispatch a synthetic SSE event into the running app."""
    page.evaluate(
        "([t, p]) => window.__pushSse(t, p)",
        [type_, payload],
    )
    print(f"  sse    : {type_}  {json.dumps(payload)[:90]}")


def try_fill(page: Page, selector: str, value: str, *, label: str, timeout: int = 3000) -> None:
    try:
        page.locator(selector).first.fill(value, timeout=timeout)
        print(f"  fill   : {label} = {value!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  skip   : fill {label} -> {exc.__class__.__name__}")


def try_click(page: Page, selector: str, *, label: str, timeout: int = 3000) -> None:
    try:
        page.locator(selector).first.click(timeout=timeout)
        print(f"  click  : {label}")
    except Exception as exc:  # noqa: BLE001
        print(f"  skip   : click {label} -> {exc.__class__.__name__}")


def record(
    name: str,
    walk: Callable[[Page], None],
    *,
    viewport: tuple[int, int] = (1280, 820),
) -> int:
    """Open headless Chromium, install the EventSource stub, run `walk(page)`, save webm."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUT_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    w, h = viewport

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": w, "height": h},
            record_video_dir=str(run_dir),
            record_video_size={"width": w, "height": h},
        )
        ctx.add_init_script(EVENT_SOURCE_STUB)
        page = ctx.new_page()
        try:
            walk(page)
        except Exception as exc:  # noqa: BLE001
            print(f"!! walk raised: {exc!r}")
        finally:
            video = page.video
            ctx.close()
            browser.close()

        if video is None:
            print("ERROR: no video was recorded.")
            return 1

        webm = video.path()
        final = run_dir / f"{name}.webm"
        Path(webm).rename(final)
        print(f"\nWEBM: {final}")
        print(f"size: {final.stat().st_size} bytes")
    return 0
