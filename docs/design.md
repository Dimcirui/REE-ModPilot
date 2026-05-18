# REE-ModPilot Design Decisions

A compact reference of the load-bearing decisions behind ModPilot. Each item lists the
decision, one paragraph of rationale, one-line rejections of the main alternatives, and
(when relevant) the escape hatch we left for the future. Discussion notes have been
pruned — git history holds the original 中文 draft if you need them.

## Status

| # | Topic | Layer | Status | Summary |
|---|-------|-------|--------|---------|
| A1 | Agent role | Product | 🟢 | Guided wizard (b); MVP only accepts structured source models |
| A2 | User skill assumed | Product | 🟢 | Tier 2 — Blender beginner; gate-as-filter; lazy explanation; internal prerequisite list |
| A3 | "30 min" semantics | Product | 🟢 | 30 min marketing anchor / 1 h engineering target; MVP covers videos 1-7 |
| A4 | MVP acceptance case | Product | 🟢 | L3 in-game / MHWs single-game / user-supplied MMD-preferred source |
| B5+B6 | State sensing + tool granularity | Arch | 🟢 | Mid-tier phase tools (~12–15) + light Agent cache + per-phase spot-check |
| B7 | Error recovery | Arch | 🟢 | Structured error + LLM phrasing + retry/skip/ask; no rollback; entry sanity checks |
| B8 | Cross-session persistence | Arch | 🟢 | Out of MVP; rely on `.blend` save + scene rescan |
| C9 | Agent framework | Tech | 🟢 | Hand-rolled ReAct on raw SDKs for MVP; LangGraph rewrite post-MVP as a learning exercise |
| C10 | LLM choice | Tech | 🟢 | Provider abstraction; DeepSeek V4 default; Claude Sonnet/Haiku as oracle/fallback; Ollama Cloud added later |
| C11 | RAG? | Tech | 🟢 | No RAG in MVP; inject `docs/agent_workflow.md` into system prompt + prompt cache |
| ~~C12~~ | ~~Frontend stack~~ | ~~Tech~~ | ⚪ Superseded by C25 (2026-05-18) | ~~htmx + Jinja2~~ |
| C13 | Python deps | Tech | 🟢 | `uv` |
| D14 | Directory + test layout | Eng | 🟢 | Functional submodules under `ModPilot/app/`; pytest `unit` / `integration` markers |
| D15 | Test assets | Eng | 🟢 | Not bundled; `docs/demo_setup.md` lists picks; user downloads on first run |
| E16 | `PhaseResult` shape | Impl | 🟢 | 3 fields: `success` / `state_diff` / `error`; LLM phrasing lives outside |
| E17 | Classification location | Impl | 🟢 | Agent loop classifies; phase tools just execute |
| E18 | Sync BlenderClient under FastAPI | Impl | 🟢 | `asyncio.to_thread(phase.run, ...)`; client stays sync |
| E19 | Physics preset storage | Impl | 🟢 | Standalone JSON; injected into system prompt at startup |
| E20 | Vision model routing | Impl | 🟢 | Independent `vision_model` config; default to Qwen-VL / DeepSeek-VL2; Claude only as upgrade |
| E21 | Texture-slot UX | Impl | 🟢 | Bulk proposal + low-confidence highlight + user fixes exceptions; reusable `propose_and_confirm` primitive |
| E22 | Stage 4 loop control flow | Impl | 🟢 | Videos 1-3 = batch block; videos 4-7 = `NEGOTIATING` inner loop w/ phase-scoped history |
| E23 | Video 4 internal split | Impl | 🟢 | Two serial sub-phases: bone-ops (classification) → physics-file (chain2 params); structured handoff |
| E24 | `prompts.py` structure | Impl | 🟢 | Functional builders; system / per-phase split; cache on system; `agent_workflow.md` is the injected manual |
| E25 | Agent loop fine print | Impl | 🟢 | Phases 1-3 also via tool-call; `tool_schema()` on ABC; error format LLM-driven, error parse keyword-driven; in-memory sessions |
| C25 | Frontend rebuild + desktop shell | Tech | 🟢 (supersedes C12, 2026-05-18) | React 19 + TS + Vite + motion SPA; optional Tauri v2 shell for native drag-drop file paths |

Legend: ⚪ open / 🟡 in discussion / 🟢 decided / 🔴 blocked

---

# A — Product / UX

## A1. Agent role in the workflow

**Decision** (2026-05-08): **Guided wizard** — the agent breaks `docs/plan.md` into steps, explains each, fires the right operator, waits for "OK" or a complaint, then advances. MVP only accepts **structured source models** (MMD / VRC / Unity Humanoid / specific-game unpacks) — no arbitrary FBX.

**Why**: A wizard fits the "Blender-literate, modding-illiterate" target (A2), bakes error recovery into the rhythm (user confirms every step), and matches the natural decomposition of `plan.md`. The structured-source constraint shrinks the X-preset space to ~3-5 candidates, making preset routing tractable in a single LLM turn.

**Rejected**:
- Fully automatic batch mode — needs deep industry-experience priors we don't have; classification (X-preset, physics route) is aesthetic, not procedural.
- Passive copilot / Q&A — devolves into a `plugin_api.md` doc search; no Agent value, 30-min goal unreachable.

**Escape hatch**: Tool layer stays agnostic of "structured source" — only the prompts/flow constrain it, so any-source FBX support is a flow addition, not a refactor.

## A2. User starting level

**Decision** (2026-05-08): **Tier 2 — Blender beginner** (knows imports, outliner, mode switching; doesn't know RE Engine modding). The gate is intentional — installing two add-ons already filters out Tier 1. **Lazy explanation**: the agent does *not* volunteer deep "why" lectures; errors become the explanation hook with high-quality phrasing. We keep an internal prerequisite checklist (Appendix P) but never list it on the landing page (listing it scares off potential users).

**Rejected**:
- Tier 1 (zero Blender experience) — turns the agent into a Blender tutorial site; budget blown.
- Tier 3 (Blender expert) — overshoots; product collapses to a Modding-Toolkit batch wrapper.

## A3. What "30 minutes" means

**Decision** (2026-05-08): **30 min is a marketing anchor; 1 hr is the engineering target** for the full videos 1-7 flow. MVP scope is therefore **videos 1-7**, not 1-3 — the original 1-3 cut would skip the experience-classification phases (4-7) where the agent's real value lives (physics-bone naming, PBR routing, equipment slots). Reference points: author personal best ~6 min; a Modding-Toolkit hand-runner ships a quality mod in 1-2 h, basic quality in ~30 min.

**Rejected**:
- Wall-clock includes user think time — uncontrollable, anti-pattern for benchmarking.
- Restrict to videos 1-3 — strips out the parts that justify an LLM in the loop.

**Escape hatch**: 1 hr isn't load-bearing. If schedule slips, ship correctness first and tune duration later. If video 4-7 classification accuracy disappoints, fall back to "agent proposes candidates, user confirms."

## A4. MVP acceptance case

**Decision** (2026-05-08):

| Axis | Value |
|---|---|
| Pipeline range | `docs/plan.md` videos 1-7 |
| Target game | **MHWs only** |
| Source model | User-supplied; **MMD preferred / VRC secondary** |
| Acceptance bar | **L3** — exports actually run in-game |
| Time target | 1 h |
| Demo runner | Author |

**Why MHWs**: RE Engine games are similar enough that one deep integration generalizes; multi-game in MVP explodes the workload. **Why MMD**: textures usually single-sheet, geometry pre-assembled — fewer pre-processing surprises than VRC. **Why L3**: toolkit auto-handles game config / paths / REF prereqs, so L3 is cheap *if* L2 passes. The "success signal" simplifies to "no severe export errors → almost certainly runs."

**Rejected**:
- L1 (operators don't crash) / L2 (files written) — too weak to claim "actually works."
- Bundle a demo model in the repo — license + filesize concerns (D15).

**Escape hatch**: If MMD models prove too stylized for MHWs, fall back to a canonical VRC reference model. `verify_mvp.py` is written game-agnostic to ease MHWI / RE4 / RE9 extension.

---

# B — Architecture

## B5 + B6. State sensing + tool granularity (joint decision)

**Decision** (2026-05-08):

- **Tool granularity** — **mid-tier phase tools** aligned to `plan.md` steps, ~12-15 total. Each tool: (1) spot-checks scene at entry, (2) optionally calls LLM for in-phase classification (high-conf = auto, low-conf = surface candidates to user), (3) drives the underlying operators in deterministic Python, (4) returns a structured `PhaseResult` (E16) and updates cache.
- **State sensing** — **hybrid**. Agent keeps a light cache (armature/mesh lists, current mode, loaded X/Y preset, active object). Each phase tool runs `get_scene_info` at entry and the agent diffs against cache. Tools update cache on exit.

**Why**: Flattening to ~50 raw operators inflates round-trips 5-10x, blows context, multiplies failure points, and doesn't surface where LLM value actually is (classification). Phase-level spot-checks are cheap enough; per-operator checks would burn budget. The deal: **trade Python orchestration code for stability, cost, and per-step testability** (mockable socket).

**Rejected**:
- Expose every operator as an LLM tool — fanout too high; LLM does mechanical scheduling instead of judgment.
- Stateless agent (re-read everything every turn) — wastes round-trips on data we just wrote.
- Static "I tracked everything in Python" — drifts from real Blender state, brittle.

**Escape hatch**: Phase boundaries can be re-cut later (merge / split adjacent phases) without API impact — the LLM-visible tool count is an implementation detail. A debug `execute_code` operator exists but is **not** registered as an MVP tool to prevent the LLM from going off-script.

## B7. Error recovery

**Decision** (2026-05-08): Each phase tool returns a `Result<state_diff, structured_error>`. The agent loop phrases the error via LLM (one shot), then offers the user **retry / skip / ask** (Q&A mode). No rollback. Sanity checks at key phase boundaries (e.g. Phase 4 entry requires Phase 3 vertex-group match ≥ ~90%).

**Why**: Most errors are user-state mismatches (wrong selection, wrong mode, low-quality result), not toolkit crashes. The LLM's job is *to phrase*, not to invent fixes — phase tools never call LLM, the loop never lets the LLM call extra operators on the error branch.

**Rejected**:
- Auto-snapshot `.blend` for rollback — heavy disk I/O, naming hell; Blender's Ctrl+Z + user `.blend` saves cover the realistic cases.
- LLM-driven "creative repair" — violates `project_python_over_llm` memory; failure modes multiply.

**Escape hatch**: Add lightweight per-phase JSON snapshots if MVP turns up real "can't get back" pain. Tighten the "ask" branch's LLM autonomy if it gives bad advice in practice.

## B8. Cross-session persistence

**Decision** (2026-05-08): **Out of MVP.** Refresh = new session. `.blend` is the implicit progress store; on session start, `get_scene_info` rebuilds cache.

**Why**: A4 demo runs in one sitting; full persistence (progress + paths + cache + history) costs a lot for one user.

**Escape hatch**: If users report mid-run interruptions, add a minimal session-state JSON (phase index + cache snapshot, no chat history) before considering full restoration.

---

# C — Technology

## C9. Agent framework

**Decision** (2026-05-08): **Raw Anthropic / OpenAI SDKs + hand-rolled ReAct loop** (~300 lines) for MVP. Post-MVP, rewrite the same agent in **LangGraph** on a branch as a learning exercise.

**Why**: The phase architecture (B6) already is a near-state-machine; framework overhead beats own weight. Learning happens deliberately *after* MVP so framework churn doesn't slip the schedule.

**Rejected**:
- Full LangChain — heavy abstraction, frequent breakage, debug pain; we use ~10% of it.
- LangGraph upfront — genuinely fits, but the learning curve costs MVP velocity.
- Claude Agent SDK — small ecosystem, transferable skills limited.

## C10. LLM choice

**Decision** (2026-05-08): **Provider abstraction** (~100 LoC `LLMClient`). Dev default: **DeepSeek V4** via OpenAI-compatible API. Oracle / fallback: **Claude Sonnet 4.6** (strong) + **Haiku 4.5** (mid-tier). A third path was added 2026-05-15: **Ollama Cloud** for `deepseek-v4-flash / -pro` (its `/api/chat` is *not* OpenAI-compatible — needs its own provider, see `lesson.md`). Switching = config change, no business-code edit.

**Why**: DeepSeek V4 is strong, cheap, OpenAI-protocol-compatible — fits the dev loop. Claude is debug oracle (rules out "is this a design problem or a model problem"), demo safety net, and A/B baseline.

**Rejected**:
- Lock to a single provider — premature; cost/capability landscape moves fast.
- LangChain's `ChatModel` abstraction — pulls in the whole framework for ~50 lines of routing.
- Local Ollama — hardware requirements, perf, scope creep. (Ollama *Cloud* is fine; same OAI-tool budget, no local GPU.)

**Escape hatch**: A/B (V4 vs Sonnet) on real phase classifications post-MVP to quantify "what we saved vs what we lost." Per-user keys are first-class (matches A2 gate-as-filter).

## C11. RAG?

**Decision** (2026-05-08): **No RAG.** Inject `docs/agent_workflow.md` (the machine-readable execution manual; *not* `plan.md`, which is human video copy — correction 2026-05-09) directly into the system prompt and ride prompt cache.

**Why**: Static, modest-size content; tool surface is 12-15 named phase tools — vector retrieval would add three new failure modes (embedder + DB + retriever) for negligible win. Prompt cache amortizes the cost after one round-trip.

**Rejected**:
- Tool retrieval — built for 100+ tools / MCP marketplaces; we have 15.
- Content RAG on `plan.md` — direct injection is cheaper and more reliable.

**Escape hatch**: If the workflow doc grows substantially (e.g. multi-game unpacking guides) or Q&A becomes high-frequency, lancedb + sentence embeddings + top-k is the upgrade path — without LangChain's retriever layer.

## ~~C12. Frontend stack~~ — superseded by C25 (2026-05-18)

Original decision (2026-05-08): htmx + Jinja2 + vanilla CSS, no SPA framework. Shipped successfully through Stage 5. Replaced because (a) browser sandboxes refuse to expose `File.path` on drop, blocking the "drag your model file into the field" UX, and (b) htmx's partial-swap model can't express the adaptive-UI panels we want next (capability-aware feature toggles, reasoning-trace drawers). See **C25** for current decision.

## C13. Python deps

**Decision** (2026-05-08): **uv** for deps + venv + Python-version management.

**Why**: 10-100x faster than pip, replaces pyenv + venv + pip with one tool, `uv.lock` is standard, active maintenance.

**Rejected**:
- pip + venv — slow, no modern lockfile, needs pyenv alongside.
- poetry — slow, weird resolver edge cases, slow release cadence.
- conda/mamba — only earns its keep for non-Python C deps; we don't have any.
- rye — Astral's prior project; uv is the successor.

**Escape hatch**: uv is still 0.x; `pyproject.toml` is standard so pip + venv is a painless fallback if a blocker hits.

---

# D — Engineering

## D14. Directory + test layout

**Decision** (2026-05-08): Functional submodules under `ModPilot/app/`: `blender/`, `llm/`, `agent/`, `phases/`, `routes/`. Tests split into `unit/` (mock socket — default in CI) and `integration/` (real Blender on 9876, marker-gated). Stage scripts (`verify_blender_mcp.py`, `verify_mvp.py`) stay at repo root, outside pytest.

**Why**: Module boundaries match the architectural layers in B6/C10. Two-tier tests let CI run fast while integration stays a manual lever.

**Note (2026-05-18)**: With C25, `templates/` and `static/` are gone — replaced by `ModPilot/frontend/` (React + TS + Vite + Tauri). The rest of the layout stands.

**Escape hatch**: Sub-dirs are renamable post-MVP (e.g. split `agent/` into `agent/` + `flows/`) without callers caring.

## D15. Test assets

**Decision** (2026-05-08): **Not bundled.** `docs/demo_setup.md` lists model picks, MHWs base armature acquisition (REasy / RE-Toolbox), and asset layout (e.g. `~/.modpilot_assets/`). Don't lock in a specific MMD model until implementation starts.

**Why**: License + filesize.

**Escape hatch**: If MMD candidates underperform, swap to a single canonical VRC model.

---

# E — Implementation

## E16. `PhaseResult` shape

**Decision** (2026-05-09): Three fields — `success: bool`, `state_diff: dict`, `error: PhaseError | None`. `PhaseError` carries `category / operator / message / suggestion / raw`. User-facing message is *not* in the result; the agent loop generates it via LLM (B7).

**Why**: Phase tools stay free of LLM dependencies → trivially unit-testable. Splitting "what happened" (structured) from "how to phrase it" (LLM) keeps both clean.

## E17. Classification location

**Decision** (2026-05-09): **Agent loop classifies; phase tools execute.** Phase tools receive parameters (e.g. `preset: str`) and never call the LLM themselves.

**Why**: Centralizes LLM dependency, deduplicates classification logic, keeps phase unit tests synchronous and LLM-free.

## E18. Sync `BlenderClient` under FastAPI

**Decision** (2026-05-09): `result = await asyncio.to_thread(phase.run, scene_cache, params)`. `BlenderClient` stays synchronous.

**Why**: Single-user tool; no concurrency pressure to justify an async rewrite of the socket layer.

**Escape hatch**: If future needs add concurrent users or WebSocket pushes, convert `BlenderClient` to asyncio sockets at that point.

## E19. Physics preset storage

**Decision** (2026-05-09): Standalone `app/agent/physics_presets.json`, injected into system prompt at startup (same path as `agent_workflow.md` per C11).

**Why**: Data / logic separation; injection is a one-time cost amortized by prompt cache; no runtime read latency.

**Escape hatch**: If preset count balloons (multi-game physics formats), promote to RAG (C11 backup path).

## E20. Vision model routing

**Decision** (2026-05-09): Independent `vision_model` config. Default to a low-cost OpenAI-compatible vision API (Qwen-VL / DeepSeek-VL2). Claude vision only as opt-in upgrade.

**Why**: PBR-channel identification is mid-tier vision; mainstream open / cn models suffice. Claude vision is reserved as oracle for C10 reasons.

## E21. Texture-slot UX

**Decision** (2026-05-09): "**Bulk proposal + low-confidence highlight + user fixes exceptions.**" Reused as a generic `propose_and_confirm` primitive (applies to physics-bone classification too — E22). MMD skips this whole flow when CATS pre-processing already connects nodes.

**Why**: One-by-one prompts are cognitively expensive; pure dump is unguided. The middle path concentrates user attention where it matters.

## E22. Stage 4 loop control flow

**Decision** (2026-05-09): Videos 1-3 ride a single batch block (`RUNNING_PHASE → AWAIT_CONFIRM once → DONE`). Videos 4-7 each carry a `NEGOTIATING` inner loop (propose → user → refine → execute). Per-phase chat history lives inside `NEGOTIATING` only, cleared on exit; results flow out as structured params.

**Why**: The two halves have radically different LLM densities; one state machine forced to handle both becomes either over-engineered for 1-3 or under-engineered for 4-7.

**Escape hatch**: Cap `NEGOTIATING` rounds (anti loop) — exact limit to be picked during implementation.

## E23. Video 4 internal split

**Decision** (2026-05-09): Two serial sub-phases, each with its own `NEGOTIATING` loop:

1. **Bone-ops** — classify physics vs non-listed helper bones; merge non-listed helpers up; emit structured **physics chain list** (chain bones + inferred type like `light_hair / stiff_cloth`).
2. **Physics-file** — create chain2 collections; fill header / group / setting params keyed by inferred type → `physics_presets.json` lookup; iterate with user ("a bit stiffer here").

Hand-off between layers is structured data only — no chat carryover.

**Out of MVP**: nested sub-chain de-nesting.

## E24. `prompts.py` structure

**Decision** (2026-05-09):

1. **Functional builders**: `build_system_prompt(workflow_md, physics_presets) -> str` (called at startup) and `get_phase_prompt(phase, context) -> list[Message]` (called per phase).
2. **System / per-phase split**: global rules + `agent_workflow.md` + `physics_presets.json` in system; phase-specific instructions appended each turn. Prompt cache lands on system; per-phase content is *not* cached.
3. `NEGOTIATING` rounds always include `phase_prompt` so format constraints don't drift; structure is `[system] + [phase_prompt] + [history…] + [user turn]`.

`docs/agent_workflow.md` is the actual file injected (not `plan.md`).

## E25. Agent loop fine print (Stage 3)

**Decisions** (2026-05-10):

- **A — Phases 1-3 also go through LLM tool-calls** (not hard-coded Python). Trade ~0.5-1 s/phase round-trip for a unified execution path with phase 4+, and a natural place for user-facing context messages. Easier to extend later.
- **B — `tool_schema()` on `PhaseTool` ABC**: each phase exposes its own JSON schema; the loop discovers and registers them.
- **C — Error routing**: `ErrorHandler.format()` calls the LLM once to phrase; `ErrorHandler.parse_user_choice()` is keyword-matched (no LLM) — error responses need reliability, not creativity.
- **D — `phase_history` is owned by `NEGOTIATING`**, not by `step()`. `step()` only maintains `global_history`. This avoids "is the state transition already done?" timing bugs.
- **E — `/agent/chat` sessions live in `app.state.agent_sessions: dict[str, AgentLoop]`** (in-memory, lifespan-initialized). No persistence pre-Stage-5 (B8's escape hatch).

---

## C25. Frontend rebuild + desktop shell (supersedes C12, 2026-05-18)

**Decision** (2026-05-18): Replace the Stage-5 htmx + Jinja2 frontend with a **React 19 + TypeScript + Vite + motion** SPA under `ModPilot/frontend/`. Layer an **optional Tauri v2 Rust shell** on top so disk paths can be drag-and-dropped natively. Same backend SSE / REST contracts; only the rendering layer changes.

**Stack**:

| Layer | Choice | Purpose |
|---|---|---|
| Framework | React 19 + TypeScript | Component reuse; types capture the SSE event union in one place (`src/types/sse.ts`) |
| Build | Vite | Fast HMR; proxies `/agent /app /viewport_screenshot /health` → backend `:8000` |
| Animation | `motion/react` | Config-form collapse, widget enter/exit |
| Desktop shell | Tauri v2 (Rust + WebView2) | `tauri-plugin-dialog` for native pickers; `onDragDropEvent` for drag handling |
| Pkg | pnpm | Frontend only; backend keeps uv |

**Two delivery tracks (same React bundle)**:

- **Browser** (`pnpm dev`, `:5173`): fast dev loop; `PathField` falls back to plain text input (detected via `window.__TAURI_INTERNALS__`).
- **Tauri desktop** (`pnpm tauri:dev` → `modpilot.exe`): native drag-drop + Browse button; ~10 MB exe in release builds.

**Why now (not in C12 originally)**:

1. **Paths can't be drag-dropped in a browser sandbox** — the `File` object hides `.path`. Drag-the-folder-in is a high-frequency painpoint the user kept hitting; only a Tauri (or Electron) shell unblocks it.
2. **htmx's partial-swap model strains under adaptive UI** — capability-aware feature toggles ("vision model present → show texture VL panel", "reasoning model → show thinking-trace drawer") fan out enough state that componentization beats htmx ergonomics.

**Invariants (unchanged across the rebuild)**:

- Backend `/agent/*` and `/app/*` REST + SSE contracts. The 11 SSE event types (`message / state / phase_started / phase_completed / tool_call / tool_result / error_choice / widget_classification / widget_material / interrupted / model_type_inferred / done`) flipped from server-rendered HTML fragments back to **typed JSON** — widget routes no longer render templates.
- Browser and desktop share a single bundle. `src/lib/desktop.ts` is the only Tauri-aware module; it dynamic-imports Tauri APIs so browser builds stay Tauri-free.
- Config stack: `ModPilot/.env` (gitignored) + `~/.modpilot/config.json` — Tauri does *not* introduce a third config layer.

**Rejected**:
- Keep htmx + write Electron shell — solves paths but doesn't help adaptive UI; we'd swap anyway.
- Vue 3 + Vite — equivalent; React's discriminated unions express SSE event payloads more cleanly in TS.
- Svelte / Solid — Tauri ecosystem is React-first; example coverage matters during ramp-up.
- Electron — ~150 MB vs ~10 MB; we already need Rust for `src-tauri/`.
- Tauri v1 — superseded by v2's capability model + plugin system.

**Escape hatch**: If Tauri Windows ever blocks a needed capability, the React bundle ports to Electron without changes. Backend sidecar bundling (one `modpilot.exe` that runs uvicorn too) is deferred — currently the user runs the backend themselves.

**Stage-driven UI** (2026-05-18, follow-up): The initial React port mirrored htmx's "render everything in one canvas" model — `ChatPage` held form + stepper + viewport + widgets simultaneously and let the user's eyes do the work of locating the currently-relevant control. Replaced with a per-phase **stage** system under `src/stages/`. `StageRouter` reads `state.phaseStatus` (and `loopState === 'done'`), picks a component out of `STAGE_REGISTRY: Partial<Record<PhaseName, ComponentType<StageProps>>>`, and cross-fades with `motion`'s `AnimatePresence`. Multiple `PhaseName` keys may resolve to the same component (e.g. `phase_2` and `phase_3` both → `Phase23Stage`); the router keys the cross-fade on the **component identity**, not the phase name, so sibling phases sharing a surface advance without remounting (no animation replay between phase_2 done → phase_3 active). The bottom **`ChatStrip`** absorbs the body-occupying chat log: collapsed mode shows status badge + 140-char last-line preview + unread badge; expanded mode reveals `ChatLog` (30 vh max) + `MessageInput`. Each stage owns its right sidebar's content (pending / running / ok / fail cards, activity feed scoped to its phase) and hosts the widget it cares about — `Phase4Stage` overlays `ClassificationWidget` on the viewport, `Phase5Stage` swaps the viewport out for `MaterialWidget` while the widget is open. Phases that haven't been migrated yet (setup_*) fall through to `FallbackStage`, which keeps the old multi-purpose layout. Stages query a new **`ToolRun[]`** field on `useChatState` (phase / name / runId / toolId / success / summary), populated by the SSE dispatchers and paired via `toolId` match falling back to last-unfinished-by-name (handles Ollama / DSML where `tool_use` ids are absent). Two structural changes worth knowing about: (a) `StageRouter` keys by component, not phase name (line ~18); (b) `loopState === 'done'` short-circuits to `DoneStage` regardless of the last active phase. Playwright recorders for each stage live in `ModPilot/scripts/record_phase{23,4,5,6_done}_walkthrough.py` (sharing `_walkthrough_common.py`); they install a fake `EventSource` via `add_init_script` and drive synthetic SSE through `page.evaluate(window.__pushSse(...))`, so a 22-35 s recording walks pending → running → ok → wrap-up without a real LLM round-trip.

**Backend-as-sidecar** (2026-05-18, follow-up): The initial Tauri shell was a thin webview that required the user to run `uvicorn` themselves — fine for dev, broken UX for end users (two processes to start, an open terminal window). Solved by bundling the FastAPI backend as a pyinstaller-frozen exe that the Tauri shell spawns on `setup()` and kills on exit. The pyinstaller spec (`ModPilot/modpilot_backend.spec`) ships `app/data/*.json`, `docs/agent_workflow.md`, and `app/static_built/` (the Vite output) into the frozen tree; runtime asset reads go through `app/resources.py` which picks `sys._MEIPASS` when frozen. Rust spawn lives in `src-tauri/src/lib.rs`: resolves `resource_dir()/binaries/backend/modpilot-backend.exe`, spawns with `APP_HOST` / `APP_PORT` env vars, suppresses the Windows console window via `CREATE_NO_WINDOW`, and binds the child to a Windows **Job Object with `KILL_ON_JOB_CLOSE`** so `taskkill /F`, OOM-kills, or crashes can't orphan the backend (Tauri's `RunEvent::ExitRequested` only covers graceful exits). The React side gains a `BackendSplash` that polls `/health` until reachable before mounting `ChatPage` / `ConfigPage`; `lib/origin.ts` returns an absolute `http://localhost:8000` base for Tauri builds (the webview lives at `tauri://localhost`, so relative `fetch('/health')` would never reach the Python backend) — `lib/api.ts`, `useSSE.ts`, `ViewportPane.tsx`, and `BackendSplash.tsx` all route through it. FastAPI adds a `CORSMiddleware` allowing the three Tauri origins (`tauri://localhost`, `http://tauri.localhost`, `https://tauri.localhost`) — verified with preflight requests from disallowed origins returning the `Disallowed CORS origin` error and allowed origins echoing the right `Access-Control-Allow-Origin`. **Code signing**: `tauri.conf.json` declares `bundle.windows.digestAlgorithm` + `timestampUrl` so the pipeline activates the moment a `certificateThumbprint` is set; `src-tauri/scripts/generate_dev_cert.ps1` creates a self-signed cert for internal testing; `src-tauri/scripts/sign_bundle.ps1` wraps `signtool.exe` to sign the outer `modpilot.exe` AND the bundled sidecar (Tauri's own signing pipeline skips the sidecar). A real EV cert from a CA is needed to bypass Windows SmartScreen — the scripts are ready to consume one via `TAURI_SIGNING_CERT_THUMBPRINT`. Final installer sizes: ~25 MB MSI, ~22 MB NSIS.

**Known gotchas (logged in `lesson.md`)**:

- Tauri v2 on Windows spawns at `(-21333, -21333)` size `158×26` if `tauri.conf.json` omits `"center": true` (the Tao event-target window is misclassified as primary). `center: true` alone fixes it; no JS `show()` needed.
- Browser sandbox drag-drop never exposes `File.path`; `PathField` degrades to text input via `isDesktop`.
- Vite dep-optimization doesn't pick up new Tauri plugins via HMR; kill + restart after adding any `@tauri-apps/plugin-*`.

---

# Appendix P — Internal prerequisite checklist

> Defines what we assume the user already knows. Drives prompt phrasing, UI copy density,
> and error-response wording. **Not published externally** (A2 decision).
> Revise when shipping a feature that depends on a new baseline skill.

**Assumed (the agent doesn't teach these)**:

- Blender 4.x installed (4.3.2 recommended; matches Modding-Toolkit compatibility).
- Adds-on install: `blender-mcp` + `Modding-Toolkit` enabled.
- Imports FBX / PMX / glTF; saves `.blend` files.
- Basic viewport navigation (orbit / zoom / pan); N / T panel toggles.
- Outliner selection; knows ARMATURE / MESH / EMPTY / CAMERA / LIGHT.
- Active object vs selected objects (yellow vs red).
- OBJECT / EDIT / POSE mode switching and what each is for.
- Armature modifier binds mesh to bones; vertex group is a thing (no weight-painting expertise required).
- ★ **Source-model identification** (A1 hard constraint): can name the source's family (VRChat / MMD / Unity Humanoid / specific game). MVP only accepts these structured sources.

**Not assumed (agent teaches or auto-handles)**:

- RE Engine model structure, bone-naming rules, X/Y preset concept, helper bones.
- Vertex group / weight internals (mentioned only on error, never volunteered).
- Physics-bone topology, `chain_role` custom properties, `_End` terminal bones.
- MDF2 / MRL3 material formats, PBR channel mapping, texconv flow.
- Batch export config, `parts_mask`, BoneSystem (video 6).
