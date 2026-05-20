# Backlog

Forward-looking task list. For shipped work and release history see [../../CHANGELOG.md](../../CHANGELOG.md).

Status badges follow the [project convention](../../AGENTS.md#status-badge-convention):

| Symbol | Meaning |
|--------|---------|
| 🟢 | Done |
| 🟡 | In progress |
| ⚪ | Not started |
| 🔴 | Blocked |

---

## Current state

**MVP shipped** (2026-05-17, L3 acceptance against MMD/VRC source models).

Stack: FastAPI + hand-rolled ReAct loop + 16 phase tools; React 19 + TypeScript + Vite frontend; optional Tauri 2 desktop shell with bundled Python sidecar. LLM providers: Anthropic, OpenAI-compatible, Ollama. **534 unit tests** + 70+ Playwright e2e checks. Latest live numbers track in CHANGELOG.

Current work is post-MVP polish — see open items below.

---

## P1 — Important

- ⚪ Vision model integration (Phase 0 + E20) — PBR channel classification fallback. Config fields exist (`vision_model` / `vision_api_key` / `vision_base_url`) but never consumed. Requires: provider image content block support (`docs/dev/issues/vision_model_integration.md`), Pillow dep, and Layer 3 integration into `suggest_texture_mapping()`.
- ⚪ DeepSeek V4 vs Sonnet 4.6 A/B eval on the three high-leverage classification points (X-preset, physics route, PBR mapping). The most genuinely uncertain claim left in the project — capability gap directly affects which model the user wants the app pointed at.
- ⚪ Single-page user-facing landing copy — written for non-developers, avoids listing prereqs explicitly per design A2.
- ⚪ Toolkit dependency preflight check — surface missing/disabled Blender addons before a run instead of silently 500ing mid-phase. New `GET /app/toolkit_status` route probes Blender once via a single `execute_code` round-trip (checks `bpy.context.preferences.addons` keys + `hasattr(bpy.ops, ...)` for critical operators) and returns per-tool status `{id, label, status}` where `status ∈ {present, disabled, missing}` so the UI distinguishes "not installed" from "installed but disabled" (the common post-Blender-upgrade case). Required set: Modding-Toolkit / Modder-Batch-Tool / RE Mesh Editor / MHW Model Editor / RE Chain Editor / blender-mcp. Frontend: small panel on the session-config form auto-loaded when the form mounts + a manual "Re-check" button; results cached for the session. Critical-tool gate: form's Start button disabled if any `critical=true` tool isn't `present`, with the offending row(s) highlighted and an inline link to the install doc.
- ⚪ Provider abstraction: uniform SSE streaming across Anthropic / OpenAI / Ollama (the three back-ends differ subtly today; consumer-side gets an `LLMResponse` rather than a token stream).

---

## P2 — Post-MVP

- ⚪ Vision model E20a — UV-assisted mesh dedup. Blender-side UV wireframe overlay rendering + VLM judgment for `MaterialConsolidate._scan_groups()` (`docs/dev/issues/vision_model_integration.md`).
- ⚪ Phase 4C: 全身碰撞体 + clspFlags0 碰撞过滤 — 新增 phase_4c 阶段。Step 1: 导入内置 full_body.clsp.3 生成全身碰撞体（ColliderCreate, sub-step）。Step 2: LLM 分类 + widget 用户确认每条 chain 的碰撞部位掩码（NEGOTIATING）。Step 3: 写入 clspFlags0（ColliderApply, advancing）。内置资源：app/data/full_body.clsp.3 + clsp_body_bits.json。参考 Wilds Chain 插件的 create_full_body_clsp / set_clsp_flags。
- ⚪ Phase 4B post-step: Chain header 默认值调整到 MHWs 特调 — RE Chain Editor 的 Chain2HeaderData 默认值与 MHWilds 特调值有 7 处差异（calculateMode/chainAttrFlags/calculateStepTime/modelCollisionSearch/highFPSCalculateMode/wilds_unkn1/wilds_unkn2）。在 PhysicsChains.run() 末尾追加 post-step 覆盖这 7 个字段，不新增 tool，不修改 _PHASE_SEQUENCE。
- ⚪ MHWI game support — port phase tools, test full pipeline. The real "phase 2 of the project" if you want one.
- ⚪ RE4 game support (FakeBone phase, test pipeline).
- ⚪ RE9 game support (sync child orientation phase, test pipeline).
- ⚪ Per-game advanced tools from video 7 (MHWs-specific; out of MVP scope).
- ⚪ Additional source-model presets (Unity Humanoid generic, more VRC variants).
- ⚪ Consecutive `tool_use` without `tool_result` — prevent at `_dispatch` instead of post-hoc `heal_history`. Strengthen "one tool per turn" in `agent_workflow.md` prompt and detect duplicate tool names before they reach the API.
- ⚪ Done-watchdog mis-timing — 5 s watchdog fires while a legitimately long tool call (e.g. large `physics_chains`) is still running, unlocking chat input prematurely. Start only after `message(assistant)` fires, make timeout configurable (10–15 s default).
- ⚪ Session-store GC — `~/.modpilot/sessions/{sid}/moves.jsonl` accumulates forever; the context-management design called for an mtime sweep that didn't ship. Per-session is bounded; across-session is not.
- ⚪ Session index + completed-session auto-archive — disk-side counterpart to the FE resume prompt (shipped 2026-05-19). Maintain `~/.modpilot/sessions/index.jsonl` updated debounced on every `MoveLog.append`, carrying `{session_id, created_ts, last_activity_ts, completed, current_phase}`. On DONE, rename `~/.modpilot/sessions/{sid}/` → `~/.modpilot/sessions/archived/{sid}/`. Cheap fast path for the `/agent/session/status` endpoint (today it tail-scans the per-session log on every call) and a natural place to hook the GC sweep above.

---

## P3 — Future / nice-to-have

- ⚪ Vision model E20b — Reverse texture inference (experimental). Merged-UV layout → unknown texture slot inference. Feature-gated, suggestion-only (`docs/dev/issues/vision_model_integration.md`).
- ⚪ Physics classification widget hierarchical tree view (nested/collapsible rows for parent-child chain relationships; requires multi-level groupby or a JS tree component).
- ⚪ Cross-session resume affordance — single-session resume prompt and completed-session detection shipped 2026-05-19. A multi-session "browse my sessions" list view is NOT planned: ModPilot's workflow is one-mod-per-pipeline, parallel sessions are unlikely. Revisit only if real usage shows demand.
- ⚪ Tool retrieval / Content RAG upgrade if `plan.md` grows large (C11 留的口子).
- ⚪ Multi-provider expansion (Qwen3 / Gemini 2.5 Flash / GPT-5 mini).
- ⚪ Auto rollback / `.blend` snapshots if "can't go back" becomes a high-frequency pain (B7 留的口子).
- ⚪ LangGraph rewrite as a learning exercise (C9 留的口子).
- ⚪ Asset marketplace / curated demo model list (D15 留的口子).

---

## Removed from backlog

These items were overtaken by other work and no longer need tracking:

- ~~Static asset cache-busting~~ — htmx-era concern; Vite handles fingerprinting natively.
- ~~Local model support (Ollama, Qwen3-32B etc.) for offline deployment~~ — shipped 2026-05-15 via `OllamaProvider`.
- ~~React frontend migration if interaction complexity grows~~ — shipped 2026-05-18; the whole frontend is React 19 + Vite.
- ~~Prompt-cache hit-rate observability~~ — superseded by 2026-05-19 context-management layer (phase-boundary compaction + `moves.jsonl` index + `query_history` meta-tool). Per-turn prompt size is now structurally bounded; Anthropic prompt caching is a marginal optimization rather than a primary cost lever.

---

## Risk Notes

- **DeepSeek V4 capability uncertain at our workload**. If classification accuracy on key decisions (X-preset / physics route / PBR mapping) falls below ~80%, fall back to Sonnet 4.6. Tracked by the P1 A/B eval.
- **MMD model quality varies**. A4 retains VRC fallback. If MMD-first acceptance regresses, swap demo paths to a single VRC standard model.
- **Toolkit auto-fix coverage assumed strong** (per user). If real-world usage shows toolkit failures more common than expected, the B7 error handler needs a thicker fallback path.
- **No test asset in repo** (D15). First-time user friction depends on `docs/user/demo_setup.md` quality.
- **uv still 0.x** (C13). On breakage, fall back to `pip + venv`; pyproject.toml standardization preserves portability.
