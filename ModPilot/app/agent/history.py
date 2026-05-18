"""
Context-management layer for long modding sessions.

A full mod session walks 12+ phases and easily runs 30+ turns. Each turn
re-sends the entire `_global_history` to the LLM, so verbose phase detail
(tool_use/tool_result/narration) compounds into token cost, latency, and
eventually context exhaustion on smaller models.

This module provides two cooperating mechanisms:

  MoveLog
    Append-only JSON-Lines log at `~/.modpilot/sessions/{sid}/moves.jsonl`,
    storing every meaningful event (user msg, agent reply, tool call,
    phase advance, widget confirm, error choice, interrupt). The log
    lives OFF-PROMPT — the LLM never sees it unconditionally; it is the
    ground truth that survives compaction and serves as a session-recovery
    artifact across backend restarts.

  compact_phase_range
    Pure function that collapses a span of `_global_history` (the messages
    belonging to a just-completed phase) into a single assistant summary
    message marked with COMPACT_MARKER. The MoveLog still holds the full
    detail; the LLM can recover it via the `query_history` meta-tool.

  query_history meta-tool
    Schema-only here; the executor lives in `AgentLoop._execute_tool_call`
    (alongside the `sync_phase_state` meta-tool). Lets the LLM page through
    its own past on demand instead of being forced to re-process all of it
    every turn.

Design philosophy: ModPilot's existing rule is "the Blender scene IS the
memory". This module extends that to: "the move log IS the chat memory" —
the prompt holds only enough to make the next decision; everything older
is one tool call away.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# ── compaction marker ────────────────────────────────────────────────────────

#: Prefix tag prepended to every synthetic summary message produced by
#: `compact_phase_range`. Used for two things:
#:   1. Idempotency: re-compacting an already-compacted span is a no-op.
#:   2. Future debug / UI surfacing: a chat log inspector can filter or
#:      style compaction summaries differently from raw model output.
#: The marker is intentionally human-readable so model output containing
#: this exact string is vanishingly unlikely to false-positive.
COMPACT_MARKER: str = "[compacted]"


# ── MoveLog ──────────────────────────────────────────────────────────────────

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _sessions_root() -> Path:
    """Resolve `~/.modpilot/sessions` at call time so tests can monkeypatch
    Path.home() before any IO happens (mirror of config_store._config_path).
    """
    return Path.home() / ".modpilot" / "sessions"


class MoveLog:
    """Append-only per-session log of agent "moves".

    File format: JSON Lines (one JSON object per line, newline-terminated,
    UTF-8). Append is the only write operation; reads scan the whole file
    and filter in memory. Sessions are short enough (hours, not days) that
    a linear scan is cheaper than maintaining an index.

    Each move dict carries at minimum:
      - kind: "user" | "assistant" | "tool" | "phase_advance"
              | "widget" | "error_choice" | "interrupt"
      - ts:   float, unix epoch seconds (injected if omitted)
      - turn: int, 1-indexed (injected if omitted; counts only logged moves)

    Additional fields are kind-specific; see `AgentLoop`'s logging call
    sites for the canonical shapes.

    Thread/process model: single writer per session by design. The session
    id is unique per AgentLoop instance and the loop is single-threaded
    per turn (tools execute via asyncio.to_thread but log writes happen
    on the orchestrator thread). No locking required.
    """

    def __init__(self, session_id: str, base_dir: Path | None = None) -> None:
        if not _SESSION_ID_RE.match(session_id):
            raise ValueError(
                f"Invalid session_id {session_id!r}: must match [A-Za-z0-9_-]+. "
                "Path traversal attempts (e.g. '..') are rejected outright."
            )
        root = base_dir if base_dir is not None else _sessions_root()
        self._dir: Path = root / session_id
        self._path: Path = self._dir / "moves.jsonl"
        self._turn_counter: int = self._count_existing_moves()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, move: dict[str, Any]) -> None:
        """Write one move as a JSON line. Creates parent dirs lazily.

        Mutates `move` only by injecting `ts` and `turn` if absent — the
        caller's dict is otherwise preserved.
        """
        move.setdefault("ts", time.time())
        if "turn" not in move:
            self._turn_counter += 1
            move["turn"] = self._turn_counter
        else:
            # Caller-supplied turn wins; keep the counter in sync so the
            # next omitted-turn append doesn't collide.
            self._turn_counter = max(self._turn_counter, int(move["turn"]))

        self._dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(move, ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read(
        self,
        *,
        phase: str | None = None,
        kind: str | None = None,
        name: str | None = None,
        last_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return moves matching the filters, in chronological order.

        Filters are ANDed. `last_n` applies AFTER filtering so callers get
        the most-recent-N within the selected slice. Missing-file and
        malformed-line cases return / skip silently — a corrupted log
        line must not crash the agent loop.
        """
        if not self._path.is_file():
            return []
        out: list[dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    move = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(move, dict):
                    continue
                if phase is not None and move.get("phase") != phase:
                    continue
                if kind is not None and move.get("kind") != kind:
                    continue
                if name is not None and move.get("name") != name:
                    continue
                out.append(move)
        if last_n is not None and last_n > 0:
            out = out[-last_n:]
        return out

    def _count_existing_moves(self) -> int:
        """Count moves already on disk so a resumed session continues the
        turn counter monotonically. Returns 0 when the file is absent."""
        if not self._path.is_file():
            return 0
        try:
            with self._path.open("r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0


# ── compact_phase_range ──────────────────────────────────────────────────────


def compact_phase_range(
    history: list[dict[str, Any]],
    start_idx: int,
    end_idx: int,
    summary: str,
) -> list[dict[str, Any]]:
    """Collapse `history[start_idx:end_idx]` into a single assistant summary.

    Returns a NEW list; does not mutate the input. The caller swaps its
    `_global_history` reference for the returned list.

    Idempotency: if the span is already a single message whose string
    content begins with COMPACT_MARKER, the input is returned (as a copy).
    This guards against double-compaction in error/retry paths.

    Safety: the caller is responsible for ensuring the span contains only
    matched tool_use ↔ tool_result pairs. Compacting a span that orphans
    a tool_use will cause the next LLM call to 400. In practice the natural
    boundaries (phase entry → wrap-up) satisfy this — phase advance only
    fires after `_execute_tool_call` returns success, which means the
    tool_result has already been appended.
    """
    if start_idx < 0 or end_idx > len(history) or start_idx >= end_idx:
        return list(history)

    span = history[start_idx:end_idx]

    # Already compacted? bail.
    if len(span) == 1:
        content = span[0].get("content")
        if isinstance(content, str) and content.startswith(COMPACT_MARKER):
            return list(history)

    summary_msg: dict[str, Any] = {
        "role": "assistant",
        "content": f"{COMPACT_MARKER} {summary}",
    }
    return [*history[:start_idx], summary_msg, *history[end_idx:]]


# ── query_history meta-tool schema ───────────────────────────────────────────

#: Tool name registered with the LLM. Mirrors `sync_phase_state` — handled
#: in `AgentLoop._execute_tool_call` as a meta-tool that does not touch
#: Blender, since it reads from the on-disk MoveLog instead.
QUERY_HISTORY_TOOL_NAME: str = "query_history"

#: Default `last_n` applied by the meta-tool handler when the LLM omits it
#: or passes an invalid value. Small enough that a no-args `query_history()`
#: returns useful recent context without re-injecting the entire session.
QUERY_HISTORY_DEFAULT_LAST_N: int = 50

#: Hard ceiling on `last_n` enforced by the meta-tool handler. The LLM may
#: legitimately want more than the default — e.g. scanning all classification
#: decisions across a long phase — but cannot demand the whole log. A typical
#: full mod session logs ~100-300 moves end-to-end, so 1000 leaves comfortable
#: headroom for power-user filters without enabling a single tool call to
#: defeat compaction's whole purpose.
QUERY_HISTORY_MAX_LAST_N: int = 1000

QUERY_HISTORY_TOOL_SCHEMA: dict[str, Any] = {
    "name": QUERY_HISTORY_TOOL_NAME,
    "description": (
        "Retrieve detail from earlier in the session that has been compacted "
        "out of the active conversation. After a phase completes, its tool "
        "calls and intermediate messages are collapsed to a single summary "
        "line in chat history; the full record is kept off-prompt in the "
        "session move log. Call this tool to look up what tools were called, "
        "what arguments they received, or what they returned. All filters "
        "are optional — call with no arguments to see the most recent moves."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "phase": {
                "type": "string",
                "description": (
                    "Filter to moves recorded while this phase was active "
                    "(e.g. 'phase_4a'). Omit to span all phases."
                ),
            },
            "kind": {
                "type": "string",
                "description": (
                    "Filter to a single move kind: 'tool', 'user', "
                    "'assistant', 'phase_advance', 'widget', "
                    "'error_choice', 'interrupt'."
                ),
            },
            "name": {
                "type": "string",
                "description": (
                    "Filter to moves with this `name` field — for kind='tool' "
                    "this is the tool name (e.g. 'physics_classification')."
                ),
            },
            "last_n": {
                "type": "integer",
                "description": (
                    f"Return only the most recent N matches after filtering. "
                    f"When omitted (or invalid), defaults to "
                    f"{QUERY_HISTORY_DEFAULT_LAST_N}. Hard-capped at "
                    f"{QUERY_HISTORY_MAX_LAST_N} — larger values are clamped "
                    "by the server, so a no-args call cannot dump the whole "
                    "log. Filter narrowly (phase / name) when you need detail "
                    "from a specific point in the session."
                ),
            },
        },
        "required": [],
    },
}
