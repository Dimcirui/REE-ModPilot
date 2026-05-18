# CLAUDE.md

**Always read [lesson.md](lesson.md) at the start of every conversation in this project** — it is the orientation primer (layout, current state, run commands, hard rules) and the fix log of past mistakes worth avoiding. Read it before anything else.

Working notes specific to Claude Code / Claude API agents in this repo.

For project overview see [README.md](README.md).
For agent baseline rules (commands, hard rules, code style, conventions) see [AGENTS.md](AGENTS.md).

---

## Read These First (in order)

1. [lesson.md](lesson.md) — Orientation primer + fix log. Hard requirement: read first, every session.
2. [README.md](README.md) — Project at a glance, status, architecture, tech stack.
3. [AGENTS.md](AGENTS.md) — Hard rules + commands + style + workflow.
4. [docs/design.md](docs/design.md) — All 15 design decisions with rationale (A/B/C/D layers, all 🟢).
5. [docs/backlog.md](docs/backlog.md) — Current implementation tasks (P0-P3 with status badges).
6. [docs/plan.md](docs/plan.md) — The 7-video mod-making workflow being automated.
7. [docs/plugin_api.md](docs/plugin_api.md) — Modding-Toolkit operator reference (the "API" being wrapped).

---

## Current Stage

**MVP shipped.** All P0 work in [docs/backlog.md](docs/backlog.md) is 🟢: Stages 0 / Setup / 1 / 2 / 3 / 4 / 5 / MVP-verification.
Backend (FastAPI + hand-rolled ReAct + 16 phase tools), frontend (React 19 + TypeScript + Vite + motion SPA under `ModPilot/frontend/`, with optional Tauri v2 desktop shell; same SSE + widget + viewport surfaces, now driven by per-phase stages — see C25), LLM provider abstraction (Anthropic + OpenAI-compatible + Ollama), and `verify_mvp.py` headless harness all live.
Live with **526+ unit tests** and 70+ Playwright e2e checks. L3 acceptance achieved against MMD/VRC source models.

Current work is post-MVP polish — backlog item priorities P1 → P3.
Recently shipped: #10 (config hoist) → #11 (material widget pre-fill) → #13 (arm-bone scale) → #14 (interrupt) → #15 (phase transition pause) → #16 (Phase 5A small loop) → 2026-05-19: `setup_import_source` FBX tool + LLM provider/model guardrail; later same day, context-management layer (`app/agent/history.py` — off-prompt move log + phase-boundary compaction + `query_history` meta-tool + cold-start session recovery; FE session_id persisted in `localStorage`).

---

## blender-mcp Wire Protocol (footgun-prone — read carefully)

Read directly from [blender-mcp/addon.py](blender-mcp/addon.py); do **not** trust the README in `blender-mcp/` — it describes `server.py`'s MCP wrapper, not the raw socket protocol.

- **Request**: pure JSON object, **no newline / no length prefix**.

  ```json
  {"type": "execute_code", "params": {"code": "..."}}
  ```

- **Response**: pure JSON object, also no separator.

  ```json
  {"status": "success", "result": {"executed": true, "result": "<stdout>"}}
  ```

  Errors: `{"status": "error", "message": "..."}`.
- **`execute_code` returns stdout only** — `exec(code)`'s last expression is **not** auto-returned. Always `print(...)` to get values back.
- **Reading the socket**: loop `recv()` accumulating buffer, retry `json.loads` each iteration; success = full response. **Don't read by line. Don't read by fixed length.**
- **Operator stdout pollution**: Modding-Toolkit operators may `print` their own debug output, which interleaves with our `print(...)`. Use a **sentinel string** to bracket our own output and slice from there. (Pattern in [verify_blender_mcp.py](verify_blender_mcp.py): `SENTINEL = "===STAGE0_OUT==="`.)
- Built-in handlers (addon.py:206-216): `get_scene_info` / `get_object_info` / `get_viewport_screenshot` / `execute_code`.

---

## Memory Files

Persistent context for Claude (in `~/.claude/projects/.../memory/`):

| File | Type | Summary |
|------|------|---------|
| `MEMORY.md` | index | One-line pointers to each memory below |
| `feedback_planning_first.md` | feedback | Don't jump from tech-validation to implementation; use design docs |
| `feedback_discuss_alternatives.md` | feedback | Discuss unsuitable options too — user uses this project as a learning vehicle |
| `project_python_over_llm.md` | project | LLM only at phase-internal classification points; deterministic Python everywhere else |
| `recall_as_template.md` | reference | Project structure (README/CLAUDE/AGENTS/backlog) borrows from YoungZ2357/Recall |

---

## Project-Specific Quirks & Rules

- **User Platform**: Windows 11 and Blender 4.3.2.
- **Language Policy**:
  - **Communication**: Use **中文** as the primary language for reply, while keeping **technical terms in English** to maintain precision and technical purity.
  - **Deliverables**: All code comments, documentation, and technical strings must be in **pure English**.
- **Interaction Style**:
  - **Verbosity**: Maintain conciseness. Expand on details only when technically necessary or explicitly requested.
  - **No Guessing**: If requirements are ambiguous or information is missing, **ask for clarification**. Do not proceed based on assumptions.
  - **Critical Analysis**: Perform a critical review of any code provided by the user. If errors, inefficiencies, or logic flaws are found, point them out directly.
- **Workflow Constraints**:
  - **Plan-then-Code**: Follow a "Plan-then-Code" sequence. Present a conceptual or structural plan first. **Do not** generate implementation code until the plan has been explicitly confirmed by the user.
  - **Strict Scope**: Implement only the components explicitly requested. Do not proactively add unrequested features, extra utilities, or "helpful" optimizations.
