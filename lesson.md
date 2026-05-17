# REE-ModPilot

> Read this file at the start of every session. Orientation up top so you don't have to re-explore; lesson log at the bottom so you don't repeat past mistakes.

---

## What this project is

AI-guided Blender automation for crafting **RE Engine** character mods (MHWs / MHWI / RE4 / RE9). A FastAPI backend that drives Blender through the `blender-mcp` socket (port 9876), orchestrating the **Modding-Toolkit** Blender addon's `bpy.ops.modder.* / mhws.*` operators. The LLM makes classification decisions *inside* phase tools and orchestrates *between* phases; deterministic Python handles everything within a phase. MVP target: MHWs single-game, full plan.md videos 1-7, L3 acceptance (mod runs in-game).

## Layout

```
REE-ModPilot/
├── ModPilot/                     # FastAPI backend (uv-managed)
│   ├── app/
│   │   ├── main.py               # routes: /, /health, /scene_info, /agent/chat (legacy),
│   │   │                         #         /agent/messages, /agent/stream/{sid}, /exec (debug)
│   │   ├── agent/loop.py         # hand-rolled ReAct; AgentLoop.step() is the entry point
│   │   ├── agent/error_handler.py
│   │   ├── agent/prompts.py
│   │   ├── blender/client.py     # TCP socket to blender-mcp addon (port 9876)
│   │   ├── blender/state.py      # SceneCache
│   │   ├── llm/client.py         # Provider-agnostic; from_settings() picks adapter
│   │   ├── llm/{anthropic,openai}_provider.py
│   │   ├── phases/               # one module per phase tool, all subclass PhaseTool
│   │   │                         # (pose_correction, skeleton_align, vertex_groups,
│   │   │                         #  physics_bones, material, batch_export, mesh_cleanup,
│   │   │                         #  query_tools, setup)
│   │   ├── templates/chat.html   # Stage 5 htmx + SSE chat shell
│   │   └── routes/               # currently empty; routes live in main.py
│   ├── static/                   # app.css, app.js, vendor/{htmx.min.js, htmx-ext-sse.js, htmx-ext-json-enc.js}
│   └── tests/{unit,integration,e2e}/   # e2e = Playwright browser smokes (opt-in: error_choice_ui.py, session_config_form.py)
├── cli.py                        # interactive CLI client (talks to POST /agent/chat)
├── verify_blender_mcp.py         # Stage 0 connectivity smoke (5 checks)
├── docs/                         # design.md, backlog.md, plan.md, agent_workflow.md,
│                                 # plugin_api.md, blender-mcp-analysis.md
├── README.md, CLAUDE.md, AGENTS.md
└── lesson.md                     # this file
```

## Current state (as of 2026-05-17)

- **Issue #8 closed** (final MVP P0): `verify_mvp.py` at repo root + `verify_mvp_config.example.json` template + `docs/demo_setup.md` user-facing prerequisite + L3 acceptance walkthrough. The script bypasses the agent loop / LLM entirely — it imports phase tools (`SetupValidateScene`, `SetupImportMHWilds`, `PoseCorrection`, `SkeletonAlign`, `VertexGroups`, `PhysicsTransplant`, `PhysicsClassification`, `PhysicsChains`, `MaterialInspect`, `MaterialSetup`, `MaterialGenerate`, `BatchExport`) directly and feeds them config-supplied classification mappings (`x_preset`, `inferred_types`, `texture_mapping`, `preset_mapping`) so the run is fully deterministic. Adds phase-level success aggregation around the per-tool `require_finished()` checks, plus a post-Phase-6 file existence + non-zero-size sweep against `natives_root/expected_files`. CLI: `uv run python ../verify_mvp.py --config ../verify_mvp_config.json [--phases setup phase_1_2_3 ...] [--report out.json]`. `docs/demo_setup.md` covers prerequisites, MMD model recommendations (no assets in repo per D15), `.fbxskel.7` extraction, mod folder layout, full config-field reference, and a symptom→failure-phase table for the in-game L3 acceptance pass. All MVP P0 items 🟢; the actual L3 run remains user-side because it requires a real MHWs install + a self-provided MMD source.

## Current state (as of 2026-05-16, later)

- Stages 0-4 complete. Phase 1→6 verified end-to-end against real Blender.
- **Issues #4 / #5 / #6 closed** (P2): source-model type auto-inference + preset supplement + custom-preset paths. New `setup_infer` phase slot between `setup_validate` and `setup_import`; `InferModelType` reports a 4-band decision (`exact` 100% / `supplement` 80-99% / `custom` 1-79% / `unsupported` 0%) over Modding-Toolkit's `assets/presets/import/` folder. `PresetSupplementWrite` writes `<base>_extended.json` (additive, never overwrites shipped); `PresetCustomWrite` writes `<character_name>_custom.json` from an LLM-confirmed full mapping. `X_PRESETS` is now a runtime-mutable set seeded by the lifespan handler. Session-config form's `model_type` dropdown is dynamic (`GET /app/x_presets`); SSE `model_type_inferred` back-fills the inferred value. `[强制自定义]` button on the error_choice widget when category=`unsupported_rig`; sends `[FORCE_CUSTOM]` for re-inference with `force_custom=true`. 50 new unit tests (catalog + inference + write). Live-verified against the toolkit's 13 shipped X-presets.
- Stage 5 P0 frontend complete (waves 1-5). Waves 1-4 same-day on 2026-05-15: issue #1 (htmx+SSE chat), issue #2 (error-choice three-button UI), issue #3 (session-config form, 8 fields), issue #7 (confirmation widgets for Phase 4A classification + Phase 5 material mapping), issue #9 (global config UI with persistence). Wave 5 on 2026-05-16: viewport screenshot side-panel — chat shell now has a 2-column `#main-area`, right column shows a `<img>` pulled via `GET /viewport_screenshot` on a 5 s `setInterval` (pauses on `document.hidden`; uses `fetch`+Blob URL so 503 → "Blender unreachable" badge instead of broken-image icon). `BlenderClient.get_viewport_screenshot` wraps the addon's quirk that errors come back as `status="success"` + `result["error"]` (not top-level status="error"). Routes inventory: `GET /`, `POST /agent/messages`, `GET /agent/stream/{session_id}`, `POST /agent/config`, `POST /agent/widget/classification`, `POST /agent/widget/material`, `GET/POST /app/config`, `GET /config`, **`GET /viewport_screenshot`**. Legacy `POST /agent/chat` preserved untouched for `cli.py`.
- AgentLoop publishes structured events via an optional `event_sink: Callable[[dict], None]` constructor param. 11 event types: `message / state / phase_started / phase_completed / tool_call / tool_result / error_choice / widget_classification / widget_material / error / done`.
- AgentLoop also accepts `session_config: dict | None` (issue #3) — values are appended to the system prompt as a final "Pre-collected session parameters" block, so the LLM passes them through to phase tool calls instead of asking the user mid-run. Routing via `app.state.session_configs` keyed by `session_id`.
- New SSE event `error_choice` ships an HTML fragment (not JSON) so htmx `sse-swap` can drop the three buttons (重试 / 跳过 / 查看详情) into `#error-choice-slot` directly. Buttons post the keyword back to `/agent/messages`, routed through `_handle_error_choice`.
- Issue #9 introduces `~/.modpilot/config.json` (cross-platform, via `Path.home()`) layered on top of the .env-derived `Settings` singleton. Loader sits in `app/config_store.py`; persisted file is read once in `lifespan()` and applied via `apply_to_settings(settings, ...)`. `LLMClient.from_settings()` is wrapped in try/except at startup so a missing api_key doesn't crash the server — `_require_llm()` 503s only when a route actually needs the LLM. `POST /app/config` with empty `llm_api_key` field PRESERVES the existing key (the only way to clear is to delete the JSON file directly).
- Issue #7 confirmation widgets follow the same HTML-fragment-over-SSE pattern. After `physics_classification` / `material_inspect` success, AgentLoop emits a `widget_*` event; `agent_stream` renders a Jinja partial (`app/templates/widgets/{classification,material}.html`) into the data: field; `<div id="widget-slot" sse-swap="widget_classification widget_material">` swaps it in. Form submissions to `POST /agent/widget/{classification,material}` re-package the flat `type__<chain>` / `texmap__<idx>__<mat>` keys as `[CONFIRMED_CLASSIFICATIONS]` / `[CONFIRMED_MATERIAL_MAPPING]`-prefixed JSON, feed `loop.step()`, and the LLM consumes the dict directly per the system-prompt protocol block. `app.js` `tool_call` dispatcher clears the slot when `physics_chains` / `material_setup` / `material_generate` fires, preventing stale-widget re-submission.
- All hx-post elements use `hx-ext="json-enc"` per-element (NOT inherited from body) — htmx 1.x's default form-urlencoded encoding 422s against our Pydantic JSON endpoint.
- The session-config form uses flat `config.foo` input names (since FormData has no native nesting), then re-packs to `{session_id, config: {...}}` in `htmx:configRequest` before `json-enc` serializes. localStorage key `modpilot.config.v1` rehydrates the form across page refresh.
- All Stage 5 P0 items 🟢. Remaining MVP P0: `verify_mvp.py`, `docs/demo_setup.md`, full L3 acceptance run.

## How to run

```bash
# One-time
cd ModPilot
uv sync                                              # install from uv.lock

# Dev server (Blender must be running, blender-mcp addon enabled, "Connect to Claude" clicked)
uv run uvicorn app.main:app --reload                 # http://localhost:8000/

# CLI client (parallel to web UI; shares app.state.agent_sessions by session_id)
python cli.py

# Tests
uv run pytest -m unit                                # no Blender required
uv run pytest -m integration                         # requires Blender on 9876
uv run pytest tests/unit/test_agent_loop_events.py   # single file
uv run ruff check app tests

# Playwright browser smokes (issues #2 + #3; opt-in)
# One-time: uv add --dev playwright && uv run playwright install chromium
uv run python tests/e2e/error_choice_ui.py           # issue #2 — uvicorn must be running on 8000
uv run python tests/e2e/session_config_form.py       # issue #3 — same server prerequisite

# Stage 0 connectivity smoke
uv run python ../verify_blender_mcp.py
```

## Where to dive deeper

- `CLAUDE.md` — Claude-specific footguns, the blender-mcp wire protocol cheat sheet, memory map. **Always read.**
- `AGENTS.md` — hard rules sourced from design.md, common commands, naming/style.
- `docs/design.md` — A/B/C/D/E-layer design decisions (all 🟢 decided). Rationale + alternatives + escape hatches.
- `docs/backlog.md` — P0-P3 implementation tasks with status badges.
- `docs/agent_workflow.md` — machine-readable execution manual for the agent (the "system prompt extension").
- `docs/plugin_api.md` — Modding-Toolkit operator reference (the "API" the agent wraps).
- `docs/plan.md` — 7-video mod-making workflow (human reference only; not injected into the agent).

## Hard rules (from AGENTS.md — non-negotiable)

1. LLM never manages operator-level calls. It picks phases and makes classification decisions inside them.
2. No `execute_code` exposed as an LLM tool in MVP.
3. State cache is single-source in `app/blender/state.py`. Phase tools never hold local state.
4. Phase tools return `Result<state_diff, structured_error>`. Never raw exceptions to the LLM.
5. `import anthropic` / `import openai` only inside `app/llm/`. Business code uses `LLMClient.chat`.
6. Don't modify existing tests unless the task is about them. Add new tests instead.
7. Communication with user: 中文; code/comments/commits: pure English.

## Project quirks worth remembering

- Windows 11 + Blender 4.3.2. PowerShell ≠ Bash syntax (see global lesson.md).
- `blender-mcp/addon.py` is installed into Blender separately, NOT vendored. Don't look for `blender-mcp/` in this repo.
- `LLMClient.chat()` is **synchronous** in both providers. Token streaming is not supported. SSE granularity is at the tool-call / phase-advance level (L2), not tokens.
- DeepSeek V4 sometimes emits tool calls as inline DSML markup (`<｜｜DSML｜｜tool_calls>...`) instead of using the API's `tool_calls` field. `loop.py` parses both paths. New emit sites must cover both.

---

# Lesson Log

<!-- One line per fix: symptom → resolution. Newest at top. -->
- Issue #14 interrupt mechanism: bail point MUST be at the top of the `_run_react_turn` for-loop (i.e. *between* rounds) and inside the per-tool-call inner break — NEVER between `history.append(assistant tool_use msg)` and the matching `history.append(user tool_result msg)`. The Anthropic API rejects unmatched `tool_use` blocks with HTTP 400. The existing try/finally placeholder injector (the "Skipped — preceding tool call failed" branch) covers the inner-loop break path so partial drains stay balanced. Reset the flag on bail-out — a sticky flag silently swallows the user's NEXT message. Also: on the JS side, ignore Escape when the focus is inside an `INPUT`/`TEXTAREA`/`SELECT` (otherwise Esc-to-blur kills the agent) and skip the POST when status isn't `thinking` (no in-flight phase to interrupt).
- Phase 1 scale-align method changed in issue #13: was mesh-bbox-Z-max ratio, is now arm-bone average-Z ratio (upperarm/forearm/hand × L/R). The bbox method silently failed on rigs with anything sticking above the head (hats, weapons, hair flying up). Note that this also means the existing test_phases.py PoseCorrection class needs an autouse fixture to stub `_resolve_arm_candidates` — otherwise `discover_preset_dir` triggers an extra `execute_and_extract` call on the mock client and inflates `call_count` past existing assertions (3 → 4 for MMD/Endfield paths). Pattern: when a phase tool starts doing a side-channel Blender query, audit `call_count`-based assertions in its test file. The check itself isn't broken, but it becomes a coupling that propagates.
- `PresetSupplementWrite` always writes `<base>_extended.json` — if the user (or LLM) passes `base_preset_name="VRChat_extended"`, the result is `VRChat_extended_extended.json`. Functionally correct (merges fine, registers via `add_x_preset`) but the chain of `_extended` suffixes is ugly when the LLM re-supplements a preset it just wrote. Verified on the Eku VRChat avatar run: turn 1 produced `VRChat_extended` @ 83.33%, turn 2 then produced `VRChat_extended_extended` @ 98.15%. Polish path (post-MVP): when the base already ends with `_extended`, mutate it in place instead of generating a deeper suffix.
- DS's chat reply is not authoritative about coverage — on the Eku run, DS announced "总槽位数：54/54（100% 覆盖）" but the file it actually wrote was 53/54 (98.15%); it forgot to deduct the unmappable `spine_03` slot. Don't trust the LLM's natural-language summary of tool results — re-run `InferModelType` (or read the diff returned by the write tool) to get ground truth.
- Unity Humanoid spec calls the pinky finger **`Little`**, not `pinky`. When scanning a humanoid rig for finger bones, grep both `little` AND `pinky` (and don't forget `Little Proximal.{L,R}` with a space if the rig uses the literal spec names — see Eku). VRChat preset's `pinky_*_L/R` slots already include `LittleProximal_L` / `Little1_L` etc. as candidates, but the dot-separator variant (`Little Proximal.L`) only matches after a supplement.
- DS reasoning-mode (`deepseek-v4-flash`) turn latency is **~3 min per turn** when the system prompt carries the full `agent_workflow.md` + 20+ tool schemas. Most of that time is internal CoT before any visible content streams. Acceptable for automation; painful for chat UX. `OllamaProvider` currently doesn't expose Ollama's `think: False` request-body field — adding it would short-circuit reasoning when the user wants snappier replies (issue for the backlog).
- `curl --data` with UTF-8 Chinese on Windows Git Bash mangles the body (cp1252 round-trip), and FastAPI then 422s with `"There was an error parsing the body"`. Use `curl --data-binary @file.json`, or drive the API from Python `httpx` (which encodes UTF-8 natively). This bit on the very first POST of the Eku DS-as-agent probe.
- Phase-tool param-name drift between schema and runtime is invisible from outside the agent loop: e.g. `MaterialInspect`/`MaterialSetup` take `target_object` (not `mesh_object`), `MaterialGenerate` takes `mesh_collection` + `preset_mapping` (not `mesh_object` + `material_presets`), `PhysicsChains` takes `inferred_types` (not `chain_classifications`). The LLM-driven path normally hides this because the prompt+schema flow ensures names match. When writing direct-call harnesses like `verify_mvp.py`, grep `tool_schema` for each phase you call and use the exact key names — don't mirror what the agent loop appears to do, mirror what the JSON schema declares.
- `PhaseError` is a `@dataclass`, not an `Exception` subclass → `raise PhaseError(...)` inside a phase tool's helper triggers `TypeError: catching classes that do not inherit from BaseException`. Helpers that may fail must return `(value, error)` tuples instead of raising. Pattern used in `app/phases/infer_model_type.py:_read_armature_bones`.
- Modding-Toolkit ships **13** X-presets (not 14) in `assets/presets/import/`: `街霸6.json` lives in that folder but declares `preset_info.type="Y_PRESET"`. The X-preset enumeration must filter by `preset_info.type == "X_PRESET"`, not by folder location. Mirror the filter in `SHIPPED_X_PRESETS` so the boot fallback matches the live count.
- Standard X-preset slot list is **51 keys**, not 58. The "58 standard bone slots" figure in `plugin_api.md` refers to Modding-Toolkit's preset *editor* scaffolding (`modder.init_editor`'s populated slots), not what shipped presets actually fill. For issue #6 / `PresetCustomWrite`, document the 51-slot list in `agent_workflow.md` so the LLM knows what to map.
- blender-mcp `get_viewport_screenshot` writes the PNG to a filepath we supply on disk (NOT a base64 blob in the response) and reports failures as `status="success"` + `result["error"]` rather than top-level `status="error"` (the addon catches its own exceptions before returning) → Wrapper picks a `tempfile.NamedTemporaryFile(delete=False)` path, sends it as `filepath`, reads bytes back, deletes the temp, and inspects `result.get("error")` itself to raise `BlenderError`. Don't trust top-level status alone for this handler.
- Ollama Cloud's `POST /api/chat` is NOT OpenAI-compatible (response is `body.message.content`, not `choices[0].message.content`) so `OpenAIProvider` 200s the request then explodes on parse. Wire format check: if a provider returns `done` / `done_reason` / `eval_count` you're talking Ollama, write a dedicated `OllamaProvider` against `httpx`. Tool-call ids are NOT returned — synthesize client-side uuids so the agent loop can pair `tool_use ↔ tool_result` across turns.
- Reasoning-mode Ollama models (e.g. `deepseek-v4-flash`) burn token budget on internal CoT before emitting visible content. With `num_predict=32` the response was `content=""` + `stop_reason=max_tokens`; the default 4096 budget works fine. If you want zero-reasoning behavior, pass `think: False` in the request body — currently not exposed in `OllamaProvider`.
- `Path.home()` is a classmethod on pathlib.Path, NOT an instance method — patch it with `monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))`, NOT `lambda self: ...` (issue #9 tests). Wrong patch silently leaks to the real `~` and tests poison the developer's `~/.modpilot/config.json`.
- htmx `sse-swap="widget_classification widget_material"` on a single slot DOES work in 1.x (space-separated event names supported per htmx-ext-sse). Verified with issue #7's `#widget-slot`. No fallback to two separate slots needed.
- Pydantic v2 dynamic field names: `BaseModel` with `model_config = ConfigDict(extra="allow")` exposes them via `model.model_extra` (NOT in `model_dump()` by default). Use this to accept form-submissions with unknown-ahead-of-time keys like `type__<chain_name>` or `texmap__<slot_idx>__<mat>` (issue #7's widget routes). Iterate `model_extra.items()` to harvest.
- CSS `display:none` on an ancestor wins over `display:flex` on a descendant, even if the descendant's selector specificity is higher — the descendant's box is never generated at all. Symptom (issue #3): `#config-saved-badge` was nested inside `#config-form-section`, and `body.config-locked #config-form-section { display: none }` made the whole subtree invisible, including the badge that was supposed to appear in the section's place → Either keep them as siblings, or (cleaner here) hide only the form's inner children when locked and leave the section itself in the DOM so the badge stays renderable.
- htmx-ext-json-enc serializes `ev.detail.parameters` verbatim, and FormData yields flat keys like `config.model_path` rather than a nested `{config: {model_path: ...}}` object. Posting flat-dotted keys to a Pydantic body with `body: SessionConfigRequest` fails validation → Override `ev.detail.parameters` in an `htmx:configRequest` handler scoped to the form: re-read the form, build the nested dict in JS, replace `parameters` before json-enc's encodeParameters runs. configRequest fires before encodeParameters, so the replacement is picked up cleanly.
- htmx 1.x default body encoding is `application/x-www-form-urlencoded`. Our FastAPI endpoint `POST /agent/messages` declares `body: ChatRequest` (Pydantic model = JSON only) and 422s on form data. Both the chat-form and the new error-choice buttons silently 422'd in real browsers (only TestClient JSON posts were verified) → Vendor `htmx-ext-json-enc.js` and put `hx-ext="json-enc"` on EVERY posting element. Inheritance via `hx-ext` on `<body>` is unreliable for this (see next entry); set it per-element.
- Inline `onclick="this.closest(...).remove()"` on an htmx hx-post button runs SYNCHRONOUSLY before htmx's click handler. By the time htmx fires `htmx:configRequest`, the button has already been detached and htmx bails out → app.js's optimistic-bubble handler never runs, request still goes through. Move removal to `htmx:beforeRequest` in app.js (fires AFTER configRequest), keep the button in DOM until htmx has built the request.
- htmx 1.x `hx-ext="a,b"` on `<body>` IS recognized for events like `htmx:configRequest` (so `json-enc`'s header hook fires), but its `encodeParameters` is NOT reliably called for dynamically-inserted (`htmx.process`'d) descendants — symptom is Content-Type=application/json but body stays form-urlencoded → Set `hx-ext` directly on the posting element (the button or form), not on a distant ancestor. The single-extension case on the element works; the multi-extension inheritance path doesn't.
- `htmx-ext-sse` `sse-swap` resolves its source via `getClosestMatch(child, hasEventSource)` — it walks UP the DOM for an ancestor with `sse-connect`. Putting `sse-connect` on a SIBLING of the swap target silently breaks the swap (no error; the event arrives at `document.body` listeners but no `sse-swap` element triggers) → Place `sse-connect` on a common ancestor of every swap target. For ModPilot's chat shell that means `<body>`, not `<ol id="phases">`.
- Windows PowerShell stdout is cp1252; `print("中文")` from `uv run python -c "..."` raises `UnicodeEncodeError: 'charmap' codec can't encode characters` → Prefix the command with `$env:PYTHONIOENCODING="utf-8"` AND use `sys.stdout.buffer.write(s.encode("utf-8"))` instead of `print()`. Pure `print()` still fails because `sys.stdout` was already opened with cp1252 before the env var takes effect.
- TestClient + `sse-starlette` long-lived stream don't compose: `client.stream(...).iter_text()` and even just entering/exiting `with client.stream(...)` block can hang forever waiting for body drain → Verify SSE wire format via manual `curl -N --max-time N` against a running uvicorn, not via TestClient. In unit tests, inspect `app.state.agent_streams[sid]` queue contents directly.
- Starlette 1.0 changed `Jinja2Templates.TemplateResponse` signature: `request` is now first positional arg, not in the context dict. Old form raises `TypeError: unhashable type: 'dict'` → Use `templates.TemplateResponse(request, "name.html", {"key": value})`, NOT the legacy `("name.html", {"request": request, ...})`.
- Event sink ordering bug: `call_soon_threadsafe`-scheduled emits land in the queue AFTER any synchronous `put_nowait` even when both are issued from the loop thread → "Smart sink": if `asyncio.get_running_loop() is event_loop`, push directly; otherwise threadsafe-marshal. Otherwise the final `done` event arrives before earlier `message(assistant)`.
- `LLMClient.from_settings()` runs in FastAPI lifespan. Without `LLM_API_KEY` in env / .env, the openai client constructor raises `OpenAIError: Missing credentials` at server boot → For smoke tests that don't need real LLM calls: `LLM_API_KEY=dummy LLM_BASE_URL=http://127.0.0.1:9999 uv run uvicorn ...`.
- `_get_or_create_session` calls `_get_client()` which raises 503 when Blender unreachable, BEFORE reaching `loop.step()` → SSE event payloads can't be smoke-tested via curl without a live Blender connection. Use unit tests with `BlenderClient.connect` / `connected` monkey-patched for event-sequence assertions.
- Issue tracker descriptions can be aspirational, not factual ("SSE endpoint already exists at POST /agent/chat" — it didn't) → Verify load-bearing technical claims against actual code before designing around them.
- `cli.py` and the web UI share `app.state.agent_sessions` keyed by session_id. CLI sessions never install an event sink; web UI sessions do. Sharing a session_id across both means the second client picks up existing `AgentLoop` state but only sees events for turns triggered through `/agent/messages` → Don't reuse session_ids across the two surfaces unless that sharing is desired.
