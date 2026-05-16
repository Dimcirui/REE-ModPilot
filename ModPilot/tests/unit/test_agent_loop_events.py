"""
Unit tests for AgentLoop event_sink emission.

Asserts that AgentLoop publishes the structured event sequence required by
the SSE frontend (issue #1) at every emit site:
  - message(user) at step() entry
  - state + phase_started on IDLE -> RUNNING_PHASE transition
  - tool_call / tool_result around each _execute_tool_call
  - phase_completed + next phase_started on phase advance
  - state(done) when the sequence is exhausted
  - state(error_handling) when a phase tool fails
  - state(await_confirm) when a propose-and-confirm proposal is detected
  - DSML inline-markup branch fires tool_call/tool_result the same as the
    structured tool_calls path

Run with: uv run pytest -m unit tests/unit/test_agent_loop_events.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agent.loop import _PHASE_SEQUENCE, AgentLoop, LoopState
from app.phases.base import PhaseError, PhaseResult


def _make_loop(*, sink: list[dict], llm=None, blender=None) -> AgentLoop:
    if llm is None:
        llm = MagicMock()
        llm.chat.return_value = MagicMock(
            content="Hello.", has_tool_calls=False, tool_calls=[]
        )
    if blender is None:
        blender = MagicMock()
        blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}
    return AgentLoop(llm=llm, blender=blender, event_sink=sink.append)


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_sink_default_does_not_break_step():
    """Default sink=None — existing 117 tests' implicit contract."""
    llm = MagicMock()
    llm.chat.return_value = MagicMock(
        content="ok", has_tool_calls=False, tool_calls=[]
    )
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}
    loop = AgentLoop(llm=llm, blender=blender)
    reply = await loop.step("hello")
    assert reply == "ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_only_first_turn_emits_state_and_phase_started():
    events: list[dict] = []
    loop = _make_loop(sink=events)
    await loop.step("Let's start")

    types = _types(events)
    # message(user) -> state(running_phase) -> phase_started(setup_validate) -> message(assistant)
    assert types == ["message", "state", "phase_started", "message"]
    assert events[0]["role"] == "user"
    assert events[1]["state"] == "running_phase"
    assert events[2]["phase"] == _PHASE_SEQUENCE[0]
    assert events[2]["index"] == 0
    assert events[2]["total"] == len(_PHASE_SEQUENCE)
    assert events[3]["role"] == "assistant"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successful_tool_call_emits_full_sequence():
    """One structured tool_call that advances the phase."""
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
            content_blocks=[],
        ),
        MagicMock(content="phase 1 done", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    # Bypass IDLE so we land directly in RUNNING_PHASE for a simpler assertion
    loop.state = LoopState.RUNNING_PHASE

    from app.phases.pose_correction import PoseCorrection
    with patch.object(PoseCorrection, "run", return_value=PhaseResult.ok({"k": 1})):
        await loop.step("run phase 1")

    types = _types(events)
    # Expected ordering:
    #   message(user) -> tool_call -> tool_result -> phase_completed -> phase_started -> message(assistant)
    assert types == [
        "message",
        "tool_call",
        "tool_result",
        "phase_completed",
        "phase_started",
        "message",
    ]
    assert events[1]["name"] == "pose_correction"
    assert events[1]["id"] == "t1"
    assert events[2]["success"] is True
    assert events[3]["phase"] == _PHASE_SEQUENCE[0]
    assert events[3]["index"] == 0
    assert events[4]["phase"] == _PHASE_SEQUENCE[1]
    assert events[4]["index"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dsml_inline_markup_path_emits_tool_call_events():
    """DeepSeek inline DSML markup path must still fire tool_call/tool_result."""
    dsml = (
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="pose_correction">'
        '<｜｜DSML｜｜parameter name="x_preset" string="true">MMD</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="source_armature" string="true">Body</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="target_armature" string="true">MHWs</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke>"
        "</｜｜DSML｜｜tool_calls>"
    )
    llm = MagicMock()
    llm.chat.side_effect = [
        MagicMock(content=dsml, has_tool_calls=False, tool_calls=[]),
        MagicMock(content="ok", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE

    from app.phases.pose_correction import PoseCorrection
    with patch.object(PoseCorrection, "run", return_value=PhaseResult.ok({"k": 1})):
        await loop.step("run via dsml")

    types = _types(events)
    # tool_call/tool_result must fire from the DSML branch the same as the structured path
    assert "tool_call" in types
    assert "tool_result" in types
    assert "phase_completed" in types
    assert "phase_started" in types
    tool_call_evt = next(e for e in events if e["type"] == "tool_call")
    assert tool_call_evt["name"] == "pose_correction"
    assert tool_call_evt["input"]["x_preset"] == "MMD"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase_failure_emits_state_error_handling():
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
            content_blocks=[],
        ),
        MagicMock(content="[Retry] | [Skip] | [Ask]", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE

    from app.phases.pose_correction import PoseCorrection
    error = PhaseError(category="operator_failed", operator="op", message="boom")
    with patch.object(PoseCorrection, "run", return_value=PhaseResult.fail(error)):
        await loop.step("try")

    # state(error_handling) must fire before the failure tool_result
    state_evts = [e for e in events if e["type"] == "state"]
    assert any(e["state"] == "error_handling" for e in state_evts)
    failure_evt = next(e for e in events if e["type"] == "tool_result")
    assert failure_evt["success"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase_failure_emits_error_choice_event():
    """error_choice event must follow the failure tool_result (issue #2).

    Carries the structured error payload the SSE renderer uses to build the
    three-button HTML fragment. Must appear AFTER state(error_handling) and
    AFTER the failure tool_result so the frontend can render buttons against
    the correct visible error message.
    """
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
            content_blocks=[],
        ),
        MagicMock(content="[Retry] | [Skip] | [Ask]", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE

    from app.phases.pose_correction import PoseCorrection
    error = PhaseError(
        category="operator_failed",
        operator="modder.pose_correction",
        message="No armature named 'Body' in scene",
    )
    with patch.object(PoseCorrection, "run", return_value=PhaseResult.fail(error)):
        await loop.step("try")

    error_choice_evts = [e for e in events if e["type"] == "error_choice"]
    assert len(error_choice_evts) == 1
    evt = error_choice_evts[0]
    assert evt["operator"] == "modder.pose_correction"
    assert evt["message"] == "No armature named 'Body' in scene"
    assert evt["summary"] == "No armature named 'Body' in scene"
    assert "ts" in evt and isinstance(evt["ts"], float)
    assert evt["state"] == "error_handling"
    assert evt["phase"] == "setup_validate"  # phase_idx hasn't advanced

    # Ordering: error_choice comes after the failure tool_result and after state(error_handling)
    types = _types(events)
    failure_idx = next(
        i for i, e in enumerate(events)
        if e["type"] == "tool_result" and e.get("success") is False
    )
    err_handling_state_idx = next(
        i for i, e in enumerate(events)
        if e["type"] == "state" and e.get("state") == "error_handling"
    )
    error_choice_idx = types.index("error_choice")
    assert error_choice_idx > failure_idx
    assert error_choice_idx > err_handling_state_idx


@pytest.mark.unit
@pytest.mark.asyncio
async def test_negotiating_proposal_emits_state_await_confirm():
    llm = MagicMock()
    llm.chat.return_value = MagicMock(
        content='```json\n{"proposals": [], "requires_user_review": true}\n```',
        has_tool_calls=False,
        tool_calls=[],
    )
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.NEGOTIATING
    loop._phase_idx = 6  # phase_4a

    await loop.step("classify physics bones")

    state_evts = [e for e in events if e["type"] == "state"]
    assert any(e["state"] == "await_confirm" for e in state_evts)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_event_payload_includes_ts_phase_state():
    events: list[dict] = []
    loop = _make_loop(sink=events)
    await loop.step("hi")

    for evt in events:
        assert "type" in evt
        assert "ts" in evt
        assert isinstance(evt["ts"], float)
        # phase may be None after DONE, but the key is always present
        assert "phase" in evt
        assert "state" in evt


@pytest.mark.unit
def test_session_config_injected_into_system_prompt():
    """Issue #3: the form-collected config must land in the system prompt so
    the LLM can pass values through to phase tools without asking the user.
    """
    llm = MagicMock()
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}
    cfg = {
        "model_path": "C:/models/hero.fbx",
        "model_type": "MMD",
        "texture_dir": "C:/models/tex",
        "mod_root": "C:/mods/myhero",
        "author": "Acme",
        "character_name": "Hero",
        "use_bone_system": True,
        "body_parts": ["1", "2", "3"],
    }
    loop = AgentLoop(llm=llm, blender=blender, session_config=cfg)
    prompt = loop._system_prompt

    assert "Pre-collected session parameters" in prompt
    assert "C:/models/hero.fbx" in prompt
    assert "Acme" in prompt
    assert "Hero" in prompt
    # x_preset mapping rendered for MMD path
    assert "x_preset='MMD'" in prompt
    # texture_base_path is derived from author + character
    assert "Acme/Hero/" in prompt
    # body_parts list rendered as Python literal
    assert "['1', '2', '3']" in prompt


@pytest.mark.unit
def test_session_config_other_model_type_asks_once():
    """When model_type='Other', the prompt should instruct the LLM to ask the
    user once for x_preset (rather than silently defaulting)."""
    llm = MagicMock()
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}
    cfg = {
        "model_path": "X",
        "model_type": "Other",
        "texture_dir": "T",
        "mod_root": "M",
        "author": "A",
        "character_name": "C",
        "use_bone_system": False,
        "body_parts": ["1"],
    }
    loop = AgentLoop(llm=llm, blender=blender, session_config=cfg)
    assert "ask the user ONCE" in loop._system_prompt


@pytest.mark.unit
def test_no_session_config_leaves_prompt_untouched():
    """Default (no config) — the Pre-collected block must not appear."""
    llm = MagicMock()
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}
    loop = AgentLoop(llm=llm, blender=blender)
    assert "Pre-collected session parameters" not in loop._system_prompt


# ── Issue #7: confirmation-widget event emission ───────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_physics_classification_success_emits_widget_classification():
    """After physics_classification succeeds, AgentLoop emits widget_classification
    carrying chain_heads so the SSE renderer can ship the editable table.
    """
    chain_heads = [
        {"name": "hair_001", "role": "head", "depth": 5, "parent": "head"},
        {"name": "skirt_002", "role": "head", "depth": 8, "parent": "waist"},
    ]
    llm = MagicMock()
    llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[{
                "id": "t-pc",
                "name": "physics_classification",
                "input": {"target_armature": "MHWs"},
            }],
            content_blocks=[],
        ),
        MagicMock(content="awaiting confirmation", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE
    loop._phase_idx = _PHASE_SEQUENCE.index("phase_4a")

    from app.phases.physics_bones import PhysicsClassification
    with patch.object(
        PhysicsClassification, "run",
        return_value=PhaseResult.ok({"chain_topology": {"chain_heads": chain_heads}}),
    ):
        await loop.step("classify physics bones")

    widget_evts = [e for e in events if e["type"] == "widget_classification"]
    assert len(widget_evts) == 1
    assert widget_evts[0]["chains"] == chain_heads


@pytest.mark.unit
@pytest.mark.asyncio
async def test_material_inspect_success_emits_widget_material():
    """material_inspect success → widget_material carrying materials +
    existing_connections + texture_files."""
    materials = ["body_mat", "hair_mat"]
    connections = {"body_mat": {"Base Color": None}}
    texture_files = ["C:/tex/diff.png", "C:/tex/norm.png"]

    llm = MagicMock()
    llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[{
                "id": "t-mi",
                "name": "material_inspect",
                "input": {"target_object": "Body", "texture_dir": "C:/tex"},
            }],
            content_blocks=[],
        ),
        MagicMock(content="awaiting confirmation", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE
    loop._phase_idx = _PHASE_SEQUENCE.index("phase_5")

    from app.phases.material import MaterialInspect
    with patch.object(
        MaterialInspect, "run",
        return_value=PhaseResult.ok({
            "materials": materials,
            "existing_connections": connections,
            "texture_files": texture_files,
        }),
    ):
        await loop.step("inspect materials")

    widget_evts = [e for e in events if e["type"] == "widget_material"]
    assert len(widget_evts) == 1
    assert widget_evts[0]["materials"] == materials
    assert widget_evts[0]["existing_connections"] == connections
    assert widget_evts[0]["texture_files"] == texture_files
