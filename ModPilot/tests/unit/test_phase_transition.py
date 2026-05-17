"""
Unit tests for the inter-phase transition pause (issue #15).

The contract:
  - After a phase-advancing tool succeeds while state stays RUNNING_PHASE,
    `_run_react_turn` bails out of the tool-call loop and produces a single
    text-only wrap-up via one extra `llm.chat` call (no tools), then returns
    that text to the user.  The LLM is NOT given the opportunity to call the
    next phase tool in the same step.
  - Sub-step tools (`advances_phase=False`) do NOT trigger the pause — the
    loop continues into another tool round as before.
  - The flag is reset at the top of each `step()` so chaining across user
    turns proceeds normally (next user message → next phase tool).
  - `build_system_prompt()` carries the "Phase Transition Protocol" section
    extracted from `agent_workflow.md`, so the LLM is told the rule too.

Run with: uv run pytest -m unit tests/unit/test_phase_transition.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agent.loop import AgentLoop, LoopState
from app.agent.prompts import build_system_prompt
from app.phases.base import PhaseResult


def _make_loop(*, sink: list[dict], llm=None, blender=None) -> AgentLoop:
    if llm is None:
        llm = MagicMock()
        llm.chat.return_value = MagicMock(
            content="ok", has_tool_calls=False, tool_calls=[]
        )
    if blender is None:
        blender = MagicMock()
        blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}
    return AgentLoop(llm=llm, blender=blender, event_sink=sink.append)


# ── prompt-level protocol ───────────────────────────────────────────────────


@pytest.mark.unit
def test_transition_protocol_present_in_system_prompt():
    """The system prompt must include the Phase Transition Protocol section so
    the LLM is told the rule, not just gated by the backend rail."""
    sysp = build_system_prompt()
    # Marker phrase from the new section; deliberately specific so it can't
    # collide with other workflow sections.
    assert "Phase Transition Protocol" in sysp
    # Must mention the core behavior: pause + report + wait for user.
    assert "wait" in sysp.lower() or "暂停" in sysp or "等待" in sysp


# ── backend rail: pause after phase-advancing tool ──────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_advancing_tool_in_running_phase_pauses_before_next_tool_round():
    """After SetupValidateScene succeeds, the loop must NOT chain into a second
    tool round.  Instead it should fire one wrap-up LLM call (no tools) and
    return that text to the user."""
    llm = MagicMock()
    # 1st call: tool call to setup_validate (the phase-advancing tool).
    # 2nd call: wrap-up text — would have been a chained next-phase tool call
    # before this fix, but the rail breaks the loop before that.
    llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[
                {"id": "t1", "name": "setup_validate_scene", "input": {}}
            ],
            content_blocks=[],
        ),
        MagicMock(content="✓ setup_validate done. Ready for setup_infer.", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE

    from app.phases.setup import SetupValidateScene
    with patch.object(SetupValidateScene, "run", return_value=PhaseResult.ok({"k": 1})):
        reply = await loop.step("start")

    # Exactly two LLM calls: the tool-call round, then the wrap-up round.
    # Without the rail this would be 3+ (next phase tool call + its wrap-up).
    assert llm.chat.call_count == 2
    # The wrap-up call MUST be text-only (no tools arg).  Phase tools are
    # withheld so the LLM cannot smuggle a next-phase tool call into the gap.
    wrap_call = llm.chat.call_args_list[1]
    assert wrap_call.kwargs.get("tools") is None
    # Reply is the wrap-up text, not the tool result blob.
    assert reply == "✓ setup_validate done. Ready for setup_infer."
    # The phase did advance — the rail pauses, it does not block progress.
    assert loop._phase_idx == 1
    # State stays RUNNING_PHASE so the NEXT user turn can continue.
    assert loop.state == LoopState.RUNNING_PHASE
    # Flag is cleared so the next step() doesn't re-bail immediately.
    assert loop._phase_just_advanced is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_next_step_after_pause_can_call_the_next_phase_tool():
    """The pause is per-turn, not permanent: after the user sends 'continue',
    the loop must be willing to call the next phase tool."""
    llm = MagicMock()
    llm.chat.side_effect = [
        # Turn 1: setup_validate then wrap-up
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[{"id": "t1", "name": "setup_validate_scene", "input": {}}],
            content_blocks=[],
        ),
        MagicMock(content="phase 0 done", has_tool_calls=False, tool_calls=[]),
        # Turn 2: setup_infer (next advancing phase) then wrap-up
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[{
                "id": "t2", "name": "setup_infer_model_type",  # advances phase
                "input": {"source_armature": "Body"}
            }],
            content_blocks=[],
        ),
        MagicMock(content="phase 1 done", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE

    from app.phases.setup import SetupValidateScene
    from app.phases.infer_model_type import InferModelType
    with patch.object(SetupValidateScene, "run", return_value=PhaseResult.ok({"k": 1})), \
         patch.object(InferModelType, "run", return_value=PhaseResult.ok({"preset": "MMD"})):
        await loop.step("start")
        reply2 = await loop.step("continue")

    assert loop._phase_idx == 2
    assert reply2 == "phase 1 done"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_substep_tool_does_not_trigger_pause():
    """material_inspect has `advances_phase=False`; a successful call must
    NOT cause the loop to break out — it should continue the tool-call loop
    until the LLM produces text-only output."""
    llm = MagicMock()
    llm.chat.side_effect = [
        # Round 1: material_inspect (sub-step, does not advance phase)
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[{
                "id": "t1", "name": "material_inspect",
                "input": {"target_object": "Body"},
            }],
            content_blocks=[],
        ),
        # Round 2: LLM now produces text-only output ending the turn naturally
        MagicMock(content="inspected", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE
    # Position the loop on Phase 5 where material_inspect makes sense.
    loop._phase_idx = 9  # phase_5

    from app.phases.material import MaterialInspect

    fake_diff = {
        "materials": ["m1"],
        "existing_connections": {},
        "texture_files": [],
    }
    with patch.object(
        MaterialInspect, "run", return_value=PhaseResult.ok(fake_diff)
    ), patch.object(
        AgentLoop, "_suggest_texture_mapping", return_value={},
    ):
        reply = await loop.step("inspect materials")

    # Two LLM calls is the normal tool-loop behavior; no extra wrap-up
    # injected because the phase did not advance.
    assert llm.chat.call_count == 2
    # Phase index unchanged — material_inspect is a sub-step.
    assert loop._phase_idx == 9
    # The flag never flipped on.
    assert loop._phase_just_advanced is False
    assert reply == "inspected"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_flag_resets_when_set_pre_step():
    """A leftover True flag at the start of step() must be cleared.  This is
    a safety belt — under normal operation the flag is reset by the wrap-up
    branch, but defensive reset at step entry prevents one path's leak from
    breaking the next turn."""
    events: list[dict] = []
    loop = _make_loop(sink=events)
    loop.state = LoopState.RUNNING_PHASE
    # Simulate a leak (should not happen in practice but guard the contract).
    loop._phase_just_advanced = True
    reply = await loop.step("hello")
    # The simple text-only LLM mock returned "ok" — that means step() ran
    # normally and was not short-circuited by the leftover flag.
    assert reply == "ok"
    assert loop._phase_just_advanced is False
