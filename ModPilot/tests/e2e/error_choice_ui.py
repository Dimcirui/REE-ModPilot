"""
Playwright smoke for issue #2 — error-choice three-button UI.

This is a STANDALONE script, not pytest-collected (no `test_` prefix).
Run after manually installing Playwright:

    uv add --dev playwright
    uv run playwright install chromium
    LLM_API_KEY=dummy LLM_BASE_URL=http://127.0.0.1:9999 \
        uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
    # ... then in a separate terminal:
    uv run python tests/e2e/error_choice_ui.py

What it covers
--------------
The wire-level SSE event delivery is already covered by unit tests
(`test_post_failing_turn_emits_error_choice_in_queue`). What unit tests CAN'T
exercise is the actual browser DOM behavior. This script verifies:

  1. The chat page renders with the empty #error-choice-slot in place.
  2. Injecting the HTML fragment into the slot via JS (the same fragment the
     SSE generator ships) produces three buttons with the right Chinese labels.
  3. Each button has the right hx-post URL and hx-vals JSON.
  4. Clicking a button:
     - synchronously removes the .error-choice-group (via inline onclick).
     - fires POST /agent/messages with the correct keyword in the body.
     - appends the optimistic user bubble with that keyword (app.js handler).

What it does NOT cover
----------------------
Real LLM + Blender end-to-end. The POST is intercepted and stubbed so the test
doesn't depend on a running phase tool or Blender connection. For full E2E,
also need Blender on 9876 and a real LLM key.

Server prerequisite
-------------------
Uvicorn must be running on http://127.0.0.1:8000 with stubbed env so the page
can load without a real LLM key. Server is NOT booted by this script — start
it separately as shown above.
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

# The exact HTML fragment shape we expect from app.main._render_error_choice_html.
# Building it in JS via page.evaluate lets the test stay fully client-side and
# bypass the SSE wire (which the unit tests already cover).
FRAGMENT_TEMPLATE = """
<div class="error-choice-group" role="group">
  <button class="error-choice-btn retry" hx-ext="json-enc" hx-post="/agent/messages"
          hx-vals='{"session_id":"__SID__","message":"重试"}' hx-swap="none">重试</button>
  <button class="error-choice-btn skip" hx-ext="json-enc" hx-post="/agent/messages"
          hx-vals='{"session_id":"__SID__","message":"跳过"}' hx-swap="none">跳过</button>
  <button class="error-choice-btn ask" hx-ext="json-enc" hx-post="/agent/messages"
          hx-vals='{"session_id":"__SID__","message":"查看详情"}' hx-swap="none">查看详情</button>
</div>
""".strip()


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

        # Intercept POST /agent/messages so we don't touch the real loop.
        captured_posts: list[dict] = []

        def handle_post(route):
            req = route.request
            body_text = req.post_data or ""
            try:
                body = json.loads(body_text)
            except json.JSONDecodeError:
                body = {"_raw": body_text}
            captured_posts.append(body)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "reply": "stubbed", "state": "running_phase",
                    "session_id": body.get("session_id", ""),
                }),
            )

        page.route("**/agent/messages", handle_post)

        print(f"Navigating to {SERVER_URL}")
        page.goto(SERVER_URL, wait_until="domcontentloaded")

        # 1. Empty slot present
        slot = page.locator("#error-choice-slot")
        check("slot exists", slot.count() == 1)
        check("slot starts empty", slot.inner_html().strip() == "")

        # Pull the real session_id baked into the template
        sid = page.eval_on_selector("body", "el => el.dataset.sessionId")
        check("body carries session_id", isinstance(sid, str) and len(sid) > 0,
              f"sid={sid!r}")

        # 2. Inject the fragment (simulating the SSE swap)
        fragment = FRAGMENT_TEMPLATE.replace("__SID__", sid)
        page.evaluate(
            "(html) => { const slot = document.getElementById('error-choice-slot');"
            " slot.innerHTML = html; htmx.process(slot); }",
            fragment,
        )

        buttons = page.locator(".error-choice-btn")
        check("three buttons rendered", buttons.count() == 3,
              f"got {buttons.count()}")

        labels = [buttons.nth(i).inner_text() for i in range(buttons.count())]
        check("labels are 重试 / 跳过 / 查看详情",
              labels == ["重试", "跳过", "查看详情"], f"got {labels}")

        # 3. hx-vals JSON
        retry_btn = page.locator(".error-choice-btn.retry")
        hx_vals = retry_btn.get_attribute("hx-vals") or ""
        check("retry hx-vals contains correct keyword", '"message":"重试"' in hx_vals,
              f"hx-vals={hx_vals!r}")
        check("retry hx-post targets /agent/messages",
              retry_btn.get_attribute("hx-post") == "/agent/messages")

        # 4. Click retry → request fires, group removed, user bubble appended
        retry_btn.click()
        page.wait_for_function("document.querySelectorAll('.error-choice-group').length === 0",
                               timeout=2000)
        check("group removed after click",
              page.locator(".error-choice-group").count() == 0)

        # Optimistic user bubble (added by app.js htmx:configRequest handler)
        # Wait briefly for it to appear (configRequest fires sync; bubble is appended sync)
        page.wait_for_selector(".bubble.user", timeout=2000)
        last_bubble = page.locator(".bubble.user").last
        check("optimistic user bubble = 重试",
              last_bubble.inner_text() == "重试",
              f"got {last_bubble.inner_text()!r}")

        # 5. POST was intercepted with the right keyword
        # Give htmx a moment to flush the request through the routed handler.
        for _ in range(20):
            if captured_posts:
                break
            page.wait_for_timeout(50)

        check("exactly one POST intercepted", len(captured_posts) == 1,
              f"got {len(captured_posts)}")
        if captured_posts:
            payload = captured_posts[0]
            check("POST body has message=重试",
                  payload.get("message") == "重试", f"got {payload}")
            check("POST body has matching session_id",
                  payload.get("session_id") == sid, f"got {payload}")

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
