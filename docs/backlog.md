# Backlog

Priority-ranked task list. Status badges follow the [project convention](../AGENTS.md#status-badge-convention):

| Symbol | Meaning |
|--------|---------|
| 🟢 | Done |
| 🟡 | In progress |
| ⚪ | Not started |
| 🔴 | Blocked |

Tasks are grouped by priority band (P0 → P3). Within a band, ordering is suggested execution sequence; tasks are independently deliverable unless an explicit dependency is noted.

**Last updated**: 2026-05-08 — Stage Setup just kicked off; no implementation tasks completed.

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
- ⚪ Push to GitHub (user-driven: create `REE-ModPilot` repo on github.com → `git remote add origin <url>` → `git push -u origin main`)

### Stage 1 — communication backbone

- ⚪ `uv init` ModPilot/ project; pyproject.toml with FastAPI / Anthropic SDK / OpenAI SDK / pytest / ruff / pydantic-settings
- ⚪ Configure Ruff + pytest in pyproject.toml (markers: `unit` / `integration`)
- ⚪ Directory skeleton per [design.md D14](design.md#d14)
- ⚪ `.env.example` (LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, BLENDER_HOST, BLENDER_PORT)
- ⚪ `app/blender/client.py` — extract `BlenderConnection` from verify_blender_mcp.py
- ⚪ `app/blender/state.py` — Scene state cache + diff (B5)
- ⚪ `app/llm/client.py` — Provider-agnostic `LLMClient` (C10)
- ⚪ `app/llm/anthropic_provider.py` — Anthropic SDK adapter
- ⚪ `app/llm/openai_provider.py` — OpenAI-compatible adapter (DeepSeek V4 default)
- ⚪ `app/main.py` — FastAPI app + low-level endpoints (`/health`, `/scene_info`, `/exec` for debug)
- ⚪ `tests/unit/test_blender_client.py` — fake socket server fixture + protocol tests
- ⚪ `tests/unit/test_llm_client.py` — mock provider responses + tool-call shape tests

### Stage 2 — phase tool layer (videos 1-3)

- ⚪ `app/phases/base.py` — `PhaseTool` base contract (Result type, entry spot-check, exit cache update)
- ⚪ `app/phases/pose_correction.py` (video 1)
- ⚪ `app/phases/skeleton_align.py` (video 2; includes X/Y preset routing)
- ⚪ `app/phases/vertex_groups.py` (video 3)
- ⚪ Hybrid classification helper (B5/B6: high-confidence auto vs low-confidence user pickup)
- ⚪ Unit tests per phase (mock `BlenderConnection`)

### Stage 3 — agent loop

- ⚪ `app/agent/loop.py` — Hand-rolled ReAct loop (C9)
- ⚪ `app/agent/prompts.py` — System prompt + per-phase prompts (plan.md slices)
- ⚪ `app/agent/error_handler.py` — Structured error → user message (B7)
- ⚪ Lazy-explanation behavior wired (A2: error = teaching trigger)

### Stage 4 — phase tools (videos 4-7)

- ⚪ `app/phases/physics_bones.py` (video 4; A/B route classification — chain-role identification)
- ⚪ `app/phases/material.py` (video 5; PBR channel mapping)
- ⚪ `app/phases/batch_export.py` (video 6; equipment slot binding routing)
- ⚪ `app/phases/advanced.py` (video 7; MHWs-specific tools)

### Stage 5 — frontend (htmx)

- ⚪ Jinja2 base template + static `htmx.min.js`
- ⚪ Phase progress sidebar (live updates via `hx-swap`)
- ⚪ Chat UI with SSE streaming
- ⚪ Error response UI: retry / skip / help buttons (B7)
- ⚪ Blender viewport screenshot side-panel (`hx-trigger` periodic refresh)

### Stage MVP — verification

- ⚪ `verify_mvp.py` — End-to-end script (Operator FINISHED checks + file existence + non-zero size + key intermediate state checks)
- ⚪ `docs/demo_setup.md` — Specific MMD model recommendation, MHWs skeleton acquisition, REF setup hints (D15)
- ⚪ Run a full L3 acceptance pass with self-provided assets

---

## P1 — Important but not MVP-blocking

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
