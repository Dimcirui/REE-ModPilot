"""
Playwright smoke for issue #3 — session config form.

This is a STANDALONE script, not pytest-collected (no `test_` prefix).
Run after manually installing Playwright:

    uv add --dev playwright
    uv run playwright install chromium
    $env:LLM_API_KEY="dummy"; $env:LLM_BASE_URL="http://127.0.0.1:9999"
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
    # ... then in a separate terminal:
    uv run python tests/e2e/session_config_form.py

What it covers
--------------
Server-side path validation and storage are covered by unit tests
(`test_session_config_form.py`). What unit tests CAN'T exercise is the
browser DOM behavior. This script verifies:

  1. Form renders on page load with Start disabled and badge hidden.
  2. Start enables only after every required input is filled.
  3. Clicking Start fires POST /agent/config with the nested
     {session_id, config: {...}} JSON shape.
  4. On success: form hides (body.config-locked), badge appears,
     localStorage carries the saved config under modpilot.config.v1.
  5. On page reload, the form rehydrates from localStorage.
  6. Clicking Edit unlocks the form again.
  7. 422 field_errors response surfaces in #config-errors and marks
     the offending input with .invalid.

What it does NOT cover
----------------------
Real LLM + Blender end-to-end. POST /agent/config is intercepted and
stubbed — the test doesn't touch the actual filesystem-existence checks.

Server prerequisite
-------------------
Uvicorn must be running on http://127.0.0.1:8000 with the dummy LLM env.
"""

from __future__ import annotations

import json
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright is not installed. Run:", file=sys.stderr)
    print("    uv add --dev playwright", file=sys.stderr)
    print("    uv run playwright install chromium", file=sys.stderr)
    sys.exit(2)


SERVER_URL = "http://127.0.0.1:8000/"

VALID_CONFIG = {
    "model_path": "C:/models/hero.fbx",
    "model_type": "MMD",
    "texture_dir": "C:/models/tex",
    "mod_root": "C:/mods/myhero",
    "author": "Acme",
    "character_name": "Hero",
    "use_bone_system": True,
    "body_parts": ["1", "2"],
}


def _fill_form(page, cfg: dict) -> None:
    page.fill('input[name="config.model_path"]',     cfg["model_path"])
    page.select_option('select[name="config.model_type"]', cfg["model_type"])
    page.fill('input[name="config.texture_dir"]',    cfg["texture_dir"])
    page.fill('input[name="config.mod_root"]',       cfg["mod_root"])
    page.fill('input[name="config.author"]',         cfg["author"])
    page.fill('input[name="config.character_name"]', cfg["character_name"])
    if cfg.get("use_bone_system"):
        page.check('input[name="config.use_bone_system"]')
    for v in cfg.get("body_parts", []):
        page.check(f'input[name="config.body_parts"][value="{v}"]')


def run() -> int:
    failures: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  PASS  {label}")
        else:
            failures.append(f"{label} — {detail}" if detail else label)
            print(f"  FAIL  {label}  {detail}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        captured_posts: list[dict] = []
        # Toggle in handler to test the 422 path without re-routing later.
        stub_state = {"mode": "ok"}

        def handle_config_post(route):
            req = route.request
            body_text = req.post_data or ""
            try:
                body = json.loads(body_text)
            except json.JSONDecodeError:
                body = {"_raw": body_text}
            captured_posts.append(body)
            if stub_state["mode"] == "ok":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "session_id": body.get("session_id", ""),
                        "saved": True,
                    }),
                )
            else:
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps({
                        "detail": {
                            "field_errors": {
                                "model_path": "File not found",
                            }
                        }
                    }),
                )

        page.route("**/agent/config", handle_config_post)

        print(f"Navigating to {SERVER_URL}")
        page.goto(SERVER_URL, wait_until="domcontentloaded")

        # Clear any stale localStorage from a previous run so the test starts
        # from a clean form.
        page.evaluate("() => localStorage.removeItem('modpilot.config.v1')")
        page.reload(wait_until="domcontentloaded")

        # 1. Form rendered, Start disabled, badge hidden, body NOT locked
        check("config form rendered",
              page.locator("#config-form").count() == 1)
        check("Start button starts disabled",
              page.locator("#config-start-btn").is_disabled())
        check("saved badge starts hidden",
              not page.locator("#config-saved-badge").is_visible())
        check("body not in config-locked state",
              not page.evaluate("() => document.body.classList.contains('config-locked')"))

        # 2. Filling all required fields enables Start
        _fill_form(page, VALID_CONFIG)
        # Updating the last checkbox triggers `change`, which fires the
        # validation handler — wait a tick.
        page.wait_for_function(
            "() => !document.getElementById('config-start-btn').disabled",
            timeout=2000,
        )
        check("Start enables once all fields are filled",
              not page.locator("#config-start-btn").is_disabled())

        # 3. Click Start → POST fires with nested {session_id, config}
        sid = page.eval_on_selector("body", "el => el.dataset.sessionId")
        page.click("#config-start-btn")
        # Wait for the routed handler to capture the POST.
        for _ in range(40):
            if captured_posts:
                break
            page.wait_for_timeout(50)

        check("exactly one POST intercepted",
              len(captured_posts) == 1, f"got {len(captured_posts)}")
        if captured_posts:
            payload = captured_posts[0]
            check("POST body has session_id",
                  payload.get("session_id") == sid, f"got {payload!r}")
            cfg_field = payload.get("config", {})
            check("POST body has nested config dict",
                  isinstance(cfg_field, dict) and cfg_field.get("model_path") == VALID_CONFIG["model_path"],
                  f"got {payload!r}")
            check("POST body body_parts is a list",
                  cfg_field.get("body_parts") == VALID_CONFIG["body_parts"],
                  f"got {cfg_field.get('body_parts')!r}")
            check("POST body use_bone_system is boolean true",
                  cfg_field.get("use_bone_system") is True,
                  f"got {cfg_field.get('use_bone_system')!r}")

        # 4. After success: body gets config-locked, form hidden, badge visible
        page.wait_for_function(
            "() => document.body.classList.contains('config-locked')",
            timeout=2000,
        )
        check("body gains config-locked",
              page.evaluate("() => document.body.classList.contains('config-locked')"))
        check("form hidden after save",
              not page.locator("#config-form").is_visible())
        check("saved badge visible after save",
              page.locator("#config-saved-badge").is_visible())

        # 5. localStorage persists the config
        stored_raw = page.evaluate(
            "() => localStorage.getItem('modpilot.config.v1')"
        )
        check("localStorage has modpilot.config.v1",
              isinstance(stored_raw, str) and len(stored_raw) > 0,
              f"got {stored_raw!r}")
        if stored_raw:
            stored = json.loads(stored_raw)
            check("localStorage model_path matches",
                  stored.get("model_path") == VALID_CONFIG["model_path"],
                  f"got {stored.get('model_path')!r}")
            check("localStorage body_parts matches",
                  stored.get("body_parts") == VALID_CONFIG["body_parts"],
                  f"got {stored.get('body_parts')!r}")

        # 6. Reload → form rehydrates from localStorage
        page.reload(wait_until="domcontentloaded")
        # Inputs should be re-populated
        rehydrated_path = page.eval_on_selector(
            'input[name="config.model_path"]', "el => el.value",
        )
        check("model_path rehydrated after reload",
              rehydrated_path == VALID_CONFIG["model_path"],
              f"got {rehydrated_path!r}")
        rehydrated_parts = page.evaluate(
            "() => Array.from(document.querySelectorAll('input[name=\"config.body_parts\"]:checked')).map(e => e.value)"
        )
        check("body_parts rehydrated after reload",
              sorted(rehydrated_parts) == sorted(VALID_CONFIG["body_parts"]),
              f"got {rehydrated_parts!r}")
        check("Start enabled after rehydrate",
              not page.locator("#config-start-btn").is_disabled())

        # 7. 422 path: switch stub to error mode, click Start, expect field error
        captured_posts.clear()
        stub_state["mode"] = "err"
        page.click("#config-start-btn")
        page.wait_for_function(
            "() => document.querySelector('#config-errors').textContent.includes('File not found')",
            timeout=2000,
        )
        check("422 surfaces field error in #config-errors",
              "File not found" in page.locator("#config-errors").inner_text())
        check("offending input gets .invalid class",
              page.locator('input[name="config.model_path"].invalid').count() == 1)
        check("form stays visible after 422",
              not page.evaluate("() => document.body.classList.contains('config-locked')"))

        # 8. Edit button unlocks form when locked. Switch back to ok-mode +
        #    Click Start again to lock, then click Edit.
        stub_state["mode"] = "ok"
        captured_posts.clear()
        page.click("#config-start-btn")
        page.wait_for_function(
            "() => document.body.classList.contains('config-locked')",
            timeout=2000,
        )
        page.click("#config-edit-btn")
        check("Edit button unlocks form",
              not page.evaluate("() => document.body.classList.contains('config-locked')"))
        check("form visible again after Edit",
              page.locator("#config-form").is_visible())

        browser.close()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
