# REE-ModPilot

> AI-guided Blender automation for crafting RE Engine character mods.
>
> 面向 RE Engine 游戏（MHWs / MHWI / RE4 / RE9）的 Mod 制作 AI Agent 原型。

---

## Status

| Phase | State |
|-------|-------|
| Stage 0 — connectivity verification | 🟢 done (`verify_blender_mcp.py` 5/5 passing) |
| Stage Setup — project structure & docs | 🟢 done |
| Stage 1 — communication backbone | 🟢 done (BlenderClient + SceneCache + LLMClient; 30 unit tests) |
| Stage 2 — phase tool layer (videos 1-3) | 🟢 done (PoseCorrection + SkeletonAlign + VertexGroups; 76 unit tests) |
| Stage 3 — agent loop | 🟢 done (ReAct loop + prompts + error handler + `/agent/chat`; 117 unit tests) |
| Stage 4 — phase tools (videos 4-7) | 🟢 done: physics_bones + material + batch_export + mesh_cleanup + query tools; E2E verified (Phase 1→6 full run); advanced out of MVP scope |
| Stage 5 — frontend (React 19 + TS + Vite + motion) | 🟢 done. Rebuilt from htmx (2026-05-18, C25). Same SSE/widget/config surfaces; ports: dev `:5173` proxied → backend `:8000`. Tauri v2 desktop shell layered on top (optional) provides native drag-and-drop file/dir paths through `PathField`. Stage-driven UI under `src/stages/` — `StageRouter` cross-fades a per-phase component (`Phase1Stage`, `Phase23Stage` shared by phase_2+3, `Phase4Stage` shared by phase_35+4a+4b, `Phase5Stage`, `Phase6Stage`, `DoneStage`), chat moved to a collapsible bottom `ChatStrip`. |
| Stage MVP — verification | 🟢 done (L3 in-game acceptance: 3-4 MMD/VRC models verified; `verify_mvp.py` script + `docs/demo_setup.md` walkthrough) |
| Post-MVP polish | ongoing — #13 arm-bone scale, #14 interrupt, #15 phase transition pause, #16 Phase 5A small-loop architecture, frontend React/Tauri rebuild, single-pick Body part radio (default `Body`), "Mod Output" rename. 2026-05-19: `setup_import_source` FBX phase tool + LLM provider/model guardrail. **476 unit tests, 70+ Playwright checks.** |

All design items in [docs/design.md](docs/design.md) (A/B/C/D/E layers) are 🟢 decided.

---

## Vision

For Blender-literate users who want to make RE Engine character mods but lack RE Engine modding experience: ModPilot is a **step-by-step AI guide + Blender automation layer** that compresses the mod-making pipeline (`docs/plan.md` videos 1-7) from hours to ≤ 1 hour.

**MVP scope** (locked in design.md A4):

| Dimension | Decision |
|-----------|----------|
| Target game | **MHWs** (single-game deep integration) |
| Source models | User-provided; **MMD-preferred / VRC-secondary** |
| Pipeline range | plan.md videos 1-7 (full pipeline) |
| Acceptance | **L3** — exported mod actually runs in-game |
| Time target | 30 min marketing anchor / 1 hr engineering target |

The AI's value concentrates in **videos 4-7** (physics bones / materials / batch export / advanced) where experience-class classification decisions matter (e.g. physics-vs-body bone naming, PBR channel mapping, equipment slot routing). Videos 1-3 are mostly mechanical button-pushes.

---

## Architecture

```
Browser (Vite dev server :5173) ────────────╮
   or                                       │ Same React 19 + TS bundle
Tauri desktop shell (modpilot.exe) ─────────┤  (browser-mode falls back to
                                            │   text path inputs; desktop
                                            │   gets native drag-and-drop)
                                            ↓
                          React SPA (motion + structured JSON SSE)
                                            │
                          FastAPI backend (CORS / Vite proxy)
                            ├── Agent loop          (ReAct, hand-rolled, ~600 lines)
                            ├── Phase tools         (16 tools across setup + phases 1-6)
                            ├── LLM client          (provider-agnostic: Anthropic / OpenAI-compatible / Ollama)
                            └── Blender client      (TCP socket, thread-safe RLock)
                                            ↓
                          blender-mcp addon.py       (port 9876 — `execute_code` channel)
                                            ↓
                          User's Modding-Toolkit     (bpy.ops.modder.* / mhws.* / re4.* / etc.)
```

**Key architectural decisions**:

- **Phase tool middle layer**, not raw operator wrappers. LLM orchestrates *between* phases and makes *classification* decisions inside phases; deterministic Python orchestrates *within* phases. (B6, [project memory](.claude/projects/.../memory/project_python_over_llm.md))
- **Provider-agnostic LLM client** (~150 lines). Default DeepSeek V4 for development; Claude Sonnet 4.6 / Haiku 4.5 as oracle fallback; Ollama Cloud (`deepseek-v4-flash`) as a third option. Runtime-switchable via `/config` UI — no restart needed. (C10)
- **No RAG in MVP** — `docs/agent_workflow.md` (machine-readable execution manual) goes directly into system prompt + prompt cache. `plan.md` is the video script for humans only. Content RAG retained as a future upgrade path. (C11)
- **React 19 + TypeScript + Vite** frontend, with `motion` for transitions; same SSE event surface as the original htmx build. **Tauri v2** ships an optional Rust desktop shell so the path inputs can accept native file/directory drag-and-drop (impossible inside a browser sandbox). (C25 — supersedes C12)
- **Confirmation widgets** — server-rendered Jinja partials pushed over SSE for Phase 4A (physics bone classification) and Phase 5 (material slot → texture mapping). User edits the widget in-browser; confirmed values re-enter the agent loop as prefixed JSON messages.

---

## Tech Stack

| Layer | Choice | Decided in |
|-------|--------|-----------|
| Runtime | Python 3.11+ | — |
| Web framework | FastAPI | C9 |
| Package manager | uv | C13 |
| LLM (dev default) | DeepSeek V4 (OpenAI-compatible API) | C10 |
| LLM (oracle / fallback) | Claude Sonnet 4.6 / Haiku 4.5 (Anthropic SDK) | C10 |
| LLM (third option) | Ollama Cloud (`deepseek-v4-flash` / `deepseek-v4-pro`) | C10 |
| Agent framework | Hand-rolled ReAct (raw SDKs, no LangChain in MVP) | C9 |
| Frontend | React 19 + TypeScript + Vite + motion (SPA) | C25 (supersedes ~~C12~~) |
| Desktop shell (optional) | Tauri v2 (Rust + WebView2) — enables native drag-and-drop file pickers | C25 |
| Blender integration | TCP socket via blender-mcp addon | Stage 0 |
| Tests | pytest (`unit` / `integration` markers) | D14 |
| Lint / format | Ruff | D14 |

---

## Project Structure

```
REE-ModPilot/
├── ModPilot/                     # Backend application
│   ├── pyproject.toml            # uv-managed
│   ├── uv.lock
│   ├── .env.example
│   ├── app/
│   │   ├── main.py               # FastAPI app; /health /scene_info /agent/chat
│   │   │                         #   GET / + /agent/messages + /agent/stream/{sid}
│   │   │                         #   /agent/widget/{classification,material}
│   │   │                         #   /app/config GET+POST + /config UI
│   │   │                         #   /viewport_screenshot + /app/x_presets
│   │   ├── config.py             # Settings (LLM / Blender / vision model); runtime-mutable
│   │   ├── blender/              # BlenderClient (thread-safe RLock, BlenderBusyError) + SceneCache
│   │   ├── llm/                  # Provider-agnostic LLMClient (Anthropic + OpenAI + Ollama)
│   │   ├── agent/                # ReAct loop, prompt builders, error handler
│   │   └── phases/               # Phase tools: setup (validate+infer+import), pose_correction,
│   │                             #   skeleton_align, vertex_groups, physics_bones, material,
│   │                             #   batch_export, query_tools; advanced out of MVP scope
│   ├── frontend/                 # React 19 + TypeScript + Vite + motion (replaces former
│   │   │                         #   templates/ + static/ + htmx setup)
│   │   ├── src/
│   │   │   ├── pages/            #   ChatPage (orchestrator: Shell + StageRouter + ChatStrip) + ConfigPage
│   │   │   ├── stages/           #   StageRouter + per-phase stages (Phase1Stage, Phase23Stage,
│   │   │   │                     #     Phase4Stage, Phase5Stage, Phase6Stage, DoneStage) +
│   │   │   │                     #     FallbackStage for unmigrated phases; STAGE_REGISTRY
│   │   │   ├── components/       #   Shell, ChatStrip, SessionConfigForm, PathField (drag-drop),
│   │   │   │                     #     ChatLog, PhaseStepper, ViewportPane, widgets, ErrorChoice, …
│   │   │   ├── hooks/            #   useChatState (incl. ToolRun[] tracking), useSSE
│   │   │   ├── lib/              #   api, session, desktop (Tauri bridge w/ browser fallback)
│   │   │   └── types/            #   api, sse, domain
│   │   ├── src-tauri/            #   Rust shell (Tauri v2; dialog plugin; window center fix)
│   │   ├── vite.config.ts        #   /agent /app /viewport_screenshot /health proxied to :8000
│   │   └── package.json          #   pnpm; scripts: dev, build, tauri:dev, tauri:build
│   ├── artifacts/                # Generated; gitignored. Includes ui_walkthroughs/<stamp>/walkthrough.webm
│   └── tests/
│       ├── unit/                 # mock Blender + mock LLM
│       ├── integration/          # real Blender required; marker-gated
│       └── e2e/                  # Playwright browser smokes; opt-in install
├── docs/
│   ├── design.md                 # A/B/C/D/E-layer design decisions (🟢 all decided)
│   ├── backlog.md                # P0-P3 implementation tasks with status badges
│   ├── agent_workflow.md         # Machine-readable execution manual for the agent
│   ├── plan.md                   # 7-video workflow script (human reference only)
│   ├── plugin_api.md             # Modding-Toolkit operator reference
│   └── demo_setup.md             # Blender addon install, MMD model setup, mod folder layout,
│                                 #   verify_mvp_config.json field reference, L3 acceptance procedure
├── verify_blender_mcp.py         # Stage 0 verification (5 checks)
├── verify_mvp.py                 # MVP end-to-end script (bypasses agent loop; drives phase tools
│                                 #   directly via config JSON; exit-code-correlated; --report flag)
├── README.md                     # ← you are here
├── CLAUDE.md                     # Claude-specific working notes
└── AGENTS.md                     # Contributor baseline (commands, conventions)
```

---

## Prerequisites

- Blender 4.3.2 (other 4.x may work, untested)
- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- An LLM API key — DeepSeek V4 recommended for development; Anthropic key as oracle / fallback

### Required Blender addons (install separately — not vendored)

Install these into your Blender add-on directory before using ModPilot:

| Addon | Source | Role |
|-------|--------|------|
| **Modding-Toolkit** | [Dimcirui/Modding-Toolkit](https://github.com/Dimcirui/Modding-Toolkit) | Provides the `bpy.ops.modder.* / mhws.* / re4.* / re9.* / mhwi.*` operators ModPilot orchestrates |
| **blender-mcp** | [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) | TCP socket bridge on `localhost:9876` (we only use its `addon.py`, not the FastMCP server) |

After enabling both in Blender → Edit → Preferences → Add-ons, open the BlenderMCP side panel (N-key in 3D viewport) and click **Connect to Claude** — the socket server starts on port 9876.

---

## Quick Start

**1. Verify Blender connectivity (Stage 0)**

```bash
# In Blender: enable both addons, open the BlenderMCP side panel,
# click "Connect to Claude" so port 9876 starts listening.
python verify_blender_mcp.py
```

Expected output ends with `=== Stage 0 PASSED. Pipeline is alive. ===`.

**2. Run the backend**

```bash
cd ModPilot
uv run uvicorn app.main:app --reload   # binds 127.0.0.1:8000
```

**3. Run the frontend (pick one)**

```bash
# (a) Browser mode — fastest dev loop, text-only path inputs
cd ModPilot/frontend
pnpm install                 # one-time
pnpm dev                     # http://localhost:5173, proxies /agent /app /viewport_screenshot /health → :8000

# (b) Tauri desktop mode — native drag-and-drop file/dir paths via PathField
cd ModPilot/frontend
pnpm tauri:dev               # boots Vite, then spawns the Rust shell pointing at it
```

LLM config: first launch (no API key) redirects to `/config`. Enter provider / key / model, or seed via `ModPilot/.env` (see `.env.example` — `LLM_PROVIDER=ollama`, `LLM_API_KEY=…`, `LLM_MODEL=deepseek-v4-flash` for Ollama Cloud). Settings layer in `~/.modpilot/config.json`; `.env` is git-ignored.

**4. Start a mod session**

Fill in the session-config form (source file path, mod root, character name, body part radio, hunter type, armor set, etc.) and click **Start**. The agent walks through Phases 1-6 automatically, pausing at classification checkpoints (physics bone table, material texture mapping) for user confirmation via in-browser widgets.

**5. Run unit tests**

```bash
uv run pytest -m unit -v   # 476+ tests, no Blender required
```

**6. (Optional) Headless MVP verification**

```bash
# Copy and fill in the config template, then:
python verify_mvp.py --config verify_mvp_config.json [--phases setup phase_1_2_3 ...] [--report out.json]
```

See `docs/demo_setup.md` for prerequisite addon install order, MMD model recommendations, mod folder layout, and the full config field reference.

---

## Building the desktop installer

The Tauri shell can ship as a single-click installer that bundles the FastAPI backend as a pyinstaller-frozen sidecar — end users get one `.msi` / `.exe`, no Python install needed.

```bash
# 1. Freeze the backend (run from ModPilot/)
.venv/Scripts/pyinstaller.exe modpilot_backend.spec --clean --noconfirm

# 2. Stage the dist into the Tauri binaries dir
rm -rf frontend/src-tauri/binaries/backend
cp -r dist/modpilot-backend frontend/src-tauri/binaries/backend

# 3. Build the installer (PowerShell, with cargo on PATH)
cd frontend
pnpm tauri build           # produces target/release/bundle/{msi,nsis}/
```

Output: `~25 MB` MSI and NSIS installers. On launch the bundled `modpilot.exe` spawns the sidecar on `:8000`, displays a splash until `/health` returns, then mounts the React UI. On exit (window-close OR hard-kill via `taskkill /F`), the sidecar dies with the parent — the Windows Job Object binding in `src-tauri/src/lib.rs` ensures no orphans.

### Code signing

Without a code-signing cert, Windows SmartScreen warns on first launch ("unrecognized publisher"). End users see a blue dialog and must click "More info → Run anyway".

**Pipeline is ready** — just supply a cert:

```powershell
# Set the cert thumbprint (SHA1 of cert in Cert:\CurrentUser\My)
$env:TAURI_SIGNING_CERT_THUMBPRINT = "abc123..."

# Tauri's bundle pipeline signs modpilot.exe + the installers automatically
# (digestAlgorithm + timestampUrl are already in tauri.conf.json).
pnpm tauri build

# Tauri does NOT sign the bundled sidecar exe — run our wrapper to sign
# everything (parent + sidecar + installers).
.\src-tauri\scripts\sign_bundle.ps1
```

**For local / internal-distribution testing** (signs cleanly on machines that trust your cert, still SmartScreen-flagged elsewhere):

```powershell
# Generates a self-signed cert, installs it in your cert stores, prints thumbprint
.\src-tauri\scripts\generate_dev_cert.ps1
```

**For production** (no SmartScreen warning): purchase an **EV code-signing cert** from a CA (Sectigo, DigiCert, GlobalSign — $200-500/yr). Standard OV certs require a 7-day reputation warmup period; EV certs skip it. Both work with the pipeline above; EV is dispensed on a USB token, so the build machine needs access to the token at sign time.

---

## References

| Doc | Purpose |
|-----|---------|
| [docs/design.md](docs/design.md) | A/B/C/D-layer design decisions log (rationale, alternatives considered, escape hatches) |
| [docs/backlog.md](docs/backlog.md) | P0-P3 implementation backlog with status badges |
| [docs/agent_workflow.md](docs/agent_workflow.md) | Machine-readable execution manual for the agent (phases 1-6, protocols, operator index) |
| [docs/plan.md](docs/plan.md) | 7-video mod-making workflow script (human reference; not injected into agent) |
| [docs/plugin_api.md](docs/plugin_api.md) | Modding-Toolkit operator API reference |
| [CLAUDE.md](CLAUDE.md) | Claude-specific working notes (footguns, memory map) |
| [AGENTS.md](AGENTS.md) | General agent / contributor baseline (commands, hard rules, conventions) |
