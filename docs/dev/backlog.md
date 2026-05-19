# Backlog

Forward-looking task list. For shipped work and release history see [../CHANGELOG.md](../CHANGELOG.md).

Status badges follow the [project convention](../AGENTS.md#status-badge-convention):

| Symbol | Meaning |
|--------|---------|
| 馃煝 | Done |
| 馃煛 | In progress |
| 鈿?| Not started |
| 馃敶 | Blocked |

---

## Current state

**MVP shipped** (2026-05-17, L3 acceptance against MMD/VRC source models).

Stack: FastAPI + hand-rolled ReAct loop + 16 phase tools; React 19 + TypeScript + Vite frontend; optional Tauri 2 desktop shell with bundled Python sidecar. LLM providers: Anthropic, OpenAI-compatible, Ollama. **534 unit tests** + 70+ Playwright e2e checks. Latest live numbers track in CHANGELOG.

Current work is post-MVP polish 鈥?see open items below.

---

## P1 鈥?Important

- 鈿?DeepSeek V4 vs Sonnet 4.6 A/B eval on the three high-leverage classification points (X-preset, physics route, PBR mapping). The most genuinely uncertain claim left in the project 鈥?capability gap directly affects which model the user wants the app pointed at.
- 鈿?Single-page user-facing landing copy 鈥?written for non-developers, avoids listing prereqs explicitly per design A2.
- 鈿?Toolkit dependency preflight check 鈥?surface missing/disabled Blender addons before a run instead of silently 500ing mid-phase. New `GET /app/toolkit_status` route probes Blender once via a single `execute_code` round-trip (checks `bpy.context.preferences.addons` keys + `hasattr(bpy.ops, ...)` for critical operators) and returns per-tool status `{id, label, status}` where `status 鈭?{present, disabled, missing}` so the UI distinguishes "not installed" from "installed but disabled" (the common post-Blender-upgrade case). Required set: Modding-Toolkit / Modder-Batch-Tool / RE Mesh Editor / MHW Model Editor / RE Chain Editor / blender-mcp. Frontend: small panel on the session-config form auto-loaded when the form mounts + a manual "Re-check" button; results cached for the session. Critical-tool gate: form's Start button disabled if any `critical=true` tool isn't `present`, with the offending row(s) highlighted and an inline link to the install doc.
- 鈿?Provider abstraction: uniform SSE streaming across Anthropic / OpenAI / Ollama (the three back-ends differ subtly today; consumer-side gets an `LLMResponse` rather than a token stream).

---

## P2 鈥?Post-MVP

- 鈿?MHWI game support 鈥?port phase tools, test full pipeline. The real "phase 2 of the project" if you want one.
- 鈿?RE4 game support (FakeBone phase, test pipeline).
- 鈿?RE9 game support (sync child orientation phase, test pipeline).
- 鈿?Per-game advanced tools from video 7 (MHWs-specific; out of MVP scope).
- 鈿?Additional source-model presets (Unity Humanoid generic, more VRC variants).
- 鈿?Consecutive `tool_use` without `tool_result` 鈥?prevent at `_dispatch` instead of post-hoc `heal_history`. Strengthen "one tool per turn" in `agent_workflow.md` prompt and detect duplicate tool names before they reach the API.
- 鈿?Done-watchdog mis-timing 鈥?5 s watchdog fires while a legitimately long tool call (e.g. large `physics_chains`) is still running, unlocking chat input prematurely. Start only after `message(assistant)` fires, make timeout configurable (10鈥?5 s default).
- 鈿?Session-store GC 鈥?`~/.modpilot/sessions/{sid}/moves.jsonl` accumulates forever; the context-management design called for an mtime sweep that didn't ship. Per-session is bounded; across-session is not.

---

## P3 鈥?Future / nice-to-have

- 鈿?Physics classification widget hierarchical tree view (nested/collapsible rows for parent-child chain relationships; requires multi-level groupby or a JS tree component).
- 鈿?Cross-session resume affordance 鈥?`session_id` recovery already replays phase-completion summaries, but there's no UI to pick a prior session and no mid-phase replay (scene-is-memory covers most of it, but a "browse my sessions" surface is missing).
- 鈿?Tool retrieval / Content RAG upgrade if `plan.md` grows large (C11 鐣欑殑鍙ｅ瓙).
- 鈿?Multi-provider expansion (Qwen3 / Gemini 2.5 Flash / GPT-5 mini).
- 鈿?Auto rollback / `.blend` snapshots if "can't go back" becomes a high-frequency pain (B7 鐣欑殑鍙ｅ瓙).
- 鈿?LangGraph rewrite as a learning exercise (C9 鐣欑殑鍙ｅ瓙).
- 鈿?Asset marketplace / curated demo model list (D15 鐣欑殑鍙ｅ瓙).

---

## Removed from backlog

These items were overtaken by other work and no longer need tracking:

- ~~Static asset cache-busting~~ 鈥?htmx-era concern; Vite handles fingerprinting natively.
- ~~Local model support (Ollama, Qwen3-32B etc.) for offline deployment~~ 鈥?shipped 2026-05-15 via `OllamaProvider`.
- ~~React frontend migration if interaction complexity grows~~ 鈥?shipped 2026-05-18; the whole frontend is React 19 + Vite.
- ~~Prompt-cache hit-rate observability~~ 鈥?superseded by 2026-05-19 context-management layer (phase-boundary compaction + `moves.jsonl` index + `query_history` meta-tool). Per-turn prompt size is now structurally bounded; Anthropic prompt caching is a marginal optimization rather than a primary cost lever.

---

## Risk Notes

- **DeepSeek V4 capability uncertain at our workload**. If classification accuracy on key decisions (X-preset / physics route / PBR mapping) falls below ~80%, fall back to Sonnet 4.6. Tracked by the P1 A/B eval.
- **MMD model quality varies**. A4 retains VRC fallback. If MMD-first acceptance regresses, swap demo paths to a single VRC standard model.
- **Toolkit auto-fix coverage assumed strong** (per user). If real-world usage shows toolkit failures more common than expected, the B7 error handler needs a thicker fallback path.
- **No test asset in repo** (D15). First-time user friction depends on `docs/demo_setup.md` quality.
- **uv still 0.x** (C13). On breakage, fall back to `pip + venv`; pyproject.toml standardization preserves portability.
