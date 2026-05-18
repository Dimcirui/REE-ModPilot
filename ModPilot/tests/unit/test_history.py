"""
Unit tests for `app/agent/history.py` — the context-management layer for
long modding sessions.

Two pieces under test:

  MoveLog                    — per-session JSON-lines write/read of every
                               "move" (user msg / agent msg / tool call /
                               phase advance / widget confirm / error choice
                               / interrupt). Lives off-prompt at
                               `~/.modpilot/sessions/{sid}/moves.jsonl`.
  compact_phase_range        — pure function that collapses a phase's
                               tool_use/tool_result/narration messages in
                               `_global_history` down to a single summary
                               assistant message. The on-disk MoveLog remains
                               the ground truth for what was compacted away.

Both are designed so the LLM never re-reads verbose phase detail unless it
explicitly calls the `query_history` meta-tool, whose schema lives in the
same module.

Run with: uv run pytest -m unit tests/unit/test_history.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.history import (
    COMPACT_MARKER,
    QUERY_HISTORY_TOOL_NAME,
    QUERY_HISTORY_TOOL_SCHEMA,
    MoveLog,
    compact_phase_range,
)

# ── MoveLog ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Patch Path.home() to a tmp dir so MoveLog writes don't touch the real
    user filesystem. Mirror of test_app_config.py's home-stub pattern.

    Path.home is a classmethod — monkeypatch must use `classmethod(...)`,
    NOT `lambda self: tmp_path`, or the patch silently leaks (lesson logged).
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.mark.unit
class TestMoveLogPath:
    def test_path_resolves_under_dot_modpilot(self, fake_home):
        log = MoveLog("abc123")
        assert log.path == fake_home / ".modpilot" / "sessions" / "abc123" / "moves.jsonl"

    def test_session_id_with_special_chars_does_not_escape_dir(self, fake_home):
        # Defensive: even though session_ids are 12-char hex in practice,
        # a path-traversal attempt must not write outside the sessions root.
        with pytest.raises(ValueError):
            MoveLog("../escape")


@pytest.mark.unit
class TestMoveLogAppend:
    def test_first_append_creates_parent_dirs(self, fake_home):
        log = MoveLog("sess1")
        assert not log.path.exists()
        log.append({"kind": "user", "content": "hello"})
        assert log.path.is_file()
        assert log.path.parent.is_dir()

    def test_each_append_writes_one_jsonl_line(self, fake_home):
        log = MoveLog("sess1")
        log.append({"kind": "user", "content": "first"})
        log.append({"kind": "assistant", "content": "second"})
        lines = log.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["content"] == "first"
        assert json.loads(lines[1])["content"] == "second"

    def test_append_injects_ts_and_turn_when_omitted(self, fake_home):
        log = MoveLog("sess1")
        log.append({"kind": "user", "content": "hi"})
        log.append({"kind": "assistant", "content": "yo"})
        moves = log.read()
        assert all("ts" in m for m in moves)
        assert moves[0]["turn"] == 1
        assert moves[1]["turn"] == 2

    def test_explicit_turn_is_preserved(self, fake_home):
        log = MoveLog("sess1")
        log.append({"kind": "user", "content": "hi", "turn": 7})
        assert log.read()[0]["turn"] == 7

    def test_unicode_content_round_trips(self, fake_home):
        """Chinese/Japanese mod names must survive jsonl round-trip without
        cp1252 mangling (lesson: Windows curl + UTF-8 logged in lesson.md)."""
        log = MoveLog("sess1")
        log.append({"kind": "user", "content": "导入模型 終末地"})
        assert log.read()[0]["content"] == "导入模型 終末地"


@pytest.mark.unit
class TestMoveLogRead:
    def _seed(self, log: MoveLog) -> None:
        log.append({"kind": "user", "phase": "setup_validate", "content": "go"})
        log.append({"kind": "tool", "phase": "setup_validate", "name": "scene_info"})
        log.append({"kind": "phase_advance", "phase": "setup_validate", "to_phase": "phase_1"})
        log.append({"kind": "tool", "phase": "phase_1", "name": "pose_correction"})
        log.append({"kind": "tool", "phase": "phase_1", "name": "scene_info"})
        log.append({"kind": "assistant", "phase": "phase_1", "content": "phase 1 done"})

    def test_read_no_filter_returns_all(self, fake_home):
        log = MoveLog("sess1")
        self._seed(log)
        assert len(log.read()) == 6

    def test_read_missing_file_returns_empty(self, fake_home):
        # Cold start — no session dir created yet. Don't crash.
        log = MoveLog("brand_new")
        assert log.read() == []

    def test_filter_by_phase(self, fake_home):
        log = MoveLog("sess1")
        self._seed(log)
        phase_1_moves = log.read(phase="phase_1")
        assert len(phase_1_moves) == 3
        assert all(m["phase"] == "phase_1" for m in phase_1_moves)

    def test_filter_by_kind(self, fake_home):
        log = MoveLog("sess1")
        self._seed(log)
        tool_moves = log.read(kind="tool")
        assert len(tool_moves) == 3
        assert all(m["kind"] == "tool" for m in tool_moves)

    def test_filter_by_name(self, fake_home):
        log = MoveLog("sess1")
        self._seed(log)
        named = log.read(name="scene_info")
        assert len(named) == 2

    def test_last_n_trims_from_end(self, fake_home):
        log = MoveLog("sess1")
        self._seed(log)
        recent = log.read(last_n=2)
        assert len(recent) == 2
        # last two seeded moves are the phase_1 scene_info call + assistant msg
        assert recent[-1]["kind"] == "assistant"

    def test_combined_filters_are_ANDed(self, fake_home):
        log = MoveLog("sess1")
        self._seed(log)
        result = log.read(phase="phase_1", kind="tool")
        assert len(result) == 2
        assert all(m["phase"] == "phase_1" and m["kind"] == "tool" for m in result)

    def test_last_n_applied_after_filters(self, fake_home):
        log = MoveLog("sess1")
        self._seed(log)
        result = log.read(phase="phase_1", last_n=1)
        assert len(result) == 1
        assert result[0]["kind"] == "assistant"

    def test_malformed_line_in_middle_is_skipped(self, fake_home):
        log = MoveLog("sess1")
        log.append({"kind": "user", "content": "ok"})
        # Manually corrupt: append a bad line, then another good one.
        with log.path.open("a", encoding="utf-8") as f:
            f.write("not valid json\n")
        log.append({"kind": "user", "content": "still ok"})
        moves = log.read()
        # Bad line dropped silently; surrounding moves preserved.
        assert len(moves) == 2
        assert moves[0]["content"] == "ok"
        assert moves[1]["content"] == "still ok"


# ── compact_phase_range ──────────────────────────────────────────────────────


def _build_phase_history() -> list[dict]:
    """Synthetic _global_history slice covering one phase boundary.

    Index 0:   user opens session
    Index 1:   assistant intro
    Index 2:   user "go"
    Index 3:   assistant tool_use(pose_correction)
    Index 4:   user tool_result for index 3
    Index 5:   assistant wrap-up text "phase 1 done"
    """
    return [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Ready."},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "running phase 1"},
            {"type": "tool_use", "id": "t1", "name": "pose_correction",
             "input": {"x_preset": "MMD"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "Phase phase_1 completed. Scene diff: ..."},
        ]},
        {"role": "assistant", "content": "Phase 1 done — pose corrected via MMD preset."},
    ]


@pytest.mark.unit
class TestCompactPhaseRange:
    def test_replaces_span_with_single_summary_message(self):
        history = _build_phase_history()
        new = compact_phase_range(history, start_idx=3, end_idx=6,
                                   summary="Phase 1 done — pose corrected.")
        # 3 messages removed, 1 summary appended in their place.
        assert len(new) == len(history) - 2
        # Pre-span messages untouched.
        assert new[:3] == history[:3]
        # Compact marker present so future calls can detect already-compacted spans.
        assert new[3]["role"] == "assistant"
        assert isinstance(new[3]["content"], str)
        assert COMPACT_MARKER in new[3]["content"]
        assert "Phase 1 done" in new[3]["content"]

    def test_empty_range_is_noop(self):
        history = _build_phase_history()
        new = compact_phase_range(history, start_idx=3, end_idx=3, summary="x")
        assert new == history

    def test_out_of_bounds_indices_return_copy_unchanged(self):
        history = _build_phase_history()
        new = compact_phase_range(history, start_idx=99, end_idx=100, summary="x")
        assert new == history
        # Must be a copy — the caller should be free to mutate.
        assert new is not history

    def test_idempotent_on_already_compacted_span(self):
        """Compacting a span that is itself a single compact-marker message is
        a no-op. Prevents double-compaction wiping the summary."""
        history = _build_phase_history()
        once = compact_phase_range(history, 3, 6, "summary one")
        twice = compact_phase_range(once, 3, 4, "summary two")
        assert twice == once

    def test_preserves_messages_after_span(self):
        """When compaction happens mid-history (e.g. multiple phases buffered
        before flush), messages AFTER the span are preserved."""
        history = _build_phase_history() + [
            {"role": "user", "content": "now phase 2"},
            {"role": "assistant", "content": "ok"},
        ]
        new = compact_phase_range(history, 3, 6, "phase 1 collapsed")
        assert new[-2:] == history[-2:]
        assert COMPACT_MARKER in new[-3]["content"]


# ── query_history tool schema ────────────────────────────────────────────────


# ── system prompt injection ──────────────────────────────────────────────────


@pytest.mark.unit
class TestContextManagementProtocolInjection:
    """The system prompt must teach the LLM that older detail is compacted but
    recoverable via `query_history`. Without this section the LLM has no
    reason to reach for the tool and will keep asking the user for things
    it has already been told."""

    def test_protocol_section_present(self):
        from app.agent.prompts import build_system_prompt
        sysp = build_system_prompt()
        assert "Context Management Protocol" in sysp

    def test_protocol_names_the_query_history_tool(self):
        from app.agent.prompts import build_system_prompt
        from app.agent.history import QUERY_HISTORY_TOOL_NAME
        sysp = build_system_prompt()
        assert QUERY_HISTORY_TOOL_NAME in sysp

    def test_protocol_mentions_the_compact_marker(self):
        """The LLM must know the visible marker so it can recognize a
        compacted span when reading its own history."""
        from app.agent.prompts import build_system_prompt
        sysp = build_system_prompt()
        assert COMPACT_MARKER in sysp


@pytest.mark.unit
class TestQueryHistoryToolSchema:
    def test_name_matches_constant(self):
        assert QUERY_HISTORY_TOOL_SCHEMA["name"] == QUERY_HISTORY_TOOL_NAME

    def test_has_description_mentioning_compaction(self):
        # The LLM should learn from the schema description that detail older
        # than the most recent phase has been compacted away and is
        # retrievable here.
        desc = QUERY_HISTORY_TOOL_SCHEMA["description"].lower()
        assert "compact" in desc or "history" in desc

    def test_all_filter_params_are_optional(self):
        schema = QUERY_HISTORY_TOOL_SCHEMA["input_schema"]
        # No required params — calling with empty input returns recent history.
        assert schema.get("required", []) == []

    def test_accepts_phase_kind_name_last_n(self):
        props = QUERY_HISTORY_TOOL_SCHEMA["input_schema"]["properties"]
        for key in ("phase", "kind", "name", "last_n"):
            assert key in props, f"missing schema property: {key}"
