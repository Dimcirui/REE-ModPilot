# tests/e2e — Browser smokes via Playwright

Opt-in browser tests. Not collected by `pytest` (no `test_` prefix on files).
Run as standalone Python scripts after installing Playwright.

## Setup (one-time)

```bash
cd ModPilot
uv add --dev playwright
uv run playwright install chromium     # ~150 MB browser binary
```

## Run

1. Boot uvicorn in one terminal:
   ```bash
   LLM_API_KEY=dummy LLM_BASE_URL=http://127.0.0.1:9999 \
       uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

2. In another terminal:
   ```bash
   uv run python tests/e2e/error_choice_ui.py
   ```

## What's covered here

| Script | Issue | What it asserts |
|--------|-------|-----------------|
| `error_choice_ui.py` | #2 | The three-button slot renders, click removes the group, click fires `POST /agent/messages` with the right keyword, optimistic user bubble appears. |

## What's NOT covered (intentionally)

These scripts stub `POST /agent/messages` via `page.route(...)`, so they do NOT
test the real agent loop. The wire-level SSE event delivery from `loop.py` to
the queue is covered by:

- `tests/unit/test_agent_loop_events.py::test_phase_failure_emits_error_choice_event`
- `tests/unit/test_sse_routes.py::test_post_failing_turn_emits_error_choice_in_queue`

Full end-to-end against Blender + a real LLM is the manual procedure in
`lesson.md` / Issue #2 plan's "Manual end-to-end" section.
