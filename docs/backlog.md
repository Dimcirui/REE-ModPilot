# Backlog

Priority-ranked task list. Status badges follow the [project convention](../AGENTS.md#status-badge-convention):

| Symbol | Meaning |
|--------|---------|
| 🟢 | Done |
| 🟡 | In progress |
| ⚪ | Not started |
| 🔴 | Blocked |

Tasks are grouped by priority band (P0 → P3). Within a band, ordering is suggested execution sequence; tasks are independently deliverable unless an explicit dependency is noted.

**Last updated**: 2026-05-11 — E2E testing session 2 complete. Full fix log: [docs/e2e_fixes.md](e2e_fixes.md). Session 2 highlights: mode_set active-object fix (7 call sites); DSML markup strip plain-string fallback; query-tool throttle (max-rounds 8→15 + consecutive-query cap); ERROR_HANDLING↔ASK_MODE deadloop resolved; prepare_only cleanup flow + auto-verify via _End bone detection (retry-once, marks_clean in diff); bones_to_clear for native game bones; SEPARATE mode revert for auto_create_chains.

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

- ⚪ MVP frontend — htmx chat UI: Jinja2 template + SSE streaming + phase progress indicator ([#1](https://github.com/Dimcirui/REE-ModPilot/issues/1))
- ⚪ MVP frontend — error handling button group: retry / skip / ask via SSE `error_choice` event ([#2](https://github.com/Dimcirui/REE-ModPilot/issues/2))

### Stage MVP — verification

- ⚪ `verify_mvp.py` — End-to-end script (Operator FINISHED checks + file existence + non-zero size + key intermediate state checks)
- ⚪ `docs/demo_setup.md` — Specific MMD model recommendation, MHWs skeleton acquisition, REF setup hints (D15)
- ⚪ Run a full L3 acceptance pass with self-provided assets

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
