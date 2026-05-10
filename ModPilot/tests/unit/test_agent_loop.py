"""
Unit tests for Stage 3 agent components.

Covers:
  - prompts.py: section extraction, build_system_prompt, build_phase_prompt
  - error_handler.py: parse_user_choice (no LLM), format() with mock LLM
  - loop.py: state machine transitions with mocked LLM and phase tools

Run with: uv run pytest -m unit tests/unit/test_agent_loop.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.error_handler import ErrorHandler
from app.agent.loop import AgentLoop, LoopState
from app.agent.prompts import (
    build_error_prompt,
    build_phase_prompt,
    build_system_prompt,
    _extract_section,
)
from app.phases.base import PhaseError, PhaseResult


# ── prompts ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestExtractSection:
    def test_extracts_h2_section(self):
        doc = "## Global Behavior Rules\nsome content\n## Next Section\nother"
        result = _extract_section(doc, "Global Behavior Rules")
        assert "some content" in result
        assert "Next Section" not in result

    def test_extracts_h3_section_inside_h2(self):
        doc = "## Parent\n### Phase 1: Pose Correction\ncontent\n### Phase 2:\nother"
        result = _extract_section(doc, "Phase 1: Pose Correction")
        assert "content" in result
        assert "Phase 2" not in result

    def test_returns_empty_for_unknown_header(self):
        doc = "## Section A\ncontent"
        assert _extract_section(doc, "Nonexistent Header") == ""

    def test_case_insensitive_match(self):
        doc = "## Global Behavior Rules\nfound"
        result = _extract_section(doc, "global behavior rules")
        assert "found" in result


@pytest.mark.unit
class TestBuildSystemPrompt:
    def test_returns_nonempty_string(self):
        prompt = build_system_prompt()
        assert len(prompt) > 100

    def test_contains_agent_identity(self):
        prompt = build_system_prompt()
        assert "ModPilot" in prompt

    def test_injects_physics_presets(self):
        presets = {"light_hair": "MHWilds Short Hair"}
        prompt = build_system_prompt(presets)
        assert "light_hair" in prompt
        assert "MHWilds Short Hair" in prompt

    def test_no_presets_does_not_include_section(self):
        prompt = build_system_prompt(None)
        assert "Physics Presets Reference" not in prompt


@pytest.mark.unit
class TestBuildPhasePrompt:
    def test_returns_content_for_known_phase(self):
        prompt = build_phase_prompt("phase_1")
        assert len(prompt) > 50

    def test_returns_empty_for_unknown_phase(self):
        assert build_phase_prompt("phase_99") == ""

    def test_phase_prompt_contains_phase_name(self):
        prompt = build_phase_prompt("phase_2")
        assert "Skeleton" in prompt or "skeleton" in prompt

    def test_all_known_phases_return_content(self):
        known = ["phase_1", "phase_2", "phase_3"]
        for name in known:
            assert len(build_phase_prompt(name)) > 0, f"Empty prompt for {name}"


@pytest.mark.unit
class TestBuildErrorPrompt:
    def test_includes_operator(self):
        prompt = build_error_prompt("pose.transforms_clear", "some error", "fix hint")
        assert "pose.transforms_clear" in prompt

    def test_includes_message(self):
        prompt = build_error_prompt("", "some error", "")
        assert "some error" in prompt

    def test_includes_suggestion_when_provided(self):
        prompt = build_error_prompt("", "err", "check the armature")
        assert "check the armature" in prompt

    def test_omits_suggestion_when_empty(self):
        prompt = build_error_prompt("op", "err", "")
        assert "Suggested fix" not in prompt


# ── error handler ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestParseUserChoice:
    handler = ErrorHandler()

    @pytest.mark.parametrize(
        "reply,expected",
        [
            ("Retry", "retry"),
            ("let's retry please", "retry"),
            ("重试", "retry"),
            ("再试一次", "retry"),
            ("skip this step", "skip"),
            ("跳过", "skip"),
            ("ask why it failed", "ask"),
            ("why did it fail?", "ask"),
            ("为什么", "ask"),
            ("help me understand", "ask"),
            ("sure whatever", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_parse_choice(self, reply, expected):
        assert self.handler.parse_user_choice(reply) == expected


@pytest.mark.unit
def test_error_handler_format_calls_llm():
    handler = ErrorHandler()
    mock_llm = MagicMock()
    mock_llm.chat.return_value = MagicMock(content="Something went wrong. [Retry] | [Skip] | [Ask]")
    error = PhaseError(
        category="operator_failed",
        operator="modder.universal_snap",
        message="Operator returned CANCELLED",
        suggestion="Check armature names.",
    )
    result = handler.format(error, mock_llm)
    assert "[Retry]" in result
    mock_llm.chat.assert_called_once()


# ── agent loop state machine ───────────────────────────────────────────────


def _make_loop(llm=None, blender=None) -> AgentLoop:
    if llm is None:
        llm = MagicMock()
        llm.chat.return_value = MagicMock(
            content="Hello, let's start.",
            has_tool_calls=False,
            tool_calls=[],
        )
    if blender is None:
        blender = MagicMock()
        blender.get_scene_info.return_value = {"name": "Scene", "object_count": 3}
    return AgentLoop(llm=llm, blender=blender)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_initial_state_is_idle():
    loop = _make_loop()
    assert loop.state == LoopState.IDLE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_step_transitions_idle_to_running():
    loop = _make_loop()
    await loop.step("Let's start")
    assert loop.state == LoopState.RUNNING_PHASE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_response_stays_in_running_phase():
    loop = _make_loop()
    await loop.step("Let's start")
    await loop.step("My armatures are BodyArm and MHWsFemale")
    assert loop.state == LoopState.RUNNING_PHASE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successful_tool_call_advances_phase():
    llm = MagicMock()
    # First call: LLM requests tool use
    llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[
                {"id": "t1", "name": "pose_correction", "input": {
                    "x_preset": "MMD",
                    "source_armature": "Body",
                    "target_armature": "MHWs",
                }}
            ],
        ),
        # Second call: LLM produces text after tool result
        MagicMock(content="Phase 1 done!", has_tool_calls=False, tool_calls=[]),
    ]

    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 2}

    loop = _make_loop(llm=llm, blender=blender)

    # Patch PoseCorrection.run to return success
    from app.phases.pose_correction import PoseCorrection
    with patch.object(PoseCorrection, "run", return_value=PhaseResult.ok({"test": 1})):
        loop.state = LoopState.RUNNING_PHASE
        reply = await loop.step("Please run phase 1")

    assert loop._phase_idx == 1
    assert "Phase 1 done!" in reply


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase_failure_enters_error_handling():
    llm = MagicMock()
    llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[
                {"id": "t1", "name": "pose_correction", "input": {
                    "x_preset": "MMD",
                    "source_armature": "Body",
                    "target_armature": "MHWs",
                }}
            ],
        ),
        # Error handler calls LLM to format the error
        MagicMock(content="Something failed. [Retry] | [Skip] | [Ask]", has_tool_calls=False, tool_calls=[]),
    ]

    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 2}

    loop = _make_loop(llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE

    from app.phases.pose_correction import PoseCorrection
    error = PhaseError(category="operator_failed", operator="pose.transforms_clear", message="Cancelled")
    with patch.object(PoseCorrection, "run", return_value=PhaseResult.fail(error)):
        reply = await loop.step("Run phase 1")

    assert loop.state == LoopState.ERROR_HANDLING
    assert "[Retry]" in reply


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_after_error_reruns_phase():
    llm = MagicMock()
    llm.chat.return_value = MagicMock(
        content="Retrying...", has_tool_calls=False, tool_calls=[]
    )
    loop = _make_loop(llm=llm)
    loop.state = LoopState.ERROR_HANDLING
    loop._phase_idx = 1
    loop._pending_error = PhaseError(
        category="operator_failed", operator="op", message="failed"
    )

    reply = await loop.step("Retry")
    assert loop.state == LoopState.RUNNING_PHASE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skip_advances_phase():
    llm = MagicMock()
    llm.chat.return_value = MagicMock(
        content="Skipped.", has_tool_calls=False, tool_calls=[]
    )
    loop = _make_loop(llm=llm)
    loop.state = LoopState.ERROR_HANDLING
    loop._phase_idx = 0
    loop._pending_error = PhaseError(
        category="operator_failed", operator="op", message="failed"
    )

    reply = await loop.step("Skip")
    assert loop._phase_idx == 1
    assert "Skipping" in reply


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ask_enters_ask_mode():
    llm = MagicMock()
    llm.chat.return_value = MagicMock(
        content="The error means X.", has_tool_calls=False, tool_calls=[]
    )
    loop = _make_loop(llm=llm)
    loop.state = LoopState.ERROR_HANDLING
    loop._phase_idx = 0
    loop._pending_error = PhaseError(
        category="operator_failed", operator="op", message="failed"
    )

    await loop.step("Ask")
    assert loop.state == LoopState.ASK_MODE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_negotiating_transitions_to_await_confirm_on_proposal():
    llm = MagicMock()
    # LLM returns a proposal with requires_user_review
    llm.chat.return_value = MagicMock(
        content='```json\n{"proposals": [], "requires_user_review": true}\n```',
        has_tool_calls=False,
        tool_calls=[],
    )
    loop = _make_loop(llm=llm)
    loop.state = LoopState.NEGOTIATING
    loop._phase_idx = 4  # phase_4a

    await loop.step("Please classify the physics bones")
    assert loop.state == LoopState.AWAIT_CONFIRM


@pytest.mark.unit
@pytest.mark.asyncio
async def test_done_state_returns_completion_message():
    loop = _make_loop()
    loop.state = LoopState.DONE
    reply = await loop.step("Are we done?")
    assert "complete" in reply.lower() or "finished" in reply.lower()


@pytest.mark.unit
def test_tool_schema_present_on_all_registered_phases():
    loop = _make_loop()
    for name, phase in loop._phase_tools.items():
        schema = phase.tool_schema()
        assert schema["name"] == name
        assert "description" in schema
        assert "input_schema" in schema


@pytest.mark.unit
def test_build_tool_list_returns_list():
    loop = _make_loop()
    tools = loop._build_tool_list()
    assert isinstance(tools, list)
    assert len(tools) == 3  # pose_correction, skeleton_align, vertex_groups
    names = {t["name"] for t in tools}
    assert names == {"pose_correction", "skeleton_align", "vertex_groups"}
