# AGENTS.md

Baseline guide for AI agents and contributors working on REE-ModPilot.

For project overview see [README.md](README.md).
For design rationale see [docs/design.md](docs/design.md).
For Claude-specific working notes see [CLAUDE.md](CLAUDE.md).

---

## Project at a Glance

- **What**: Step-by-step AI guide + Blender automation for RE Engine character mod creation.
- **Stack**: Python 3.11+, FastAPI, uv, htmx, hand-rolled ReAct over Anthropic / OpenAI SDKs.
- **Key constraint**: LLM only makes classification decisions inside phase tools and orchestrates *between* phases; deterministic Python orchestrates *within* phases.
- **MVP scope**: MHWs single-game, plan.md videos 1-7, user-provided MMD/VRC source models, L3 (in-game) acceptance.

---

## Common Commands

All commands run from the `ModPilot/` directory unless noted.

```bash
# One-time dev environment
uv venv
uv sync                                              # install from uv.lock

# Add / update deps
uv add fastapi anthropic openai pydantic-settings
uv add --dev ruff pytest pytest-asyncio

# Run dev server
uv run uvicorn app.main:app --reload

# Lint + format (Ruff)
uv run ruff check app tests
uv run ruff check app tests --fix
uv run ruff format app tests

# Tests
uv run pytest                                        # unit tests (mock Blender)
uv run pytest -m integration                         # integration tests (Blender on 9876)
uv run pytest tests/unit/test_blender_client.py -v   # single file
uv run pytest -k "test_recv_response"                # match by name

# Stage scripts (top-level, not inside pytest)
uv run python ../verify_blender_mcp.py
uv run python ../verify_mvp.py
```

---

## Hard Rules (non-negotiable)

These are sourced from [docs/design.md](docs/design.md) decisions. Do not violate without amending design.md first.

1. **LLM autonomy is bounded**. LLM makes classification decisions inside phase tools and orchestrates which phase to invoke at the top level — never manage operator-level calls. (B6)
2. **No `execute_code` escape hatch in MVP**. Don't expose `BlenderConnection.execute_code(...)` as a tool the LLM can call directly. The implementation stays available for debug. (B6)
3. **State cache is single-source**. All phase tools read/write the cache in `app/blender/state.py`. No local state in phase tools. (B5)
4. **Errors are structured**. Phase tools return `Result<state_diff, structured_error>`. Never let raw exceptions propagate to the LLM. (B7)
5. **Provider abstraction is mandatory**. Business code calls `LLMClient.chat(...)`. Never `import anthropic` or `import openai` outside `app/llm/`. (C10)
6. **No automatic rollback / `.blend` snapshots**. Rely on Blender's native undo. (B7)
7. **Secrets via environment variables only**. API keys in `.env` (gitignored), never hardcoded.
8. **Don't modify existing tests** unless the task explicitly is about them. Add new tests instead.

---

## Code Style

### Python

- **Type hints required** on all function signatures (parameters + return).
- **Imports**: Ruff enforces isort order (stdlib → third-party → first-party `app.*`).
- **No `__init__.py` re-exports** — use full import paths.
- **Datetime**: always timezone-aware (`datetime.now(timezone.utc)`); never `datetime.utcnow()`.
- **Async**: FastAPI routes are `async def`. Long-running parsing runs in `asyncio.to_thread()`.
- **Logging**: use `logging` module, not `print` (except in stage scripts where stdout *is* the output).
- **Pydantic**: separate `BaseModel` schemas for HTTP request/response, distinct from internal data classes.

### Naming

| Category | Style | Example |
|----------|-------|---------|
| Python files | `snake_case` | `pose_correction.py` |
| Variables / functions | `snake_case` | `align_skeleton()` |
| Classes | `PascalCase` | `BlenderConnection`, `LLMClient` |
| Constants | `UPPER_SNAKE_CASE` | `BLENDER_HOST`, `DEFAULT_PROVIDER` |
| HTML / CSS files | `kebab-case` | `phase-card.html` |
| Phase modules | `snake_case`, plan.md aligned | `physics_bones.py` (video 4) |

### Comments

- **English only** in code.
- Default to no comments. Add only when the *why* is non-obvious (hidden constraint, non-obvious invariant, workaround).
- No comments explaining *what* — naming should do that.

---

## Workflow Constraints

1. **Plan first, code second**. For any non-trivial change, raise an item in [docs/backlog.md](docs/backlog.md) (or update existing) before coding.
2. **Update design.md before deviating from a decision**. If implementation reveals a design decision was wrong, amend the relevant section first, then change the code.
3. **Update backlog.md status** when starting (`⚪ → 🟡`) or completing (`🟡 → 🟢`) a task.
4. **Run `ruff check` + `pytest` before committing**. Don't commit broken code.
5. **Commit messages**: Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`).
6. **Branch names**: `<type>/<scope>` — e.g., `feat/blender-client`, `refactor/phase-base`, `docs/backlog`.
7. **PR titles**: same Conventional Commits format as commits.

---

## Communication Conventions

- **Reply language with user**: 中文（讨论中默认中文，混合英文术语 OK）。
- **Code comments / commit messages / PR titles / GitHub issue text**: English.
- **Be concise**. Expand only when necessary.
- **Ask when uncertain**. Don't guess.
- **Critique provided code** when reviewing — point out issues directly.
- **Status badges** are universal across docs.

---

## Status Badge Convention

Used in [README.md](README.md) (project status), [docs/design.md](docs/design.md) (decisions), [docs/backlog.md](docs/backlog.md) (tasks).

| Symbol | Meaning |
|--------|---------|
| 🟢 | Done / decided / passing |
| 🟡 | In progress / under discussion / partial |
| ⚪ | Not started / pending |
| 🔴 | Blocked / failing / decision required |

Choosing this 4-state palette over Recall's 3-state ✅/🔶/❌ deliberately — the **🔴 blocked** state has distinct semantics worth surfacing.
