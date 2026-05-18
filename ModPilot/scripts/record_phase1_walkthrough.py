"""Phase 1 (pose correction) UI walkthrough — recorded webm.

Demos the new Shell + Phase1Stage by route-bypassing the LLM:
  - Stubs `window.EventSource` so SSE events come from the script, not the
    backend. Lets us walk pending → running → success in ~25s instead of the
    real ~3 min/turn DeepSeek round-trip.
  - Real backend stays up so /app/x_presets, /app/armor_sets and the live
    /viewport_screenshot keep returning real data (Blender viewport in frame).

Run with:
    cd ModPilot
    .venv\\Scripts\\python.exe scripts\\record_phase1_walkthrough.py

Prereqs: backend on :8000 and Vite/Tauri shell on :5173 (we target the browser
URL because Playwright Chromium can't drive the Tauri webview process directly).
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "artifacts" / "ui_walkthroughs"
URL = "http://localhost:5173"


# Fake EventSource installed before any page script runs. Buffers events when
# the queue arrives before React attaches listeners (it shouldn't, but belt
# + suspenders). Exposes `window.__pushSse(type, payload)` to drive dispatch.
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


def push(page: Page, type_: str, payload: dict) -> None:
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


def walk_phase1(page: Page) -> None:
    common = {"ts": 0, "phase": None, "state": "idle"}

    print("[/] Landing on FallbackStage (no phase started)")
    page.goto(URL, wait_until="domcontentloaded")
    beat(page, 1800)

    # Show the session config form briefly so the viewer sees the starting
    # surface (FallbackStage) before we cut to Phase1Stage.
    print("  ->Fill session config to show FallbackStage")
    try_fill(page, "input[placeholder*='model.fbx']", r"D:\demo\eku\eku.fbx", label="model path")
    try_fill(page, "input[placeholder*='textures']", r"D:\demo\eku\textures", label="texture dir")
    try_fill(page, "input[placeholder*='mod_root']", r"D:\demo\eku\mod_out", label="mod root")
    try_fill(page, "input[placeholder='AuthorName']", "DemoAuthor", label="author")
    try_fill(page, "input[placeholder='CharacterName']", "Eku", label="character")
    beat(page, 1200)

    # ── Kick off Phase 1 ─────────────────────────────────────────────────
    print("[Phase 1] start")
    push(page, "state", {**common, "state": "running_phase"})
    beat(page, 300)
    push(
        page,
        "phase_started",
        {**common, "state": "running_phase", "phase": "phase_1", "index": 3, "total": 11},
    )
    beat(page, 2500)  # let StageRouter cross-fade in; show pending Phase1Stage

    # ── Tool call in flight ─────────────────────────────────────────────
    print("[Phase 1] pose_correction running")
    push(
        page,
        "tool_call",
        {
            **common,
            "state": "running_phase",
            "phase": "phase_1",
            "id": "tc_pose_1",
            "name": "pose_correction",
            "input": {
                "x_preset": "MMD",
                "source_armature": "Armature.001",
                "target_armature": "Armature",
            },
        },
    )
    beat(page, 2200)  # show the "running" tile + running card

    # ── Tool succeeded with a ratio in the summary ──────────────────────
    print("[Phase 1] pose_correction success — ratio readout")
    push(
        page,
        "tool_result",
        {
            **common,
            "state": "running_phase",
            "phase": "phase_1",
            "id": "tc_pose_1",
            "name": "pose_correction",
            "success": True,
            "summary": (
                "Pose cleared, scale align ratio=0.8724 "
                "(mean target_z=1.241 / source_z=1.422), "
                "T-pose applied via MMD shoulder rotation. "
                "6 arm bones resolved (upperarm/forearm/hand x L/R)."
            ),
        },
    )
    beat(page, 3200)  # ratio card flips green, big 0.8724 visible

    # ── Assistant wrap-up ───────────────────────────────────────────────
    print("[Phase 1] assistant wrap-up")
    push(
        page,
        "message",
        {
            **common,
            "state": "await_confirm",
            "phase": "phase_1",
            "role": "assistant",
            "content": (
                "Phase 1 complete. Pose cleared, source rig scaled by 0.8724 to "
                "match MHWs hunter height. Source is now in T-pose. Ready to "
                "proceed to Phase 2 (skeleton align) when you confirm."
            ),
        },
    )
    push(page, "state", {**common, "state": "await_confirm"})
    beat(page, 1500)

    # Expand the bottom chat strip so the message is visible
    print("[ChatStrip] expand to show assistant text")
    try_click(page, "[aria-controls='chatstrip-body']", label="expand chat strip")
    beat(page, 2500)

    # Collapse again to show the strip's compact mode with the new preview
    print("[ChatStrip] collapse")
    try_click(page, "[aria-controls='chatstrip-body']", label="collapse chat strip")
    beat(page, 1500)

    # ── Wrap with phase_completed so the stage stays put ────────────────
    push(
        page,
        "phase_completed",
        {**common, "state": "await_confirm", "phase": "phase_1", "index": 3, "total": 11},
    )
    push(page, "done", {**common, "state": "await_confirm", "reply": "", "session_id": "demo"})
    beat(page, 2000)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUT_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 820},
            record_video_dir=str(run_dir),
            record_video_size={"width": 1280, "height": 820},
        )
        ctx.add_init_script(EVENT_SOURCE_STUB)
        page = ctx.new_page()
        try:
            walk_phase1(page)
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
        final = run_dir / "phase1_walkthrough.webm"
        Path(webm).rename(final)
        print(f"\nWEBM: {final}")
        print(f"size: {final.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
