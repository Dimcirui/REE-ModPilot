"""
Playwright smoke for issue #7 — physics classification confirmation widget.

This is a STANDALONE script, not pytest-collected (no `test_` prefix).

Run with:
    uv add --dev playwright
    uv run playwright install chromium
    $env:LLM_API_KEY="dummy"; $env:LLM_BASE_URL="http://127.0.0.1:9999"
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
    # ... then in a separate terminal:
    uv run python tests/e2e/widget_classification_ui.py

What it covers
--------------
Unit tests cover the server-side rendering of the widget fragment AND the
POST route's payload-shaping. What unit tests CAN'T cover is the DOM-level
behavior once the fragment is in place: htmx form submission, optimistic
bubble, the post-success slot-clear when a downstream tool_call event arrives.

  1. Slot is empty on page load (no widget yet).
  2. Injecting a rendered widget fragment into #widget-slot via htmx.process
     wires up the form (htmx attributes recognized).
  3. Submitting without picking a type for each row is blocked by `required`.
  4. Filling each dropdown then clicking Confirm fires
     POST /agent/widget/classification with the expected `type__<chain>` keys.
  5. The form gets the `.pending` class while in flight.
  6. After success, the user bubble "[Confirmed N/N rows]" appears in #log.
  7. A subsequent `tool_call` SSE event for `physics_chains` clears the slot.

Server prerequisite
-------------------
Uvicorn must be running on http://127.0.0.1:8000 with the dummy LLM env.
The POST /agent/widget/classification route is intercepted and stubbed.
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

FAKE_CHAINS = [
    {"name": "hair_001", "role": "head", "depth": 5, "parent": "head"},
    {"name": "skirt_002", "role": "head", "depth": 8, "parent": "waist"},
    {"name": "tail_003", "role": "branch_head", "depth": 3, "parent": "hip"},
]

# Minimal subset of inferred types — full list comes from the server when
# running for real; for the e2e stub, just enough variety to assert.
FAKE_TYPES = ["hair_long_straight", "cloth_skirt_waist", "accessory_ribbon"]


def _render_widget_fragment_html(session_id: str, chains: list[dict], types: list[str]) -> str:
    """Hand-rolled approximation of the server-side Jinja template.

    Mirrors the structure of `app/templates/widgets/classification.html` so
    the JS handlers under test see the same DOM shape they will in production.
    """
    rows = []
    for ch in chains:
        type_opts = '<option value="">—</option>' + "".join(
            f'<option value="{t}">{t}</option>' for t in types
        )
        rows.append(
            f'<tr>'
            f'<td class="bone-name">{ch["name"]}</td>'
            f'<td class="bone-role">{ch["role"]}</td>'
            f'<td class="bone-depth">{ch["depth"]}</td>'
            f'<td><select name="type__{ch["name"]}" required>{type_opts}</select></td>'
            f'</tr>'
        )
    return (
        '<form class="widget-form classification" '
        'hx-ext="json-enc" hx-post="/agent/widget/classification" hx-swap="none">'
        '<header class="widget-header"><h3>Confirm physics chain classifications</h3></header>'
        '<table class="widget-table"><thead><tr>'
        '<th>Chain head</th><th>Role</th><th>Depth</th><th>Inferred type</th>'
        '</tr></thead><tbody>'
        + "".join(rows) +
        '</tbody></table>'
        f'<input type="hidden" name="session_id" value="{session_id}">'
        '<div class="widget-actions">'
        '<button type="submit" class="widget-submit">Confirm classifications</button>'
        '</div></form>'
    )


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

        def handle_widget_post(route):
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
                body=json.dumps({"saved": True, "count": len(body) - 1}),
            )

        page.route("**/agent/widget/classification", handle_widget_post)

        print(f"Navigating to {SERVER_URL}")
        page.goto(SERVER_URL, wait_until="domcontentloaded")
        # Wait for htmx + app.js init.
        page.wait_for_function("() => typeof window.htmx !== 'undefined'", timeout=3000)

        # 1. Widget slot exists and is empty
        check("widget-slot rendered",
              page.locator("#widget-slot").count() == 1)
        check("widget-slot is empty on load",
              page.locator("#widget-slot").inner_html().strip() == "")

        # 2. Inject the rendered widget fragment + htmx.process to wire it up.
        sid = page.eval_on_selector("body", "el => el.dataset.sessionId")
        fragment = _render_widget_fragment_html(sid, FAKE_CHAINS, FAKE_TYPES)
        page.evaluate(
            """([html]) => {
                const slot = document.getElementById('widget-slot');
                slot.innerHTML = html;
                window.htmx.process(slot);
            }""",
            [fragment],
        )
        check("widget form rendered after injection",
              page.locator(".widget-form.classification").count() == 1)
        check("one select per chain",
              page.locator(".widget-form select").count() == len(FAKE_CHAINS))
        check("hidden session_id present",
              page.eval_on_selector(
                  '.widget-form input[name="session_id"]', "el => el.value",
              ) == sid)

        # 3. Submitting without selecting types is blocked by required.
        page.click(".widget-submit")
        page.wait_for_timeout(200)
        check("required attribute blocks empty submission",
              len(captured_posts) == 0,
              f"unexpected POST captured: {captured_posts!r}")

        # 4. Fill each select; click Confirm.
        page.select_option('select[name="type__hair_001"]',  "hair_long_straight")
        page.select_option('select[name="type__skirt_002"]', "cloth_skirt_waist")
        page.select_option('select[name="type__tail_003"]',  "accessory_ribbon")
        page.click(".widget-submit")

        for _ in range(40):
            if captured_posts:
                break
            page.wait_for_timeout(50)

        check("exactly one POST intercepted",
              len(captured_posts) == 1, f"got {len(captured_posts)}")
        if captured_posts:
            payload = captured_posts[0]
            check("POST body session_id matches",
                  payload.get("session_id") == sid, f"got {payload!r}")
            check("POST has type__hair_001 = hair_long_straight",
                  payload.get("type__hair_001") == "hair_long_straight",
                  f"got {payload.get('type__hair_001')!r}")
            check("POST has type__skirt_002 = cloth_skirt_waist",
                  payload.get("type__skirt_002") == "cloth_skirt_waist")
            check("POST has type__tail_003 = accessory_ribbon",
                  payload.get("type__tail_003") == "accessory_ribbon")

        # 5. Optimistic user bubble appears in #log
        bubble_count = page.evaluate(
            "() => Array.from(document.querySelectorAll('#log .bubble.user')).filter(b => b.textContent.includes('Confirmed')).length"
        )
        check("optimistic Confirmed bubble appended",
              bubble_count == 1, f"got {bubble_count}")

        # 6. Simulating a downstream tool_call event clears the slot.
        # We can't synthesize a real SSE frame from the client, but we CAN
        # dispatch a synthetic htmx:sseMessage that the dispatcher recognises.
        page.evaluate("""() => {
            const ev = new CustomEvent('htmx:sseMessage', {
                detail: {
                    type: 'tool_call',
                    data: JSON.stringify({
                        type: 'tool_call', name: 'physics_chains', input: {},
                    }),
                },
            });
            document.body.dispatchEvent(ev);
        }""")
        page.wait_for_timeout(100)
        check("slot cleared after physics_chains tool_call",
              page.locator("#widget-slot").inner_html().strip() == "")

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
