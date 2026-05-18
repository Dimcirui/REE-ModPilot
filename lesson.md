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
│   │   └── routes/               # currently empty; routes live in main.py
│   ├── frontend/                 # React 19 + TS + Vite + motion SPA (replaces former htmx setup)
│   │   ├── src/{pages,components,hooks,lib,types}/   # ChatPage + ConfigPage; PathField w/ drag-drop
│   │   ├── src-tauri/            # Tauri v2 Rust shell (dialog plugin; center:true fix; modpilot.exe)
│   │   ├── vite.config.ts        # proxies /agent /app /viewport_screenshot /health → :8000
│   │   └── package.json          # pnpm; dev / build / tauri:dev / tauri:build scripts
│   ├── artifacts/                # generated; gitignored. ui_walkthroughs/<stamp>/walkthrough.webm
│   └── tests/{unit,integration,e2e}/   # e2e = Playwright browser smokes (opt-in: error_choice_ui.py, session_config_form.py)
├── cli.py                        # interactive CLI client (talks to POST /agent/chat)
├── verify_blender_mcp.py         # Stage 0 connectivity smoke (5 checks)
├── docs/                         # design.md, backlog.md, plan.md, agent_workflow.md,
│                                 # plugin_api.md, blender-mcp-analysis.md
├── README.md, CLAUDE.md, AGENTS.md
└── lesson.md                     # this file
```

## Shipped-work history

Dated "Last updated" entries with full design rationale, file maps, and test deltas live in **[docs/backlog.md](docs/backlog.md)**. This file used to mirror them in "Current state" blocks — those were dropped to stop the duplication. Below: stable run commands, reference pointers, hard rules, and the lesson log.

## How to run

```bash
# One-time
cd ModPilot
uv sync                                              # install from uv.lock

# Backend dev server (Blender should be running for full agent paths; addon enabled, "Connect to Claude" clicked)
uv run uvicorn app.main:app --reload                 # http://127.0.0.1:8000/

# Frontend dev server — pick one:
#   (a) Browser mode (fast HMR; text-only path inputs — no drag-and-drop in browser sandbox)
cd frontend && pnpm install && pnpm dev              # http://localhost:5173/ (proxies /agent /app /viewport_screenshot /health → :8000)
#   (b) Tauri desktop mode (native drag-drop file/dir paths in PathField)
cd frontend && pnpm tauri:dev                        # spawns modpilot.exe; auto-rebuilds Rust on src-tauri/ change (~7s)

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
- Context-management compaction needs TWO flags on AgentLoop, not one. `_phase_just_advanced` (Issue #15) drives the IN-TURN wrap-up llm.chat — set when `_phase_idx += 1`, consumed at the end of `_run_react_turn`, reset same turn. `_just_completed_phase` (this work) drives the NEXT-TURN compaction — set in the same place, consumed at the TOP of the next `_run_react_turn`, reset there. Don't collapse them into one flag: the wrap-up message has to land in history BEFORE compaction reads it as the summary text, so the two events fire on opposite sides of an `await asyncio.to_thread(self._llm.chat, ...)` boundary that spans two user turns. Also: the wrap-up `assistant` move logged in `step()` carries the NEW phase in its `phase` field (because `_phase_idx` already incremented before `step()` appended), so when hydrating from `moves.jsonl` you cannot filter by phase to find the wrap-up — use append order + look for the first `assistant` move AFTER the `phase_advance` entry.
- `query_history` meta-tool cannot rely on system-prompt advice alone for `last_n` bounds. The prompt asks the LLM to keep it small, but a misbehaving (or just curious) LLM can call `query_history()` with no args and force the entire move log back into context — defeating the whole point of compaction. Server-side backstop in `_execute_tool_call`'s handler: default to `QUERY_HISTORY_DEFAULT_LAST_N=50` when omitted/invalid (non-int, ≤ 0), clamp to `QUERY_HISTORY_MAX_LAST_N=1000` otherwise. Keep the cap in the handler, NOT in `MoveLog.read()`, so internal callers like `_hydrate_from_move_log()` stay uncapped (hydration legitimately needs every `phase_advance` to count the phase index correctly).
- Session-recovery hydration must be phase-granular, not turn-granular. Faithfully replaying `tool_use` / `tool_result` blocks into `_global_history` requires preserving the exact `tool_use_id` strings the LLM generated — Anthropic API 400s on any mismatch, and the move log doesn't store ids. The robust path: count `phase_advance` entries → `_phase_idx`, inject one `[compacted] <wrap-up>` summary per completed phase, NEVER replay mid-phase tool detail. Mid-phase work is recovered via (a) scene-is-memory (the agent re-queries Blender on next turn) and (b) `query_history` (past decisions stay on disk). The cost is that a mid-phase crash loses the partial work's chat context — but ModPilot's design already treats Blender's saved state as the source of truth, so this is in-policy.
- Frontend `session.ts` mints a fresh 12-char hex on every page reload by default — backend session recovery is useless without persistence. Flip to `localStorage` (key `modpilot.session_id.v1`), validate the stored value against `/^[a-f0-9]{12}$/i` before trusting it (defends against manual tampering / future format migrations), and fall back to module-scope cache when `localStorage.getItem`/`setItem` throws (Safari private mode, locked-down WebViews). Without the localStorage flip, the entire `moves.jsonl` recovery pipeline is dead code in production: same browser, same backend, fresh session_id every reload → backend always creates a new AgentLoop → prior session orphaned on disk.
- LLM provider/model mismatch is a silent 404, not a startup error: `~/.modpilot/config.json` is allowed to persist any combination of `llm_provider` + `llm_model`, the `LLMClient.from_settings()` factory constructs the provider object successfully (no model-existence check), and the failure only surfaces when an actual chat round-trip hits the provider's `/api/chat` endpoint with an unknown model id. Symptom in this session: `POST /agent/messages → 500` with `httpx.HTTPStatusError: 404 Not Found for 'https://ollama.com/api/chat'` because the persisted config carried `provider=ollama` + `model=deepseek-chat` (the `deepseek-chat` model lives on api.deepseek.com via OpenAI-compatible, NOT on Ollama Cloud). Root cause was the `/config` UI dropdown: when the user switched Provider from openai_compatible to ollama, the Model field stayed at `deepseek-chat` — nothing reset it. Fix shipped in commit `481cd9d` covers both sides: (a) server guardrail `_validate_provider_model_combo()` in `POST /app/config` returns 422 + `field_errors.llm_model` for obvious mismatches (ollama + `deepseek-chat`/`claude-*`/`gpt-*`; anthropic + non-claude); openai_compatible stays open-universe since you can't enumerate every Qwen/GLM/Mistral variant. (b) UI `PROVIDER_DEFAULTS` map in `ConfigPage.tsx` — switching the Provider dropdown swaps Model + Base URL to the new provider's known-good defaults, but ONLY if the current values match a recognized default (so a manually-typed `qwen-max` isn't clobbered when switching openai_compatible → openai_compatible). ConfigPage also unwraps `ApiError.body.detail.field_errors[0]` for the user-facing message instead of "POST failed: 422". Diagnostic order when you see `/agent/messages` 500 in the future: (1) `GET /app/config` to inspect persisted `(provider, model, base_url)`; (2) `Get-Content $env:LOCALAPPDATA\com.modpilot.app\logs\backend.log -Tail 30` for the httpx URL + status — provider mismatch shows up as a 404 against the wrong host before any LLM-internal error.
- For Tauri 2 release builds, always use `pnpm tauri build` (or `--no-bundle` variant for just the exe), NEVER raw `cargo build --release` → cargo alone compiles with `--cfg dev` set (visible in `cargo build --release -v` rustc invocation line), which makes Tauri's asset handler read from `devUrl` (Vite at :5173) instead of using the embedded bundle. modpilot.exe then spawns, opens its window with default title "ModPilot", and the WebView2 webview navigates to `http://tauri.localhost/index.html`, gets a 404 from an empty asset handler, falls back to `chrome-error://chromewebdata` (DevTools console shows `Not allowed to load local resource: chrome-error://chromewebdata/#buttons`). Backend sidecar starts fine, Rust shell logs all green, the only thing missing is the JS bundle itself. Diagnostic: grep the built exe for the current JS bundle filename (e.g. `Select-String index-XXXXXXXX` in PowerShell); zero matches = bundle not embedded; ~8.5 MB exe size also indicates dev mode (production with bundle is ~9.2+ MB). Today we lost ~2 hours chasing port conflicts, system proxy interference, WebView2 user-data corruption, and Tauri 2 capability/CSP theories before noticing `--cfg dev` in the verbose build output. Build invariant for ModPilot: never run `cargo build` against `src-tauri/` directly except for syntax checks; always go through `pnpm tauri build`.
- Tauri v2 on Windows spawns its main window at (-21333, -21333) size 158×26 (offscreen, message-only sentinel) if you don't explicitly request centering → set `"center": true` on the window entry in `tauri.conf.json`. The `visible: false` + JS `getCurrentWindow().show()` trick is unnecessary; `center` alone fixes it. Verified via Win32 EnumWindows that without center you get TWO windows (Tao event-target + the real "Tauri Window"); with center, the real window spawns at correct size.
- Browser sandboxes intentionally hide filesystem paths on drag-and-drop — the dropped `File` object has `.name` but **no `.path`**. So drag-drop-to-fill-path is impossible in the dev server (`:5173`) and you need a Tauri shell (or a system file picker via `tauri-plugin-dialog`) to surface real disk paths. PathField transparently degrades to a plain text input when `__TAURI_INTERNALS__` is not on `window`.
- Issue #15 phase-transition pause: when adding a NEW wrap-up branch to `_run_react_turn`, the issue #14 interrupt check MUST run first — otherwise the interrupted user pays for an extra wrap-up `llm.chat` round-trip before the bail-out fires. Order: drain → error_reply guard → `_interrupted` guard → `state != RUNNING_PHASE or _phase_just_advanced` wrap-up. Also: tool *names* differ from class names — `SetupValidateScene` registers as `"setup_validate_scene"` (not `"setup_validate"`), `InferModelType` as `"setup_infer_model_type"` (not `"setup_infer"`). When mocking tool_calls in tests, grep `def name(self)` on the phase module — guessing from the `_PHASE_SEQUENCE` slot name (which IS truncated, e.g. `setup_validate`) makes the test silently hit the "Tool not available" path and the phase never advances.
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
- Pydantic v2 dynamic field names: `BaseModel` with `model_config = ConfigDict(extra="allow")` exposes them via `model.model_extra` (NOT in `model_dump()` by default). Use this to accept form-submissions with unknown-ahead-of-time keys like `type__<chain_name>` or `texmap__<slot_idx>__<mat>` (issue #7's widget routes). Iterate `model_extra.items()` to harvest.
- CSS `display:none` on an ancestor wins over `display:flex` on a descendant, even if the descendant's selector specificity is higher — the descendant's box is never generated at all. Symptom (issue #3): `#config-saved-badge` was nested inside `#config-form-section`, and `body.config-locked #config-form-section { display: none }` made the whole subtree invisible, including the badge that was supposed to appear in the section's place → Either keep them as siblings, or (cleaner here) hide only the form's inner children when locked and leave the section itself in the DOM so the badge stays renderable.
- TestClient + `sse-starlette` long-lived stream don't compose: `client.stream(...).iter_text()` and even just entering/exiting `with client.stream(...)` block can hang forever waiting for body drain → Verify SSE wire format via manual `curl -N --max-time N` against a running uvicorn, not via TestClient. In unit tests, inspect `app.state.agent_streams[sid]` queue contents directly.
- Event sink ordering bug: `call_soon_threadsafe`-scheduled emits land in the queue AFTER any synchronous `put_nowait` even when both are issued from the loop thread → "Smart sink": if `asyncio.get_running_loop() is event_loop`, push directly; otherwise threadsafe-marshal. Otherwise the final `done` event arrives before earlier `message(assistant)`.
- `LLMClient.from_settings()` runs in FastAPI lifespan. Without `LLM_API_KEY` in env / .env, the openai client constructor raises `OpenAIError: Missing credentials` at server boot → For smoke tests that don't need real LLM calls: `LLM_API_KEY=dummy LLM_BASE_URL=http://127.0.0.1:9999 uv run uvicorn ...`.
- `_get_or_create_session` calls `_get_client()` which raises 503 when Blender unreachable, BEFORE reaching `loop.step()` → SSE event payloads can't be smoke-tested via curl without a live Blender connection. Use unit tests with `BlenderClient.connect` / `connected` monkey-patched for event-sequence assertions.
- `cli.py` and the web UI share `app.state.agent_sessions` keyed by session_id. CLI sessions never install an event sink; web UI sessions do. Sharing a session_id across both means the second client picks up existing `AgentLoop` state but only sees events for turns triggered through `/agent/messages` → Don't reuse session_ids across the two surfaces unless that sharing is desired.
