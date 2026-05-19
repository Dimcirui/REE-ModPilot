"""
Unit tests for the context-management wiring inside AgentLoop:

  - session_id → MoveLog plumbing (None when not provided, real instance when provided)
  - per-move logging at step() / _execute_tool_call / interrupt()
  - phase-boundary compaction at top of _run_react_turn
  - query_history meta-tool execution

Companion to tests/unit/test_history.py (which tests the pure pieces in
`app/agent/history.py` standalone — file IO, range collapse, schema shape).

Run with: uv run pytest -m unit tests/unit/test_agent_loop_history.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.agent.history import (
    COMPACT_MARKER,
    QUERY_HISTORY_DEFAULT_LAST_N,
    QUERY_HISTORY_MAX_LAST_N,
    QUERY_HISTORY_TOOL_NAME,
    MoveLog,
)
from app.agent.loop import _PHASE_SEQUENCE, AgentLoop, LoopState
from app.phases.base import PhaseResult


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _make_llm(side_effect: list) -> MagicMock:
    llm = MagicMock()
    llm.chat.side_effect = side_effect
    return llm


def _make_blender() -> MagicMock:
    b = MagicMock()
    b.get_scene_info.return_value = {"name": "Scene", "object_count": 1}
    return b


def _tool_use_response(tool_id: str, name: str, input_: dict) -> MagicMock:
    return MagicMock(
        content="",
        has_tool_calls=True,
        tool_calls=[{"id": tool_id, "name": name, "input": input_}],
        content_blocks=[],
    )


def _text_response(text: str) -> MagicMock:
    return MagicMock(content=text, has_tool_calls=False, tool_calls=[])


# ── session_id plumbing ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestMoveLogPlumbing:
    def test_no_session_id_means_no_move_log(self):
        loop = AgentLoop(llm=MagicMock(), blender=_make_blender())
        assert loop._move_log is None

    def test_session_id_creates_move_log(self, fake_home):
        loop = AgentLoop(
            llm=MagicMock(),
            blender=_make_blender(),
            session_id="abc12345",
        )
        assert isinstance(loop._move_log, MoveLog)
        assert loop._move_log.path.name == "moves.jsonl"
        assert "abc12345" in str(loop._move_log.path)


# ── per-turn logging in step() ───────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
class TestStepLogging:
    async def test_step_logs_user_then_assistant(self, fake_home):
        llm = _make_llm([_text_response("hi back")])
        loop = AgentLoop(
            llm=llm,
            blender=_make_blender(),
            session_id="sess_a",
        )
        await loop.step("hello")
        moves = loop._move_log.read()
        kinds = [m["kind"] for m in moves]
        assert kinds == ["user", "assistant"]
        assert moves[0]["content"] == "hello"
        assert moves[1]["content"] == "hi back"

    async def test_widget_prefix_logged_as_widget_kind(self, fake_home):
        llm = _make_llm([_text_response("got it")])
        loop = AgentLoop(
            llm=llm,
            blender=_make_blender(),
            session_id="sess_w",
        )
        await loop.step('[CONFIRMED_CLASSIFICATIONS]{"chain_a": "hair"}')
        moves = loop._move_log.read()
        # First move should be widget, not user.
        assert moves[0]["kind"] == "widget"

    async def test_error_handling_state_logs_user_msg_as_error_choice(self, fake_home):
        """When the loop is in ERROR_HANDLING and the user responds (retry /
        skip / ask), the step() entry should categorize that user message as
        an error_choice move, not a plain user move."""
        llm = _make_llm([_text_response("retrying")])
        loop = AgentLoop(
            llm=llm,
            blender=_make_blender(),
            session_id="sess_ec",
        )
        loop.state = LoopState.ERROR_HANDLING
        # _handle_error_choice will route via parse_user_choice; we don't care
        # what the actual choice routing does here, just the entry-time log kind.
        with patch.object(loop._error_handler, "parse_user_choice", return_value="ask"):
            await loop.step("ask")
        kinds = [m["kind"] for m in loop._move_log.read()]
        assert "error_choice" in kinds


# ── tool-call logging ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
class TestToolCallLogging:
    async def test_phase_tool_call_logged_with_args_and_result(self, fake_home):
        llm = _make_llm([
            _tool_use_response("t1", "pose_correction", {
                "x_preset": "MMD", "source_armature": "Body", "target_armature": "MHWs",
            }),
            _text_response("phase 1 done"),
        ])
        loop = AgentLoop(
            llm=llm,
            blender=_make_blender(),
            session_id="sess_tc",
        )
        loop.state = LoopState.RUNNING_PHASE
        loop._phase_idx = _PHASE_SEQUENCE.index("phase_1")
        from app.phases.pose_correction import PoseCorrection
        with patch.object(
            PoseCorrection, "run",
            return_value=PhaseResult.ok({"scale_ratio": 1.05}),
        ):
            await loop.step("run phase 1")
        tool_moves = loop._move_log.read(kind="tool")
        assert len(tool_moves) == 1
        assert tool_moves[0]["name"] == "pose_correction"
        assert tool_moves[0]["args"]["x_preset"] == "MMD"
        assert tool_moves[0]["success"] is True
        # result_summary should reference the state_diff in some form
        assert "scale_ratio" in tool_moves[0]["result_summary"]


# ── phase advance + compaction ───────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
class TestPhaseAdvanceAndCompaction:
    async def test_phase_advance_logged_and_flag_set(self, fake_home):
        llm = _make_llm([
            _tool_use_response("t1", "pose_correction", {
                "x_preset": "MMD", "source_armature": "Body", "target_armature": "MHWs",
            }),
            _text_response("phase 1 done — pose corrected via MMD"),
        ])
        loop = AgentLoop(
            llm=llm,
            blender=_make_blender(),
            session_id="sess_pa",
        )
        loop.state = LoopState.RUNNING_PHASE
        # Start at phase_1 (index 4) so this is a clean single-advance test.
        loop._phase_idx = _PHASE_SEQUENCE.index("phase_1")
        loop._phase_start_idx_global = 0
        from app.phases.pose_correction import PoseCorrection
        with patch.object(PoseCorrection, "run", return_value=PhaseResult.ok({"k": 1})):
            await loop.step("run phase 1")
        # phase_advance move present
        pa_moves = loop._move_log.read(kind="phase_advance")
        assert len(pa_moves) == 1
        assert pa_moves[0]["phase"] == "phase_1"
        assert pa_moves[0]["to_phase"] == "phase_2"
        # Flag set so next step() triggers compaction
        assert loop._just_completed_phase == "phase_1"

    async def test_next_step_compacts_completed_phase(self, fake_home):
        """End-to-end across two turns: phase 1 runs, wrap-up returned, user
        sends second message — at top of next _run_react_turn, the previous
        phase's tool_use/tool_result/wrap-up triplet gets collapsed to one
        compacted assistant message."""
        llm = _make_llm([
            # Turn 1: tool call, then wrap-up
            _tool_use_response("t1", "pose_correction", {
                "x_preset": "MMD", "source_armature": "Body", "target_armature": "MHWs",
            }),
            _text_response("Phase 1 done — pose corrected via MMD."),
            # Turn 2: text only (no tool call, just acknowledging)
            _text_response("Ready for phase 2."),
        ])
        loop = AgentLoop(
            llm=llm,
            blender=_make_blender(),
            session_id="sess_compact",
        )
        loop.state = LoopState.RUNNING_PHASE
        loop._phase_idx = _PHASE_SEQUENCE.index("phase_1")
        loop._phase_start_idx_global = 0
        from app.phases.pose_correction import PoseCorrection
        with patch.object(PoseCorrection, "run", return_value=PhaseResult.ok({"k": 1})):
            await loop.step("run phase 1")
            # Before turn 2: history should still have full detail (4 messages:
            # user, assistant tool_use, user tool_result, assistant wrap-up).
            assert len(loop._global_history) == 4
            await loop.step("continue")
        # After turn 2: phase 1's span (indices 0..3) collapsed to a single
        # compact-marker message; user "continue" + new assistant reply follow.
        assert any(
            isinstance(m.get("content"), str) and COMPACT_MARKER in m["content"]
            for m in loop._global_history
        )
        # Wrap-up text should appear inside the compact summary.
        compact_msg = next(
            m for m in loop._global_history
            if isinstance(m.get("content"), str) and COMPACT_MARKER in m["content"]
        )
        assert "pose corrected" in compact_msg["content"].lower() or "phase 1 done" in compact_msg["content"].lower()
        # Flag was consumed.
        assert loop._just_completed_phase is None

    async def test_compaction_advances_phase_start_idx(self, fake_home):
        """After compaction, _phase_start_idx_global points to the index where
        the NEW (next) phase's messages will accumulate — i.e. just past the
        compact summary."""
        llm = _make_llm([
            _tool_use_response("t1", "pose_correction", {
                "x_preset": "MMD", "source_armature": "Body", "target_armature": "MHWs",
            }),
            _text_response("Phase 1 done."),
            _text_response("ok."),
        ])
        loop = AgentLoop(
            llm=llm, blender=_make_blender(), session_id="sess_idx",
        )
        loop.state = LoopState.RUNNING_PHASE
        loop._phase_idx = _PHASE_SEQUENCE.index("phase_1")
        loop._phase_start_idx_global = 0
        from app.phases.pose_correction import PoseCorrection
        with patch.object(PoseCorrection, "run", return_value=PhaseResult.ok({})):
            await loop.step("go")
            await loop.step("continue")
        # The compact summary occupies index 0 of the compacted history.
        # Anything after it (user "continue" + new assistant reply) belongs
        # to the next phase's span.
        assert loop._phase_start_idx_global == 1


# ── interrupt logging ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestInterruptLogging:
    def test_interrupt_call_logs_interrupt_move(self, fake_home):
        loop = AgentLoop(
            llm=MagicMock(),
            blender=_make_blender(),
            session_id="sess_int",
        )
        loop.interrupt()
        moves = loop._move_log.read(kind="interrupt")
        assert len(moves) == 1


# ── session recovery (hydrate from disk) ─────────────────────────────────────


@pytest.mark.unit
class TestSessionRecovery:
    """Recovery shape (deliberate, phase-granular):

      - Cold-start AgentLoop for a session_id whose moves.jsonl exists →
        _phase_idx = count of phase_advance moves, _global_history rebuilt
        as N [compacted] summaries (one per completed phase, using the
        wrap-up text logged after each advance).
      - Partial mid-phase work is NOT re-injected. Scene-is-memory handles it
        (next turn, the agent re-queries Blender). Past decisions are
        recoverable via `query_history`.
    """

    def _seed_log(self, sid: str, moves: list[dict]) -> None:
        """Write moves directly to a session's jsonl, bypassing MoveLog so the
        test fully controls the on-disk content (including pre-existing
        ts/turn fields)."""
        log = MoveLog(sid)
        for m in moves:
            log.append(m)

    def test_no_log_file_means_no_hydration(self, fake_home):
        loop = AgentLoop(
            llm=MagicMock(), blender=_make_blender(), session_id="brand_new",
        )
        assert loop._phase_idx == 0
        assert loop._global_history == []
        assert loop._phase_start_idx_global == 0
        assert loop._just_completed_phase is None

    def test_single_phase_advance_hydrates_one_compacted_summary(self, fake_home):
        sid = "resume_one"
        self._seed_log(sid, [
            {"kind": "user", "phase": "setup_import_source", "content": "start"},
            {"kind": "tool", "phase": "setup_import_source",
             "name": "setup_import_source", "args": {}, "result_summary": "ok",
             "success": True},
            {"kind": "phase_advance", "phase": "setup_import_source",
             "to_phase": "setup_validate"},
            # The wrap-up assistant message lands AFTER phase_idx already moved,
            # so its phase field is the NEW phase (matches live wiring).
            {"kind": "assistant", "phase": "setup_validate",
             "content": "Source FBX imported successfully — Source armature is ready."},
        ])
        loop = AgentLoop(
            llm=MagicMock(), blender=_make_blender(), session_id=sid,
        )
        assert loop._phase_idx == 1
        assert len(loop._global_history) == 1
        msg = loop._global_history[0]
        assert msg["role"] == "assistant"
        assert isinstance(msg["content"], str)
        assert COMPACT_MARKER in msg["content"]
        # Wrap-up text should appear inside the compacted summary.
        assert "Source FBX imported" in msg["content"]
        assert loop._phase_start_idx_global == 1

    def test_multiple_phase_advances_each_get_a_summary(self, fake_home):
        sid = "resume_many"
        self._seed_log(sid, [
            {"kind": "tool", "phase": "setup_import_source", "name": "x",
             "args": {}, "result_summary": "ok", "success": True},
            {"kind": "phase_advance", "phase": "setup_import_source",
             "to_phase": "setup_validate"},
            {"kind": "assistant", "phase": "setup_validate",
             "content": "Import done."},
            {"kind": "tool", "phase": "setup_validate", "name": "y",
             "args": {}, "result_summary": "ok", "success": True},
            {"kind": "phase_advance", "phase": "setup_validate",
             "to_phase": "setup_infer"},
            {"kind": "assistant", "phase": "setup_infer",
             "content": "Scene validated."},
            {"kind": "tool", "phase": "setup_infer", "name": "z",
             "args": {}, "result_summary": "MMD detected", "success": True},
            {"kind": "phase_advance", "phase": "setup_infer",
             "to_phase": "setup_import"},
            {"kind": "assistant", "phase": "setup_import",
             "content": "Model inferred as MMD."},
        ])
        loop = AgentLoop(
            llm=MagicMock(), blender=_make_blender(), session_id=sid,
        )
        assert loop._phase_idx == 3
        # One [compacted] summary per phase_advance.
        compacted = [
            m for m in loop._global_history
            if isinstance(m.get("content"), str) and COMPACT_MARKER in m["content"]
        ]
        assert len(compacted) == 3
        # Order preserved.
        assert "Import done" in compacted[0]["content"]
        assert "Scene validated" in compacted[1]["content"]
        assert "Model inferred" in compacted[2]["content"]
        assert loop._phase_start_idx_global == 3

    def test_phase_advance_without_following_wrap_up_uses_fallback(self, fake_home):
        """Edge case: backend crash between phase_advance and the wrap-up
        assistant move getting logged. Hydration should still complete with
        a generic summary placeholder instead of silently dropping the
        phase from the count."""
        sid = "resume_partial"
        self._seed_log(sid, [
            {"kind": "tool", "phase": "setup_import_source", "name": "x",
             "args": {}, "result_summary": "ok", "success": True},
            {"kind": "phase_advance", "phase": "setup_import_source",
             "to_phase": "setup_validate"},
            # No assistant move after — simulate the crash.
        ])
        loop = AgentLoop(
            llm=MagicMock(), blender=_make_blender(), session_id=sid,
        )
        assert loop._phase_idx == 1
        assert len(loop._global_history) == 1
        assert COMPACT_MARKER in loop._global_history[0]["content"]

    def test_mid_phase_work_not_replayed_into_history(self, fake_home):
        """Crash mid-phase (tool moves but no phase_advance yet) → the agent
        resumes at the SAME phase it was in, but the partial tool detail
        is not re-injected. Scene-is-memory + query_history handle recovery."""
        sid = "resume_mid"
        self._seed_log(sid, [
            {"kind": "user", "phase": "phase_1", "content": "go"},
            {"kind": "tool", "phase": "phase_1", "name": "scene_info",
             "args": {}, "result_summary": "...", "success": True},
            # No phase_advance — we're still mid-phase_1.
        ])
        loop = AgentLoop(
            llm=MagicMock(), blender=_make_blender(), session_id=sid,
        )
        # No phase_advance moves recorded.
        assert loop._phase_idx == 0
        # No compacted summaries injected.
        assert loop._global_history == []

    def test_turn_counter_resumes_from_existing_log(self, fake_home):
        """A resumed session's MoveLog should continue numbering from where
        the prior session left off, so the log stays monotonically ordered."""
        sid = "resume_turns"
        self._seed_log(sid, [
            {"kind": "user", "content": "first"},
            {"kind": "assistant", "content": "first reply"},
            {"kind": "user", "content": "second"},
        ])
        loop = AgentLoop(
            llm=MagicMock(), blender=_make_blender(), session_id=sid,
        )
        loop._move_log.append({"kind": "user", "content": "after resume"})
        all_moves = loop._move_log.read()
        # Pre-existing 3 + new 1 = 4 total, monotonic turns.
        assert [m["turn"] for m in all_moves] == [1, 2, 3, 4]


# ── query_history tool ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestQueryHistoryToolRegistration:
    def test_query_history_is_in_tool_list(self, fake_home):
        loop = AgentLoop(
            llm=MagicMock(),
            blender=_make_blender(),
            session_id="sess_q",
        )
        tools = loop._build_tool_list()
        names = [t["name"] for t in tools]
        assert QUERY_HISTORY_TOOL_NAME in names


@pytest.mark.unit
@pytest.mark.asyncio
class TestQueryHistoryToolExecution:
    async def _run_query_with(self, fake_home, params: dict, log_seeder) -> str:
        """Helper: seed a move log, fire query_history with given params via
        a stubbed LLM tool_use, and return the tool_result body string."""
        llm = _make_llm([
            _tool_use_response("q1", QUERY_HISTORY_TOOL_NAME, params),
            _text_response("ok"),
        ])
        loop = AgentLoop(
            llm=llm, blender=_make_blender(), session_id="sess_cap",
        )
        log_seeder(loop._move_log)
        loop.state = LoopState.RUNNING_PHASE
        await loop.step("query")
        tool_results = [
            block for msg in loop._global_history
            if msg["role"] == "user" and isinstance(msg["content"], list)
            for block in msg["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        return tool_results[0]["content"] if tool_results else ""

    async def test_omitted_last_n_falls_back_to_default_cap(self, fake_home):
        """A no-args query_history() must not dump the whole log — default cap
        applies even when the LLM omits last_n entirely."""
        def seed(log):
            for i in range(QUERY_HISTORY_DEFAULT_LAST_N + 100):
                log.append({"kind": "tool", "name": f"t{i}", "phase": "phase_1",
                            "args": {}, "result_summary": "ok", "success": True})
        body = await self._run_query_with(fake_home, {}, seed)
        import json as _json
        moves = _json.loads(body)
        assert isinstance(moves, list)
        assert len(moves) == QUERY_HISTORY_DEFAULT_LAST_N

    async def test_large_last_n_is_clamped_to_max(self, fake_home):
        """LLM asking for last_n=999999 must not get the whole log."""
        def seed(log):
            for i in range(QUERY_HISTORY_MAX_LAST_N + 200):
                log.append({"kind": "tool", "name": f"t{i}", "phase": "phase_1",
                            "args": {}, "result_summary": "ok", "success": True})
        body = await self._run_query_with(
            fake_home, {"last_n": 999999}, seed,
        )
        import json as _json
        moves = _json.loads(body)
        assert len(moves) == QUERY_HISTORY_MAX_LAST_N

    async def test_explicit_small_last_n_is_honored(self, fake_home):
        """LLM-supplied small last_n stays small — cap is a ceiling, not a floor."""
        def seed(log):
            for i in range(20):
                log.append({"kind": "tool", "name": f"t{i}", "phase": "phase_1",
                            "args": {}, "result_summary": "ok", "success": True})
        body = await self._run_query_with(
            fake_home, {"last_n": 3}, seed,
        )
        import json as _json
        moves = _json.loads(body)
        assert len(moves) == 3

    async def test_invalid_last_n_falls_back_to_default(self, fake_home):
        """Negative / zero / non-int last_n: treat as omitted, apply default cap."""
        def seed(log):
            for i in range(QUERY_HISTORY_DEFAULT_LAST_N + 50):
                log.append({"kind": "tool", "name": f"t{i}", "phase": "phase_1",
                            "args": {}, "result_summary": "ok", "success": True})
        body = await self._run_query_with(
            fake_home, {"last_n": -5}, seed,
        )
        import json as _json
        moves = _json.loads(body)
        assert len(moves) == QUERY_HISTORY_DEFAULT_LAST_N

    async def test_query_history_tool_returns_filtered_moves(self, fake_home):
        # Seed the log with synthetic moves, then have the LLM call query_history.
        llm = _make_llm([
            _tool_use_response("q1", QUERY_HISTORY_TOOL_NAME, {"kind": "tool"}),
            _text_response("got it"),
        ])
        loop = AgentLoop(
            llm=llm,
            blender=_make_blender(),
            session_id="sess_qh",
        )
        # Pre-seed move log directly to simulate a resumed session.
        loop._move_log.append({"kind": "user", "content": "earlier"})
        loop._move_log.append({"kind": "tool", "name": "scene_info", "phase": "phase_1"})
        loop._move_log.append({"kind": "tool", "name": "pose_correction", "phase": "phase_1"})
        loop.state = LoopState.RUNNING_PHASE
        await loop.step("what tools ran before?")
        # Find the query_history tool_use in history — its tool_result should
        # contain the two tool moves we seeded (kind=tool filter).
        tool_results = [
            block
            for msg in loop._global_history
            if msg["role"] == "user" and isinstance(msg["content"], list)
            for block in msg["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert tool_results, "expected at least one tool_result in history"
        body = tool_results[0]["content"]
        # Body is JSON-encoded list of moves; both seeded tool moves should appear.
        assert "scene_info" in body
        assert "pose_correction" in body
        # The 'user' kind from seeding should NOT appear in this filtered result.
        assert "earlier" not in body
