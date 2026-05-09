# REE-ModPilot

> AI-guided Blender automation for crafting RE Engine character mods.
>
> 面向 RE Engine 游戏（MHWs / MHWI / RE4 / RE9）的 Mod 制作 AI Agent 原型。

---

## Status

| Phase | State |
|-------|-------|
| Stage 0 — connectivity verification | 🟢 done (`verify_blender_mcp.py` 5/5 passing) |
| Stage Setup — project structure & docs | 🟢 done (initial commit pushed to GitHub) |
| Stage 1 — communication backbone | 🟡 in progress |
| Stage 2+ — implementation | ⚪ pending; see [docs/backlog.md](docs/backlog.md) |

All 15 design items in [docs/design.md](docs/design.md) (A/B/C/D layers) are 🟢 decided as of 2026-05-08.

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
Browser (localhost)
   ↓ HTML / htmx + SSE
FastAPI backend
   ├── Agent loop          (ReAct, hand-rolled, ~300 lines)
   ├── Phase tools         (12-15, mapped 1:1 to plan.md sections)
   ├── LLM client          (provider-agnostic: Anthropic SDK / OpenAI-compatible)
   └── Blender client      (TCP socket)
            ↓
blender-mcp addon.py       (port 9876 — `execute_code` channel)
            ↓
User's Modding-Toolkit     (bpy.ops.modder.* / mhws.* / re4.* / etc.)
```

**Key architectural decisions**:

- **Phase tool middle layer**, not raw operator wrappers. LLM orchestrates *between* phases and makes *classification* decisions inside phases; deterministic Python orchestrates *within* phases. (B6, [project memory](.claude/projects/.../memory/project_python_over_llm.md))
- **Provider-agnostic LLM client** (~100 lines). Default DeepSeek V4 for development; Claude Sonnet 4.6 / Haiku 4.5 as oracle / demo fallback. (C10)
- **No RAG in MVP** — `plan.md` (~12K tokens) goes directly into system prompt + prompt cache. Content RAG retained as a future upgrade path. (C11)
- **htmx + Jinja2** frontend, no SPA framework. (C12)

---

## Tech Stack

| Layer | Choice | Decided in |
|-------|--------|-----------|
| Runtime | Python 3.11+ | — |
| Web framework | FastAPI | C9 |
| Package manager | uv | C13 |
| LLM (dev default) | DeepSeek V4 (OpenAI-compatible API) | C10 |
| LLM (oracle / fallback) | Claude Sonnet 4.6 / Haiku 4.5 (Anthropic SDK) | C10 |
| Agent framework | Hand-rolled ReAct (raw SDKs, no LangChain in MVP) | C9 |
| Frontend | htmx + Jinja2 templates | C12 |
| Blender integration | TCP socket via blender-mcp addon | Stage 0 |
| Tests | pytest (`unit` / `integration` markers) | D14 |
| Lint / format | Ruff | D14 |

---

## Project Structure

```
REE-ModPilot/
├── ModPilot/                     # Backend application (TBD — Stage Setup output)
│   ├── pyproject.toml            # uv-managed
│   ├── uv.lock
│   ├── .env.example
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── blender/              # Socket client + scene-state cache
│   │   ├── llm/                  # Provider-agnostic LLMClient
│   │   ├── agent/                # ReAct loop, prompts, error handler
│   │   ├── phases/               # 12-15 phase tools (one per plan.md section)
│   │   ├── routes/
│   │   └── templates/
│   ├── tests/
│   │   ├── unit/                 # mock Blender (fake socket server)
│   │   └── integration/          # real Blender, marker-gated
│   └── static/
├── docs/
│   ├── design.md                 # All 15 design decisions (🟢 complete)
│   ├── backlog.md                # P0-P3 implementation tasks
│   ├── plan.md                   # 7-video mod-making pipeline (the workflow being automated)
│   ├── plugin_api.md             # Modding-Toolkit operator reference
│   ├── blender-mcp-analysis.md   # Wire-protocol notes
│   └── demo_setup.md             # MMD model + game asset setup (TBD)
├── verify_blender_mcp.py         # Stage 0 verification script (5 checks)
├── verify_mvp.py                 # Stage MVP end-to-end check (TBD)
├── README.md                     # ← you are here
├── CLAUDE.md                     # Claude-specific working notes
└── AGENTS.md                     # General agent / contributor baseline
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

> ⚠️ Implementation has not started yet. This section grows as Stage 1+ progresses.
> Live status: [docs/backlog.md](docs/backlog.md).

For now you can verify the Blender ↔ socket pipeline:

```bash
# In Blender: enable both addons, open the BlenderMCP side panel,
# click "Connect to Claude" so port 9876 starts listening.
python verify_blender_mcp.py
```

Expected output ends with `=== Stage 0 PASSED. Pipeline is alive. ===`.

---

## References

| Doc | Purpose |
|-----|---------|
| [docs/design.md](docs/design.md) | A/B/C/D-layer design decisions log (rationale, alternatives considered, escape hatches) |
| [docs/backlog.md](docs/backlog.md) | P0-P3 implementation backlog with status badges |
| [docs/plan.md](docs/plan.md) | The 7-video mod-making workflow this project automates |
| [docs/plugin_api.md](docs/plugin_api.md) | Modding-Toolkit operator API reference |
| [CLAUDE.md](CLAUDE.md) | Claude-specific working notes (footguns, memory map) |
| [AGENTS.md](AGENTS.md) | General agent / contributor baseline (commands, hard rules, conventions) |
