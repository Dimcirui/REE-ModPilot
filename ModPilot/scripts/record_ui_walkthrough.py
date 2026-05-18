"""Wide-shot capability sweep of the ModPilot React frontend, recorded as webm.

Walks /config (LLM settings) and / (ChatPage with session config form, viewport pane,
chat input). Captures the current shipped UI so we can decide what an
"adaptive UI" should add/remove.

Run with:
    .venv\\Scripts\\python.exe scripts\\record_ui_walkthrough.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "artifacts" / "ui_walkthroughs"
URL = "http://localhost:5173"


def beat(page: Page, ms: int = 700) -> None:
    page.wait_for_timeout(ms)


def try_click(page: Page, selector: str, *, label: str, timeout: int = 3000) -> bool:
    try:
        page.locator(selector).first.click(timeout=timeout)
        print(f"  click  : {label}")
        return True
    except PWTimeout:
        print(f"  skip   : click {label}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  err    : click {label} -> {exc!r}")
        return False


def try_fill(page: Page, selector: str, value: str, *, label: str, timeout: int = 3000) -> bool:
    try:
        page.locator(selector).first.fill(value, timeout=timeout)
        print(f"  fill   : {label} = {value!r}")
        return True
    except PWTimeout:
        print(f"  skip   : fill {label}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  err    : fill {label} -> {exc!r}")
        return False


def sweep_config_page(page: Page) -> None:
    print("[/config] LLM provider settings")
    page.goto(f"{URL}/config", wait_until="domcontentloaded")
    beat(page, 1500)

    # Highlight the form by scrolling and focusing fields
    try_click(page, "select", label="provider dropdown")
    beat(page, 600)
    try_click(page, "input[type='text']", label="first text field (model)")
    beat(page, 500)


def sweep_chat_page(page: Page) -> None:
    print("[/] ChatPage (full app surface)")
    page.goto(URL, wait_until="domcontentloaded")
    beat(page, 1800)

    # Header + initial layout
    page.mouse.move(640, 60)
    beat(page, 700)

    # Session config — show paths, body parts radios, hunter type
    print("  ->Session config form")
    try_fill(
        page,
        "input[placeholder*='model.fbx']",
        r"C:\demo\models\character.fbx",
        label="model path",
    )
    beat(page, 400)
    try_fill(
        page,
        "input[placeholder*='textures']",
        r"C:\demo\textures",
        label="texture dir",
    )
    beat(page, 400)
    try_fill(
        page,
        "input[placeholder*='mod_root']",
        r"C:\demo\mod_out",
        label="mod root",
    )
    beat(page, 400)
    try_fill(page, "input[placeholder='AuthorName']", "DemoAuthor", label="author")
    try_fill(page, "input[placeholder='CharacterName']", "DemoChar", label="character")
    beat(page, 400)

    # Body parts radios — single-pick now, cycle through to showcase
    print("  ->Cycle Body part radios")
    for label in ["Arms (1)", "Helmet (3)", "Legs (4)", "Waist (5)", "Body (2)"]:
        try_click(page, f"text={label}", label=f"body: {label}")
        beat(page, 300)

    # Hunter type
    print("  ->Hunter type")
    for label in [
        "Male / Female armor",
        "Female / Male armor",
        "Female / Female armor (default)",
    ]:
        try_click(page, f"text={label}", label=f"hunter: {label}")
        beat(page, 300)

    # BoneSystem toggle
    try_click(page, "text=Use BoneSystem", label="BoneSystem on")
    beat(page, 400)
    try_click(page, "text=Use BoneSystem", label="BoneSystem off")
    beat(page, 400)

    # Try the chat input — type a message but DON'T necessarily submit,
    # since Blender is offline and the agent will error early.
    print("  ->Chat input typing demo")
    msg = "What can you do?"
    # MessageInput is a textarea — find it generically
    try_fill(page, "textarea", msg, label="chat input")
    beat(page, 700)

    # Submit anyway to capture the error/status flow (it's part of "what it can do now")
    try_click(page, "button:has-text('Send')", label="Send")
    beat(page, 2500)

    # Final lingering shot
    beat(page, 1200)


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
        page = ctx.new_page()
        try:
            sweep_config_page(page)
            sweep_chat_page(page)
        except Exception as exc:  # noqa: BLE001
            print(f"!! sweep raised: {exc!r}")
        finally:
            video = page.video
            ctx.close()
            browser.close()

        if video is None:
            print("ERROR: no video was recorded.")
            return 1

        webm = video.path()
        final = run_dir / "walkthrough.webm"
        Path(webm).rename(final)
        print(f"\nWEBM: {final}")
        print(f"size: {final.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
