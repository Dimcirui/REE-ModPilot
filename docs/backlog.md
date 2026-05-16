# Backlog

Priority-ranked task list. Status badges follow the [project convention](../AGENTS.md#status-badge-convention):

| Symbol | Meaning |
|--------|---------|
| 🟢 | Done |
| 🟡 | In progress |
| ⚪ | Not started |
| 🔴 | Blocked |

Tasks are grouped by priority band (P0 → P3). Within a band, ordering is suggested execution sequence; tasks are independently deliverable unless an explicit dependency is noted.

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
- ⚪ Run a full L3 acceptance pass with self-provided assets — user-side step (requires real MMD model + game install); procedure is now documented in `docs/demo_setup.md` §5.

---

## P1 — Important but not MVP-blocking

- ⚪ Full frontend — session config form: pre-run parameter collection (source model, mod root, author/character name, export settings) ([#3](https://github.com/Dimcirui/REE-ModPilot/issues/3))
- ⚪ Full frontend — interactive confirmation widgets: Phase 4A bone classification table + Phase 5 material mapping table ([#7](https://github.com/Dimcirui/REE-ModPilot/issues/7))
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
- ⚪ Source model type auto-detection via toolkit preset bone mapping coverage ([#4](https://github.com/Dimcirui/REE-ModPilot/issues/4))
- ⚪ Auto-supplement existing preset for low-coverage models (MMD variants, Humanoid) ([#5](https://github.com/Dimcirui/REE-ModPilot/issues/5))
- ⚪ Create new input preset via toolkit preset editor when no match found ([#6](https://github.com/Dimcirui/REE-ModPilot/issues/6))
- ⚪ **Phase transition protocol** — add explicit inter-phase consultation behavior.
  Current gap: after a phase tool returns success, the loop immediately re-enters
  `RUNNING_PHASE` with no architectural guarantee of a pause. The LLM may call the
  next phase tool in the same turn without checking state or informing the user.
  Intended design: phase advancement (ReAct tool calls) and inter-phase consultation
  (query tools + user Q&A) are conceptually distinct modes but share the same
  `RUNNING_PHASE` state. Fix options in priority order:
  (A) `agent_workflow.md` phase transition protocol: after phase success, call query
      tools to verify outcome, report to user, wait for explicit direction before next
      phase. Lightweight — prompt-only, no state machine change.
  (B) `PHASE_COMPLETE` state: loop pauses after each phase, forces verification +
      user-facing report before re-entering RUNNING_PHASE. Architecture change.
  (C) Separate NEGOTIATING into all phases that have classification/user decisions
      (currently only Phase 4A/4B), not just physics phases.
  Mid-phase inline Q&A is a related sub-problem: user questions during a phase should
  be answerable using query tools (NOT phase tools) without advancing phase state.
  Rule: query tools OK in Q&A, phase tools prohibited until user directs next action.

---

## P3 — Future / nice-to-have

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
