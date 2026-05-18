# Backlog

Priority-ranked task list. Status badges follow the [project convention](../AGENTS.md#status-badge-convention):

| Symbol | Meaning |
|--------|---------|
| 🟢 | Done |
| 🟡 | In progress |
| ⚪ | Not started |
| 🔴 | Blocked |

Tasks are grouped by priority band (P0 → P3). Within a band, ordering is suggested execution sequence; tasks are independently deliverable unless an explicit dependency is noted.

**Last updated**: 2026-05-18 (later) — **Stage-driven UI rebuild on top of the C25 scaffold.** Replaced the monolithic `ChatPage` canvas (form + stepper + viewport + widgets all rendered at once) with a per-phase stage architecture: `src/stages/StageRouter.tsx` reads `state.phaseStatus` (or `loopState === 'done'`), picks a component out of `STAGE_REGISTRY`, and cross-fades via `motion`'s `AnimatePresence`. Keyed by component identity (not phase name) so sibling phases that share a stage advance without remount. Shipped stages: `Phase1Stage` (pose correction — ratio readout), `Phase23Stage` (shared for phase_2+3, skeleton align + vertex groups), `Phase4Stage` (shared for phase_35+4a+4b, physics; hosts `ClassificationWidget` as viewport overlay), `Phase5Stage` (materials; swaps viewport ↔ `MaterialWidget` on canvas), `Phase6Stage` (batch_export tile with armor id chip, 5 part chips, file/size stats, natives path), `DoneStage` (animated check, output path, next-steps). Unmigrated phases (setup_*) fall through to `FallbackStage` which keeps the legacy multi-purpose surface. Bottom `ChatStrip` replaces the body-occupying chat log — collapsed shows status + 140-char preview + unread badge, expanded reveals `ChatLog` (30vh) + `MessageInput`. Structured tool-run tracking added to `useChatState` (`ToolRun[]` with phase/name/runId/toolId/success/summary), so stages filter activity feeds without parsing stringified bubbles; pairing logic prefers tool_id match, falls back to last-unfinished-by-name (handles Ollama/DSML where ids are absent). Local LLM verified end-to-end against Ollama Cloud + DeepSeek V4 Flash. **Recording artifacts** (gitignored): 4 walkthrough webms in `ModPilot/artifacts/ui_walkthroughs/`, one per new stage, via `scripts/record_phase{23,4,5,6_done}_walkthrough.py` sharing `scripts/_walkthrough_common.py` (EventSource stub + record harness). Drives synthetic SSE in ~22-35 s per stage by monkey-patching `window.EventSource` before any page script runs, so the recorder runs at sub-second per state transition without needing a real LLM round-trip. Real backend stays up so `/viewport_screenshot` and `/app/x_presets` keep returning real data. No backend delta; unit suite still 453 passing. No new e2e checks committed.

**Last updated**: 2026-05-18 — **Frontend rebuild + Tauri v2 desktop shell** (no GitHub issue; user-driven UX work). Replaced the Stage-5 htmx+Jinja2 SPA with **React 19 + TypeScript + Vite + motion** under `ModPilot/frontend/`. Same SSE event surface, same `/agent/*` and `/app/*` REST contracts; backend untouched. Component map: `ChatPage` (full app) + `ConfigPage` (LLM settings) under `src/pages/`; `SessionConfigForm`, `PathField` (new — drag-drop path input), `ChatLog`, `PhaseStepper`, `ViewportPane`, widget shells under `src/components/`; `useChatState` + `useSSE` hooks; structured-JSON SSE types in `src/types/sse.ts`. `templates/` and `static/` trees deleted from `ModPilot/app/`. Vite config proxies `/agent /app /viewport_screenshot /health` → backend `:8000`. Tauri v2 Rust shell layered on top (`frontend/src-tauri/`) so disk paths can be drag-dropped natively — browser sandboxes refuse to expose `File.path`, so the `PathField` UX requires either the Tauri webview or the `tauri-plugin-dialog` native picker. Feature-flag bridge in `src/lib/desktop.ts` keeps browser builds Tauri-free via dynamic imports. Tauri window spawn fix: `"center": true` in `tauri.conf.json` is required — without it Tauri v2 on Windows spawns at (-21333, -21333) size 158×26 offscreen. **Form-level changes**: (a) `"Mod Base"` legend → `"Mod Output"` (clarifying it's where the mod gets written); (b) Body parts widget: multi-select checkboxes → single-pick radio, default `Body (2)`; `body_parts` stays a `string[]` of length 1 for backend compat. **Local LLM env**: Ollama Cloud configured (`LLM_PROVIDER=ollama`, `LLM_MODEL=deepseek-v4-flash`, key in `ModPilot/.env`, gitignored). `/app/config` reports `has_api_key: true`. **Recording artifact**: `ModPilot/artifacts/ui_walkthroughs/20260518_173958/walkthrough.webm` (~1.2 MB, 1280×820) — Playwright sweep of `/config` + `/`; static UI surfaces only (chat-input selector missed Send fired into empty input). **Live Blender-connected sweep deferred to another machine** with Blender 4.3.2 + `blender-mcp` available. Helper script: `ModPilot/scripts/record_ui_walkthrough.py`. Design decision: **C25 supersedes C12** (htmx); see `docs/design.md`. No unit-test delta — backend unchanged; full unit suite still 453 passing. No new e2e checks committed (Playwright sweep script is one-off).

**Last updated**: 2026-05-17 (latest) — Issue **#15** Phase Transition Protocol. Two-layer fix for the "LLM chains phase tools in one turn" gap. (a) `agent_workflow.md` gains a `## Phase Transition Protocol` H2 between Pipeline State Assessment Protocol and Phase Sequence, mandating report-then-wait after every phase-advancing tool, whitelisting query tools for mid-pause Q&A, and explicitly forbidding phase tools until the user says continue. `build_system_prompt` extracts and injects the section. (b) Backend rail on `AgentLoop`: new `_phase_just_advanced: bool` flag set in `_execute_tool_call`'s phase-advance branch right after `_phase_idx += 1`; the existing wrap-up branch in `_run_react_turn` now reads `state != RUNNING_PHASE or _phase_just_advanced`, runs a single `tools=None` `llm.chat` for the completion report, returns the text to the user, and resets the flag. Same rule mirrored in the DSML branch. Flag also reset at the top of every `step()` as a safety belt. Issue #14 interrupt check inserted before the issue #15 wrap-up so an interrupted user never waits through an extra LLM call. 5 new unit tests + 1 system-prompt assertion. Full unit suite **453 passing** (was 448, +5). P2 backlog entry struck through.

**Last updated**: 2026-05-17 (just before that) — Issue **#14** UX interrupt mechanism. New `AgentLoop.interrupt()` flips a private `_interrupted` flag and emits one `interrupted` SSE event; `_run_react_turn` polls the flag at the top of every round AND inside the per-tool-call inner loop, then routes through new `_handle_interrupt_bailout` which resets the flag, transitions to `IDLE`, emits `state`, and returns "Interrupted by user." The inner-loop break uses the existing try/finally placeholder injector so any partially-executed round still drains tool_results for every tool_use id — no orphan blocks left in history. New `POST /agent/interrupt/{session_id}` route surfaces this to the frontend (returns 200 + `{interrupted: true}` on hit, 404 on unknown session). Frontend: native Escape key listener on `document` posts to the route (ignored when an `INPUT`/`TEXTAREA`/`SELECT` is focused so Esc-to-blur still works, and skipped when status isn't `thinking` so dead Escapes are no-ops); new `interrupted` dispatcher renders a dismissable yellow banner (`#interrupt-banner`, auto-hides after 6s, click `✕` to dismiss earlier). 7 new unit tests (flag init / set+emit / idempotent / pre-step short-circuit / mid-round drain + bail / route 200 / route 404) + playwright smoke (banner default-hidden, dismiss click toggles `.hidden`). Full unit suite 448 passing (was 441, +7). Backlog P3 entry struck through.

**Last updated**: 2026-05-17 (later than that) — Issue **#16** Phase 5A architecture refactor: docs-only change to `docs/agent_workflow.md`. Replaced the linear `consolidate → classify → wire` pipeline with a 6-step small-loop architecture: `Consolidate → Inspect → Already-Connected Branch → Classify+Confirm (loop entry) → Wire → Verify`. Two new guardrails: (1) **Already-Connected Branch** asks the user before overwriting fully-wired materials (Base Color + Normal both resolved to a file) — protects manual setup from silent loss; (2) **Verify** re-runs `material_inspect` after `material_setup` and asks "satisfied?", with a scope-narrowing loop-back path that re-classifies only user-named materials via a `materials_filter` instead of restarting from scratch. Layer 4 classification source updated to point at the issue #7 + #11 confirmation widget (instead of the now-deprecated `propose_and_confirm` JSON sketch). Phase tools themselves untouched (`material_inspect` / `material_setup` already support per-material scoping) — the LLM reads the new workflow spec from the system prompt. `_extract_section` smoke-tested: Phase 5 section contains all new markers; stops cleanly before Phase 6. Full unit suite still 441 passing (no test changes needed; pure spec doc).

**Last updated**: 2026-05-17 (even later) — Issue **#13** Phase 1 scale-align method swap. Replaced mesh-bbox-Z height ratio with arm-bone average-Z method: sample world-space head Z for `upperarm_L/R`, `forearm_L/R`, `hand_L/R` (6 slots), compute `ratio = mean(target_z) / mean(source_z)`, apply via `object.transform_apply(scale=True)`. Source bone names per slot come from the active X-preset's `mappings[slot]["main"]` candidate list (first match wins); target bones use the slot keys directly (MHWilds canonical naming). New module-level cache `_ARM_CANDIDATE_CACHE` populated from `discover_preset_dir + enumerate_x_presets`; failing discovery falls back to `{slot: [slot]}` so canonical-naming source rigs (e.g. 怪猎荒野) still resolve. Requires ≥ 2 resolved bones per side or PRECONDITIONs (`source_arm_bones_unresolved` / `target_arm_bones_unresolved` / `*_arm_height_zero`). New `_resolve_arm_candidates` helper is the test-injectable seam; new autouse fixture `TestPoseCorrection._stub_arm_candidates` short-circuits the cache lookup to keep call-count assertions stable. 2 new unit tests (preset-candidate injection + catalog-empty fallback) + 1 updated PRECONDITION case + 1 strengthened regression guard. agent_workflow.md Phase 1 step 3 wording updated; tool-schema `skip_scale_align` description refreshed; `pose_correction.py` module docstring rewritten. Full unit suite 441 passing (was 439, +2 from this issue). Live Blender NOT needed for implementation; visual L3 validation on a real avatar still optional (same posture as issue #8).

**Last updated**: 2026-05-17 (later still) — Issue **#11** Phase 5 material confirmation widget LLM pre-fill. New async `AgentLoop._suggest_texture_mapping(materials, texture_files, existing_connections)` calls the LLM once after `material_inspect` and returns `{material: {slot: file_path}}`; response is JSON-validated and filtered against the actual material / slot / texture sets so the model can't smuggle in invented file paths. Suggestions ride on the `widget_material` SSE event payload as a new `suggestions` key; `_render_material_widget_html` accepts a `suggestions` arg and the Jinja template applies a 3-tier precedence (LLM suggestion > existing wired connection ≠ `connected_no_image` > none). Suggested rows render with a `row-suggested` left-bar highlight + an `LLM` chip next to the slot label. Integrates with the deferred `_pending_widget` flow from the E2E PR (chat commentary lands before widget). Best-effort: any LLM failure or non-JSON reply returns `{}` so the widget falls back to bare rows. 3 new unit tests; existing `test_material_inspect_success_emits_widget_material` updated to thread the suggest LLM round-trip and assert `suggestions` on the pending widget. Full unit suite 439 passing (was 436 after E2E PR, +3 from this issue).

**Last updated**: 2026-05-17 (after E2E debug session 3) — Issue **#10** global config extension: hunter type (`armor_variant`) + equipment selection (`armor_id`) hoisted from Phase 6 mid-run prompts into the session-config form. New shipped catalog at `ModPilot/app/data/armor_sets.json` (122 armor sets mirrored from the workflow doc table) + `app/armor_catalog.py` loader; new `GET /app/armor_sets` route; `SessionConfig` extended with `armor_variant: Literal["ff","fm","mf","mm"] = "ff"` and `armor_id: str` (validated against the catalog at POST /agent/config). System prompt's pre-collected block now includes both values so Phase 6 reads them directly instead of asking the user. `docs/agent_workflow.md` Phase 6 lost Step 1 (Hunter Type Selection) and Step 2 (Equipment Selection — the 122-row armor table), now folded into a single "from session config" reference; remaining steps renumbered (3→2 collection assignment, 4→3 batch export, 5→4 BoneSystem, 6→5 log analysis). Form gains an `armor_variant` radio group (ff default) + an `armor_id` dropdown populated from `/app/armor_sets`. 3 new unit tests (catalog endpoint, unknown armor_id → 422, invalid armor_variant → 422). Full unit suite 436 passing (was 433 after E2E PR, +3 from this issue).

**Last updated**: 2026-05-17 (E2E debug session 3) — Resolved: BlenderClient socket race (RLock + BlenderBusyError), Anthropic 400 bidirectional history repair (`_heal_history`), widget Confirm stuck-thinking (`_run_step_with_done_emit` try/finally), deferred widget emit ordering (`_pending_widget` flushed from `step()` finally), physics `_End` bone cascade (`_expand_end_children` no chain_role filter), `skeleton_align` hidden-armature auto-unhide. New ⚪ P2 items: material widget pre-fill, static asset cache-busting, consecutive tool_use 400 prevention, done-watchdog mis-timing. P1 items #3 and #7 updated to 🟢; stale duplicate ⚪ entries for #4/#5/#6 removed.

**Last updated**: 2026-05-17 — Stage MVP verification (issue #8) landed: `verify_mvp.py` at repo root + `verify_mvp_config.example.json` template + `docs/demo_setup.md` user-facing setup walkthrough. The script bypasses the agent loop entirely — it imports phase tools directly and drives them with config-supplied classification mappings (`x_preset`, `inferred_types`, `texture_mapping`, `preset_mapping`) so the run is fully deterministic and exit-code-correlated. CLI: `uv run python ../verify_mvp.py --config ../verify_mvp_config.json [--phases setup phase_1_2_3 ...] [--report out.json]`. Each phase tool's internal `require_finished()` call already enforces operator FINISHED status; the script adds (a) phase-level success aggregation, (b) post-Phase-6 file-existence + non-zero-size check against a user-supplied `expected_files` list under `natives_root`, (c) per-step duration + state_diff capture written to the optional JSON report. `docs/demo_setup.md` covers Blender+addon prerequisites, MMD model recommendations (no assets bundled per design D15), MHWs `.fbxskel.7` acquisition via REasy/RE-Toolbox, mod folder layout, full config field reference, and an L3 in-game acceptance procedure with a visual-symptom→failure-phase table. All P0 MVP items now 🟢.

**Last updated**: 2026-05-16 (later) — Issues **#4 / #5 / #6** auto-inference + preset supplement + custom-preset paths landed across 5 internal waves. The session-config form's hardcoded `model_type` dropdown is gone; values now come from `GET /app/x_presets` (driven by `app.state.x_preset_catalog` populated by the lifespan handler from the toolkit's `assets/presets/import/` folder, with the 13 shipped names as the fallback when Blender isn't reachable at boot). New phase tools: `InferModelType` (returns coverage report + 4-band decision: exact / supplement / custom / unsupported) inserted as `setup_infer` between `setup_validate` and `setup_import`; `PresetSupplementWrite` writes `<base>_extended.json` next to the shipped preset (additive merge, never overwrites the shipped file); `PresetCustomWrite` writes `<character_name>_custom.json` from a full LLM-confirmed mapping. The LLM is the per-slot classifier (per design A1 "LLM at classification points"); phase tools stay pure-Python deterministic writers. New `model_type_inferred` SSE event back-fills the form dropdown with the inferred preset + coverage badge. Error-choice widget gains a conditional `[强制自定义]` button when the error category is `unsupported_rig`; clicking it sends `[FORCE_CUSTOM]` so the LLM re-runs `InferModelType(force_custom=true)`. `X_PRESETS` in `app/phases/base.py` is now a mutable runtime set seeded by the catalog at startup; `add_x_preset()` registers newly-written presets so downstream phase validators see them without a restart. 50 new unit tests (17 catalog + 17 inference + 16 write). Live-verified against the toolkit's 13 shipped X-presets — synthetic MMD-bone probe scored MMD 37.25% / VRChat 36.54% / 赛马娘 5.88%, routing to `decision="custom"` as designed.

**Last updated**: 2026-05-16 — Stage 5 frontend wave 5: viewport screenshot side-panel (the last remaining Stage 5 P0). `BlenderClient.get_viewport_screenshot` added (tempfile-based, translates the addon's in-band `result.error` shape into `BlenderError`); `GET /viewport_screenshot` returns `image/png` + `Cache-Control: no-store`, `max_size` clamped 64–2048 via FastAPI `Query`, 503 on Blender disconnect. Chat shell restructured into a `#main-area` 2-column grid (left = log/error-choice/widget, right = sidebar with img + auto-refresh + manual ↻ button + status); collapses to a top strip below 900 px. `app.js` runs a `setInterval` 5 s pull via `fetch` + Blob URL — pauses on `document.hidden`, refreshes immediately on auto-toggle / tab return / manual click; 503 surfaces as "Blender unreachable" status badge rather than a broken-image icon. Live-tested against real Blender on `127.0.0.1:9876` — 600 px → ≈110 KB PNG, 300 px → ≈22 KB, `max_size=10` → 422. 8 new unit tests; full unit suite 363 passing.

**Last updated**: 2026-05-15 — Stage 5 frontend wave 4 + Ollama provider (same day): issue #9 global config UI + a new `OllamaProvider` adapter so Ollama Cloud (`https://ollama.com/api/chat`, e.g. `deepseek-v4-flash` / `deepseek-v4-pro`) works through `/config` as a third provider option alongside Anthropic and OpenAI-compatible. Provider translates Anthropic content-block messages to Ollama's flat format and synthesizes client-side tool_call ids; 17 unit tests with mocked httpx; live-verified end-to-end (plain text + tool-calling) against a real Ollama Cloud key. `~/.modpilot/config.json` persisted across sessions, layered on `.env` at startup; `GET/POST /app/config` with API-key masking + preserve-on-empty; `GET /config` form page; first-run redirect from `GET /` when no key configured; `⚙ Settings` link in chat header. Earlier same day — wave 3: issue #7 confirmation widgets. `widget_classification` / `widget_material` SSE events ship server-rendered Jinja partials into a new `#widget-slot`; `POST /agent/widget/classification` and `POST /agent/widget/material` re-package the form data as `[CONFIRMED_CLASSIFICATIONS]` / `[CONFIRMED_MATERIAL_MAPPING]`-prefixed JSON and feed loop.step(), with system prompt explaining the prefix protocol. Frontend `.widget-form` plumbing reuses the optimistic-bubble + button-disable path from chat-form / error-choice; downstream tool_call event clears the slot. New artifacts: `app/templates/widgets/{classification,material}.html`, `tests/unit/test_widget_routes.py` (6 tests), `tests/unit/test_agent_loop_events.py` (+2 widget-emit tests), `tests/unit/test_sse_routes.py` (+2 renderer tests), `tests/e2e/widget_classification_ui.py` (13 Playwright checks). Earlier same day — wave 2: issue #3 session-config form (8 fields, server-side `Path.exists()` validation, localStorage rehydrate, pre-collected params injected into system prompt as a final block of `build_system_prompt`). New artifacts: `POST /agent/config` route + `app.state.session_configs`, `AgentLoop(session_config=...)` kwarg, `tests/unit/test_session_config_form.py` (5 tests), `tests/unit/test_agent_loop_events.py` (+3 prompt-injection tests), `tests/e2e/session_config_form.py` (24 Playwright checks). Earlier same day — wave 1: issue #1 (htmx + SSE chat UI, 8 event types, L2 streaming granularity) and issue #2 (error_choice three-button UI). Latent issue #1 bug also fixed: chat-form was sending `application/x-www-form-urlencoded` against a Pydantic JSON endpoint (422). Earlier — 2026-05-11 E2E testing session 2: mode_set active-object fix (7 call sites); DSML markup strip plain-string fallback; query-tool throttle (max-rounds 8→15 + consecutive-query cap); ERROR_HANDLING↔ASK_MODE deadloop resolved; prepare_only cleanup flow + auto-verify via _End bone detection; bones_to_clear for native game bones; SEPARATE mode revert for auto_create_chains. Full fix log: [docs/e2e_fixes.md](e2e_fixes.md).

---

## P0 — MVP critical path

Items here block MVP shipping. All must reach 🟢 before MVP acceptance (L3, [design.md A4](design.md)).

### Stage Setup — repo structure & docs

- 🟢 Stage 0 connectivity verification (`verify_blender_mcp.py` 5/5 passing)
- 🟢 Design phase A/B/C/D (15 items decided in design.md)
- 🟢 Repo restructure: README.md / CLAUDE.md / AGENTS.md / docs/backlog.md
- 🟢 `.gitignore` (Python / uv / .env / IDE / Blender backups / vendored addons / `.claude/`)
- 🟢 `LICENSE` — MIT (`Copyright (c) 2026 Dimcirui`)
- 🟢 `git init` + initial commit on `main` (commit `7c2dab1`, 11 files, 2887 lines)
- 🟢 Push to GitHub (user-driven: create `REE-ModPilot` repo on github.com → `git remote add origin <url>` → `git push -u origin main`)

### Setup Phase — scene validation + MHWilds import

- 🟢 `app/phases/setup.py` — `SetupValidateScene` + `SetupImportMHWilds`; scene validation (exclude MHWilds collection, check 1 armature + mesh children); import via `mbt.import_mhwilds_fmesh` with mode guard + idempotency check
- 🟢 `app/agent/loop.py` — `setup_validate` + `setup_import` prepended to `_PHASE_SEQUENCE`; both tools registered
- 🟢 `docs/agent_workflow.md` — Setup Phase section added; Central Collection doctrine; Phase 1-3 entry conditions updated

### Stage 1 — communication backbone

- 🟢 `uv init` ModPilot/ project; pyproject.toml with FastAPI / Anthropic SDK / OpenAI SDK / pytest / ruff / pydantic-settings
- 🟢 Configure Ruff + pytest in pyproject.toml (markers: `unit` / `integration`)
- 🟢 Directory skeleton per [design.md D14](design.md#d14)
- 🟢 `.env.example` (LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, BLENDER_HOST, BLENDER_PORT)
- 🟢 `app/blender/client.py` — `BlenderClient` (extracted + hardened from verify_blender_mcp.py)
- 🟢 `app/blender/state.py` — `SceneState` / `SceneCache` with diff (B5)
- 🟢 `app/llm/client.py` — Provider-agnostic `LLMClient` + `LLMResponse` (C10)
- 🟢 `app/llm/anthropic_provider.py` — Anthropic SDK adapter (prompt caching wired)
- 🟢 `app/llm/openai_provider.py` — OpenAI-compatible adapter (DeepSeek V4 default)
- 🟢 `app/main.py` — FastAPI app; `/health` (503 on disconnect), `/scene_info`, `/exec` (debug-only)
- 🟢 `tests/unit/test_blender_client.py` — fake socket server fixture + 13 protocol tests
- 🟢 `tests/unit/test_llm_client.py` — mock provider responses + 17 tool-call shape tests

### Stage 2 — phase tool layer (videos 1-3)

- 🟢 `app/phases/base.py` — `PhaseTool` ABC, `PhaseResult`, `PhaseError` (E16); `require_finished` helper
- 🟢 `app/phases/pose_correction.py` (video 1; 3-step pipeline: pose_reset → mesh_bbox_scale_align → deterministic_pose_convert by x_preset)
- 🟢 `app/phases/skeleton_align.py` (video 2; X+Y preset routing, selection order enforced)
- 🟢 `app/phases/vertex_groups.py` (video 3; 3-step: material_fix+merge+normalise → direct_convert → reparent to MHWilds armature)
- 🟢 Classification in agent loop, not in phase (E17); phases are pure executors
- 🟢 Unit tests: 33 tests covering all phases (param validation, operator dispatch, error paths)

### Stage 3 — agent loop

- 🟢 `app/agent/loop.py` — Hand-rolled ReAct state machine (C9); 7 states; tool-call loop for phases 1-3; isolated phase_history for NEGOTIATING phases 4+
- 🟢 `app/agent/prompts.py` — Builder functions extracting sections from `docs/agent_workflow.md` (C11 amendment); system/per-phase/error prompts
- 🟢 `app/agent/error_handler.py` — `PhaseError` → user message via single LLM call; keyword-match `parse_user_choice()` (B7)
- 🟢 Lazy-explanation behavior wired (A2: error path → ASK_MODE; no tools in ASK_MODE)
- 🟢 `app/phases/base.py` — abstract `tool_schema()` classmethod added to `PhaseTool`
- 🟢 `app/phases/{pose_correction,skeleton_align,vertex_groups}.py` — `tool_schema()` implemented; JSON Schema for LLM tool registration
- 🟢 `app/main.py` — `POST /agent/chat` endpoint; in-memory session store keyed by `session_id`
- 🟢 `app/config.py` — `vision_model` / `vision_api_key` / `vision_base_url` settings added (E20)
- 🟢 41 unit tests covering all state transitions, prompts, and error handler (117 total passing)

### Stage 4 — phase tools (videos 4-7)

- 🟢 `app/phases/physics_bones.py` (Phase 3.5/4A/4B; PhysicsTransplant + PhysicsClassification + PhysicsChains; physics_presets.json distilled from 35 RE Chain Editor presets; 38 unit tests)
- 🟢 `app/phases/material.py` (video 5; MaterialInspect + MaterialSetup + MaterialGenerate; 42 unit tests; design in [docs/phase5_material.md](phase5_material.md))
- 🟢 `app/phases/batch_export.py` (Phase 6; single-call batch export: mesh + mdf2 + chain2 + BoneSystem; 35 unit tests)
- ⚪ `app/phases/advanced.py` (video 7; MHWs-specific tools) — explicitly out of MVP scope

### Stage 5 — frontend (htmx)

- 🟢 Jinja2 chat shell + vendored `htmx.min.js` / `htmx-ext-sse.js` / `htmx-ext-json-enc.js` (issue #1 + #2)
- 🟢 Chat UI with SSE streaming — `GET /`, `POST /agent/messages`, `GET /agent/stream/{sid}`; 8 event types (`message / state / phase_started / phase_completed / tool_call / tool_result / error / done`); legacy `POST /agent/chat` preserved for `cli.py` (issue #1; 11 unit tests added)
- 🟢 Phase progress stepper (10-node strip; classes pending / active / done / error / skipped — driven by SSE `phase_started` / `phase_completed`) (issue #1)
- 🟢 Error response UI: retry / skip / 查看详情 button group, htmx `sse-swap` slot, posts the keyword back to `/agent/messages` (B7; issue #2; 4 unit tests + 12 Playwright e2e checks)
- 🟢 Session config form (8 fields, MMD/VRChat preset → x_preset, mod_root → natives_root, body_parts → target_parts, etc.). `POST /agent/config` with server-side `Path.exists()` validation; localStorage rehydrate on refresh; values appended to system prompt so the LLM doesn't ask mid-run (issue #3; P1 from issue text but bundled with Stage 5 wave; 5 unit tests + 3 prompt-injection tests + 24 Playwright e2e checks)
- 🟢 Interactive confirmation widgets (Phase 4A physics-chain classification table + Phase 5 material slot→texture mapping). Server-rendered Jinja partials shipped over SSE as `widget_classification` / `widget_material` events; `POST /agent/widget/classification` and `POST /agent/widget/material` re-package form data as `[CONFIRMED_*]`-prefixed JSON and feed loop.step(); system prompt explains the prefix so the LLM consumes the dict directly (issue #7; P1; 2 emit tests + 2 renderer tests + 6 route tests + 13 Playwright e2e checks; Phase 4A per-chain free-text + Phase 5 thumbnail previews deferred per issue text "进阶" markers)
- 🟢 Global config UI for LLM provider / api key / model + Blender host/port. `~/.modpilot/config.json` persisted between sessions, overlaid on `.env` at startup; `GET /app/config` masks API key as `"***"`; `POST /app/config` mutates `Settings` in place, writes JSON, rebuilds `app.state.llm` and (on host/port change) `app.state.blender`; empty `llm_api_key` field on POST preserves the existing key; first-run UX redirects `GET /` to `/config` when no key configured; chat-header `⚙ Settings` link. Tolerates missing key at startup (`app.state.llm = None`, `_require_llm()` 503s in `_get_or_create_session`). (issue #9; P1; 10 unit tests covering store/round-trip/masking/preserve/redirect)
- 🟢 `OllamaProvider` for `app/llm/`: `POST https://ollama.com/api/chat` with `Authorization: Bearer <key>` (default endpoint, overrideable to local Ollama daemon). Translates Anthropic-style content blocks (`tool_use` / `tool_result` / text) into Ollama's flat message+`tool_calls` format; synthesizes client-side tool-call ids since Ollama doesn't return them; maps `done_reason` to our normalized `stop_reason` vocabulary. Wired into `LLMClient.from_settings()` and exposed as a third option in the `/config` UI's provider dropdown. Live-verified against `deepseek-v4-flash` for both plain text and tool-calling round-trips. (issue #9 follow-up; 17 unit tests with mocked httpx covering message translation, tool schema, response parsing, and the LLMClient routing branch)
- 🟢 Blender viewport screenshot side-panel — `BlenderClient.get_viewport_screenshot` wraps the in-band-error addon handler (success-with-`result.error` translates to `BlenderError`); `GET /viewport_screenshot` returns `image/png` + `Cache-Control: no-store` and 503s on Blender disconnect (`max_size` clamped 64–2048); chat shell now has a `#main-area` 2-column split (sidebar collapses above the log under ~900px); `app.js` runs a 5 s `setInterval` pull that pauses on `document.hidden`, refreshes immediately on auto-toggle / visibility return, and uses `fetch` + Blob URL so 503 surfaces as an "unreachable" status badge instead of a broken-image icon. (8 unit tests for client + route; live-verified against real Blender on port 9876 — 600px screenshot ≈ 110 KB, 300px ≈ 22 KB, max_size=10 → 422 as designed)

### Stage MVP — verification

- 🟢 `verify_mvp.py` + `verify_mvp_config.example.json` — End-to-end script that imports phase tools directly (bypassing the agent loop / LLM) and drives them with config-supplied classification mappings. Aggregates Operator FINISHED checks (already enforced inside each phase tool by `require_finished()`), post-Phase-6 file existence + non-zero-size checks against `natives_root/expected_files`, per-step duration capture, optional JSON report (`--report`), `--phases` subset selector for iterative debugging.
- 🟢 `docs/demo_setup.md` — Blender + addon install order (Modding-Toolkit / Modder-Batch-Tool / RE Mesh Editor / RE Chain Editor / blender-mcp), MMD source-model recommendations (no assets bundled per D15), `.fbxskel.7` extraction via REasy / RE Toolbox, mod folder layout, full `verify_mvp_config.json` field reference, and an L3 in-game acceptance procedure with a symptom→failure-phase table.
- 🟢 Run a full L3 acceptance pass with self-provided assets — 3-4 MMD/VRC models tested end-to-end (Phase 1→6), exported mods verified in-game. Pipeline stable. MVP declared complete.

---

## P1 — Important but not MVP-blocking

- 🟢 Full frontend — session config form: pre-run parameter collection (source model, mod root, author/character name, export settings) ([#3](https://github.com/Dimcirui/REE-ModPilot/issues/3))
- 🟢 Full frontend — interactive confirmation widgets: Phase 4A bone classification table + Phase 5 material mapping table ([#7](https://github.com/Dimcirui/REE-ModPilot/issues/7))
- ⚪ DeepSeek V4 vs Sonnet 4.6 small A/B eval on key phase classifications (X preset choice, physics route, PBR mapping)
- ⚪ Prompt-cache hit-rate observability (log + simple endpoint)
- ⚪ Single-page user-facing landing copy (avoid listing prereqs explicitly per A2)
- ⚪ Toolkit dependency check (RE Mesh Editor / MHW Model Editor / RE Chain Editor presence detection)
- ⚪ Provider abstraction handles SSE streaming uniformly (Anthropic + OpenAI streaming differ subtly)

---

## P2 — Post-MVP (next phase, not started)

- 🟢 Issue **#4** source-model type auto-inference — `InferModelType` phase tool + `setup_infer` slot in `_PHASE_SEQUENCE` + dynamic `GET /app/x_presets` + `model_type_inferred` SSE dispatcher + relaxed `SessionConfig.model_type` (runtime-validated). Drops the hardcoded `Literal["MMD","VRChat","Other"]` and the `frozenset({"MMD","VRChat","终末地"})` validator; both are runtime-driven from the toolkit's actual `assets/presets/import/` folder. (See "Last updated" entry above for the full run.)
- 🟢 Issue **#5** preset auto-supplement — `PresetSupplementWrite` phase tool writes `<base>_extended.json` with the LLM's user-confirmed slot→bone mappings (additive merge into existing extended preset; never overwrites the shipped file). `add_x_preset` registers the new name on the runtime set immediately. Path-traversal-safe filename validation.
- 🟢 Issue **#6** new preset from scratch — `PresetCustomWrite` phase tool writes `<character_name>_custom.json` from a full LLM-confirmed mapping. Conditional `[强制自定义]` button on the error_choice widget (issue #2) when the error category is `unsupported_rig`; sends `[FORCE_CUSTOM]` prefix, which the system prompt instructs the LLM to recognize and re-run inference with `force_custom=true`. 51-slot canonical key list documented in `agent_workflow.md` so the LLM knows what to map.
- ⚪ MHWI game support (port phase tools, test pipeline)
- ⚪ RE4 game support (FakeBone phase, test pipeline)
- ⚪ RE9 game support (sync child orientation phase, test pipeline)
- ⚪ Per-game advanced tools from video 7
- ⚪ Additional source-model presets (Unity Humanoid generic, more VRC variants)
- 🟢 BlenderClient socket thread-safety — `threading.RLock` + `BlenderBusyError` (non-blocking viewport caller); `try_call(lock_timeout=0.5)` for screenshot route; `_invalidate()` + reconnect on OSError. Prevents WinError 10038 race when viewport auto-refresh and LLM tool calls share the same TCP socket. (4 unit tests added to `test_blender_client.py`)
- 🟢 Anthropic 400 bidirectional history repair — `_heal_history()` fixes both orphan `tool_use` (inject placeholder `tool_result`) and orphan `tool_result` with unknown id (drop the entry); called before every LLM call in `_run_react_turn`. (6 unit tests in `TestHealHistory`)
- 🟢 Widget confirm stuck thinking — `_run_step_with_done_emit()` helper ensures `done` SSE event fires from all three call sites (`/agent/messages`, `/agent/widget/classification`, `/agent/widget/material`); uses try/finally so `done` fires even on exception. (2 route regression tests added)
- 🟢 Widget deferred emit — `_pending_widget` stored in loop state; flushed from `step()` finally block after assistant message SSE fires, guaranteeing widget always appears below LLM commentary text. (2 ordering tests in `test_agent_loop_events.py`)
- 🟢 Physics `_End` bone cascade — `_expand_end_children()` unconditionally adds all `*_End` direct children of merged bones to `bones_to_merge`; no `chain_role` filter (avoids dropping bones whose role was cleared by prepare_only cleanup). (2 unit tests in `test_physics_bones.py`)
- 🟢 `skeleton_align` hidden armature — auto-unhide (`hide_viewport=False` + `hide_set(False)`) before `select_set(True)`; post-select assertion emits `PRECONDITION:not_selectable` with user-facing hint if still unselectable.
- 🟢 ~~**Material widget empty rows**~~ — superseded by issue #11 (commit `6adb153`): `AgentLoop._suggest_texture_mapping` runs an LLM pre-fill pass before emitting `widget_material`; 3-tier precedence (LLM suggestion > existing wired non-`connected_no_image` > none); JSON-validated and filtered against the inspector's actual materials/slots/files.
- ⚪ **Static asset cache-busting** — `app.js` / `style.css` served without version query string; browser caches stale JS after deployments, making frontend hotfixes unverifiable. Add `?v=<git-short-sha>` query string to static asset URLs in the base template.
- ⚪ **Consecutive tool_use without tool_result (Anthropic 400)** — `_heal_history` repairs history after the fact, but does not prevent the LLM from issuing two back-to-back `tool_use` blocks in the same turn. Mitigation: strengthen "one tool per turn" constraint in `agent_workflow.md` prompt, and detect duplicate tool names in `_dispatch` to short-circuit before they reach the API.
- ⚪ **Done watchdog mis-timing** — 5 s watchdog fires while a legitimately long tool call (e.g. large physics_chains) is still running, unlocking the chat input prematurely. Watchdog should start only after `message(assistant)` fires, and the timeout should be configurable (10–15 s default).
- 🟢 **Phase transition protocol** (issue #15) — shipped both layers: (A) `agent_workflow.md`
  gains a `## Phase Transition Protocol` section injected into the system prompt that mandates
  report-then-wait after every phase-advancing tool, with query tools explicitly whitelisted
  for mid-pause Q&A and phase tools forbidden until the user says continue; (B) backend rail
  on `AgentLoop` — new `_phase_just_advanced` flag flipped True in `_execute_tool_call`'s
  phase-advance branch, consumed by the existing wrap-up branch in `_run_react_turn` (now
  reads `state != RUNNING_PHASE or _phase_just_advanced`) which runs ONE `tools=None`
  `llm.chat` to get a completion report and returns it to the user. Interrupt check (issue
  #14) runs before the wrap-up so an interrupted user never waits through an extra LLM call.
  Mirrored in the DSML branch.

---

## P3 — Future / nice-to-have

- 🟢 **Agent interrupt mechanism** (issue #14) — `AgentLoop.interrupt()` + `POST /agent/interrupt/{sid}` + Escape key + dismissable "已打断" banner. Bail point is between rounds of `_run_react_turn` plus inside the per-tool-call inner loop; the existing try/finally placeholder injector keeps history orphan-free. Frontend ignores Esc when typing into an input/textarea/select, and when status isn't `thinking`.
- ⚪ Physics classification widget: hierarchical tree view (chain parent-child relationship rendered as nested/indented rows or collapsible tree; requires multi-level groupby or JS tree component — deferred from Phase 4A widget work)
- ⚪ Cross-session state continuation (B8 留的口子)
- ⚪ Tool retrieval / Content RAG upgrade if plan.md grows large (C11 留的口子)
- ⚪ Multi-provider expansion (Qwen3 / Gemini 2.5 Flash / GPT-5 mini)
- ⚪ Auto rollback / .blend snapshots if "can't go back" becomes high-frequency pain (B7 留的口子)
- ⚪ React frontend migration if interaction complexity grows (C12 留的口子)
- ⚪ LangGraph rewrite as a learning exercise (C9 留的口子)
- ⚪ Asset marketplace / curated demo model list (D15 留的口子)
- ⚪ Local model support (Ollama, Qwen3-32B etc.) for offline deployment

---

## Backlog (unscheduled)

Items awaiting clarity / triage:

- _empty for now_

---

## Risk Notes

- **DeepSeek V4 capability uncertain at our workload**. If classification accuracy on key decisions (X preset / physics route / PBR mapping) falls below ~80%, fall back to Sonnet 4.6. Track in P1 A/B eval.
- **MMD model quality varies**. A4 retains VRC fallback. If MMD-first MVP fails, swap to a single VRC standard model.
- **1-hour engineering target may slip**. 1-7 全流程实测可能超时；优先保证流程跑通，时长优化属于后续 polish 阶段。
- **Toolkit auto-fix coverage assumed strong** (per user). If real-world MVP shows toolkit failures more common than expected, B7 error handler needs a thicker fallback path.
- **No test asset in repo** (D15). First-time user friction depends on `docs/demo_setup.md` quality.
- **uv 仍在 0.x** (C13). On breakage, fall back to `pip + venv`; pyproject.toml standardization preserves portability.
