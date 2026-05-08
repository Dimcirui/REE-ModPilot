# CLAUDE.md

Working notes specific to Claude Code / Claude API agents in this repo.

For project overview see [README.md](README.md).
For agent baseline rules (commands, hard rules, code style, conventions) see [AGENTS.md](AGENTS.md).

---

## Read These First (in order)

1. [README.md](README.md) — Project at a glance, status, architecture, tech stack.
2. [AGENTS.md](AGENTS.md) — Hard rules + commands + style + workflow.
3. [docs/design.md](docs/design.md) — All 15 design decisions with rationale (A/B/C/D layers, all 🟢).
4. [docs/backlog.md](docs/backlog.md) — Current implementation tasks (P0-P3 with status badges).
5. [docs/plan.md](docs/plan.md) — The 7-video mod-making workflow being automated.
6. [docs/plugin_api.md](docs/plugin_api.md) — Modding-Toolkit operator reference (the "API" being wrapped).

---

## Current Stage

**Stage Setup** — design phase complete; project structure & docs being scaffolded.

All 15 design items in [docs/design.md](docs/design.md) are 🟢. Code implementation has not started; first P0 tasks are in [docs/backlog.md](docs/backlog.md) under "Stage Setup" and "Stage 1".

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

## Project-Specific Quirks

- **Not a git repo yet**. Plan: `git init` after Stage Setup completes (after this file, AGENTS.md, README.md, and `docs/backlog.md` are stable).
- **User platform**: Blender 4.3.2 on Windows 11. PowerShell available; Bash also via tool.
- **User language**: 中文 (mixed CN/EN OK; user has no English barrier — lean toward English in code/docs).
- **Default LLM**: DeepSeek V4 (user-provided API key). I (Claude) don't have V4 specifics in training data — implementation will need user to confirm exact `model` string and `base_url`. DeepSeek's API has historically been OpenAI-compatible, V4 likely the same.
- **Oracle/fallback LLM**: Claude Sonnet 4.6 (when DeepSeek output is suspect or for demo).
