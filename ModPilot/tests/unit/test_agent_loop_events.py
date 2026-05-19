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

from app.agent.history_heal import heal_history
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
    # Phase-slot gate: align _phase_idx with the tool's declared slot.
    loop._phase_idx = _PHASE_SEQUENCE.index("phase_1")

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
    # Phase-slot gate: loop runs at phase_1 (not at idx 0) since pose_correction
    # declares phase_slot="phase_1". phase_completed/phase_started carry the
    # corresponding indices.
    phase_1_idx = _PHASE_SEQUENCE.index("phase_1")
    assert events[3]["phase"] == "phase_1"
    assert events[3]["index"] == phase_1_idx
    assert events[4]["phase"] == _PHASE_SEQUENCE[phase_1_idx + 1]
    assert events[4]["index"] == phase_1_idx + 1


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
    loop._phase_idx = _PHASE_SEQUENCE.index("phase_1")

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
    loop._phase_idx = _PHASE_SEQUENCE.index("phase_1")

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
    loop._phase_idx = _PHASE_SEQUENCE.index("phase_1")

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
    assert evt["phase"] == "phase_1"  # loop set to phase_1 to satisfy phase-slot gate

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

    annotated_heads = [
        {**ch, "guessed_nature": "头发", "group": "hair", "suggested_type": "light_hair", "suggest_merge": False}
        for ch in chain_heads
    ]

    async def _fake_annotate(chains):
        return annotated_heads

    from app.phases.physics_bones import PhysicsClassification
    with (
        patch.object(
            PhysicsClassification, "run",
            return_value=PhaseResult.ok({"chain_topology": {"chain_heads": chain_heads}}),
        ),
        patch.object(loop, "_annotate_chains", side_effect=_fake_annotate),
    ):
        await loop.step("classify physics bones")

    widget_evts = [e for e in events if e["type"] == "widget_classification"]
    assert len(widget_evts) == 1
    emitted_names = {ch["name"] for ch in widget_evts[0]["chains"]}
    assert emitted_names == {"hair_001", "skirt_002"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_widget_classification_emits_after_assistant_message():
    """Issue 3 — widget_classification must arrive AFTER the LLM has had a
    chance to comment on the inspection in chat.  Emitting at tool-return
    time (the old behaviour) surfaced an empty-looking table before any
    proposal text landed, which confused users into either submitting blank
    or chatting in parallel.  Deferred emit fixes this.
    """
    chain_heads = [{"name": "hair_001", "role": "head", "depth": 5, "parent": "head"}]
    llm = MagicMock()
    llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[{"id": "t1", "name": "physics_classification", "input": {}}],
            content_blocks=[],
        ),
        MagicMock(
            content="我建议把 hair_001 设为 hair_long_straight，请确认。",
            has_tool_calls=False,
            tool_calls=[],
        ),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE
    loop._phase_idx = _PHASE_SEQUENCE.index("phase_4a")

    async def _fake_annotate(chains):
        return [{**c, "suggested_type": "hair_long_straight", "group": "hair",
                 "guessed_nature": "头发", "suggest_merge": False} for c in chains]

    from app.phases.physics_bones import PhysicsClassification
    with (
        patch.object(
            PhysicsClassification, "run",
            return_value=PhaseResult.ok({"chain_topology": {"chain_heads": chain_heads}}),
        ),
        patch.object(loop, "_annotate_chains", side_effect=_fake_annotate),
    ):
        await loop.step("分类一下")

    types_order = [e["type"] for e in events]
    widget_idx = types_order.index("widget_classification")
    # The assistant message containing the proposal text MUST appear before
    # the widget so the user sees the chat-side proposal first.
    assistant_msg_indices = [
        i for i, e in enumerate(events)
        if e["type"] == "message" and e.get("role") == "assistant"
    ]
    assert assistant_msg_indices, "no assistant message emitted"
    assert max(assistant_msg_indices) < widget_idx, (
        f"widget emitted at index {widget_idx} BEFORE final assistant message at "
        f"{max(assistant_msg_indices)} — regression: widget should land AFTER "
        f"LLM commentary, not before. Order: {types_order}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_widget_safety_net_emits_even_without_text_commentary():
    """If the LLM skips text commentary entirely and jumps straight from
    inspector tool to the next tool (or hits an error), the deferred-emit
    safety net in `_run_react_turn`'s finally clause must still surface the
    widget — otherwise the user has no way to confirm and the pipeline
    silently stalls.
    """
    chain_heads = [{"name": "hair_001", "role": "head", "depth": 5, "parent": "head"}]
    llm = MagicMock()
    # Round 1: inspector tool call.
    # Round 2: LLM jumps straight to physics_chains with no commentary text.
    #          We make this 2nd call fail so the loop returns; this exercises
    #          the safety-net path that doesn't go through the text-only branch.
    llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[{"id": "t1", "name": "physics_classification", "input": {}}],
            content_blocks=[],
        ),
        MagicMock(
            content="",
            has_tool_calls=False,
            tool_calls=[],
        ),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE
    loop._phase_idx = _PHASE_SEQUENCE.index("phase_4a")

    async def _fake_annotate(chains):
        return [{**c, "suggested_type": "hair_long_straight"} for c in chains]

    from app.phases.physics_bones import PhysicsClassification
    with (
        patch.object(
            PhysicsClassification, "run",
            return_value=PhaseResult.ok({"chain_topology": {"chain_heads": chain_heads}}),
        ),
        patch.object(loop, "_annotate_chains", side_effect=_fake_annotate),
    ):
        await loop.step("classify")

    widget_evts = [e for e in events if e["type"] == "widget_classification"]
    assert widget_evts, "safety-net should emit widget even without text commentary"


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
        # Issue #11: extra LLM call from _suggest_texture_mapping; bare JSON
        # so it parses cleanly and the result rides on the widget event.
        MagicMock(
            content='{"body_mat": {"Base Color": "C:/tex/diff.png"}}',
            has_tool_calls=False, tool_calls=[],
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
    # Issue #11: LLM pre-fill suggestion now rides on the widget event.
    assert widget_evts[0]["suggestions"] == {
        "body_mat": {"Base Color": "C:/tex/diff.png"},
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_material_inspect_verify_purpose_suppresses_widget():
    """Phase 5A Step 6 verify call must NOT emit widget_material — otherwise
    the user has no Yes/No reply channel and material_setup re-fires in a
    self-feeding loop. See agent_workflow.md Phase 5A Step 6."""
    materials = ["body_mat"]
    connections = {"body_mat": {"Base Color": "C:/tex/diff.png"}}
    texture_files = ["C:/tex/diff.png"]

    llm = MagicMock()
    llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[{
                "id": "t-verify",
                "name": "material_inspect",
                "input": {
                    "target_object": "Body",
                    "texture_dir": "C:/tex",
                    "purpose": "verify",
                },
            }],
            content_blocks=[],
        ),
        MagicMock(content="all wired, ok?", has_tool_calls=False, tool_calls=[]),
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
        await loop.step("verify wiring")

    widget_evts = [e for e in events if e["type"] == "widget_material"]
    assert widget_evts == [], (
        "purpose=verify must suppress widget; got: "
        f"{widget_evts}"
    )
    # Sanity: _suggest_texture_mapping must also be skipped (no extra llm.chat
    # for pre-fill) — the verify path is read-only.
    assert llm.chat.call_count == 2, (
        f"expected 2 llm.chat calls (tool + reply), got {llm.chat.call_count}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_wrong_phase_slot_tool_is_rejected_without_advancing():
    """When the LLM calls an advancing tool whose phase_slot does not match
    the current _phase_idx slot, the loop must:
      - NOT execute the tool against Blender,
      - NOT advance _phase_idx,
      - NOT emit phase_completed,
      - emit a tool_result with success=False explaining the mismatch.

    Real-world bug: after phase_5 completed (loop at slot "phase_6"), the LLM
    called `setup_import_source` (slot "setup_import_source") and the loop
    blindly marked phase_6 done — flipping state to "done" without ever
    running batch_export.
    """
    llm = MagicMock()
    llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[{
                "id": "t-wrong",
                "name": "setup_import_source",
                "input": {"file_path": "C:/whatever.fbx"},
            }],
            content_blocks=[],
        ),
        MagicMock(content="rerouting", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE
    loop._phase_idx = _PHASE_SEQUENCE.index("phase_6")
    idx_before = loop._phase_idx

    # SetupImportSource.run must NOT be invoked — patch to assert that.
    from app.phases.setup import SetupImportSource
    with patch.object(
        SetupImportSource,
        "run",
        side_effect=AssertionError("tool.run should not be called on slot mismatch"),
    ):
        await loop.step("please check things")

    # Counter not bumped.
    assert loop._phase_idx == idx_before
    # No phase_completed for phase_6.
    phase_completed_events = [
        e for e in events if e["type"] == "phase_completed" and e.get("phase") == "phase_6"
    ]
    assert phase_completed_events == []
    # tool_result emitted with success=False and the mismatch summary.
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert tool_results, "expected at least one tool_result event"
    rejection = tool_results[-1]
    assert rejection["success"] is False
    assert "phase_6" in rejection["summary"]
    assert "setup_import_source" in rejection["summary"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_correct_phase_slot_tool_advances_normally():
    """Sanity check: a tool whose phase_slot matches the current slot still
    runs and advances the counter as before (the gate is a filter, not a
    blanket block on advancing tools)."""
    llm = MagicMock()
    llm.chat.side_effect = [
        MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[{
                "id": "t-ok",
                "name": "pose_correction",
                "input": {"target_object": "Armature"},
            }],
            content_blocks=[],
        ),
        MagicMock(content="phase_1 done", has_tool_calls=False, tool_calls=[]),
    ]
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    loop.state = LoopState.RUNNING_PHASE
    loop._phase_idx = _PHASE_SEQUENCE.index("phase_1")
    idx_before = loop._phase_idx

    from app.phases.pose_correction import PoseCorrection
    with patch.object(
        PoseCorrection, "run", return_value=PhaseResult.ok({}),
    ):
        await loop.step("run phase 1")

    assert loop._phase_idx == idx_before + 1
    phase_completed_events = [
        e for e in events if e["type"] == "phase_completed"
    ]
    assert any(e.get("phase") == "phase_1" for e in phase_completed_events)


# ── _annotate_chains base-name propagation ────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_annotate_chains_propagates_to_complex_variant_names():
    """The LLM often groups numbered + lateral siblings under one representative
    entry (e.g. annotates "Shoes ribbon" but not each "Shoes ribbon_L.001.L"
    variant).  _annotate_chains must strip combined .NNN/.L/.R/_L/_R/_End
    suffixes to derive a common base name, then copy the annotation to all
    unannotated siblings — otherwise the widget renders "—" for each variant
    even though the chat-side analysis correctly classified the group.

    Regression test for the Shoes ribbon / Skirt empty-inferred-type bug.
    """
    import json as _json

    # Chain heads pulled from a real-world MMD model: every common suffix
    # combination that the original `\.\d+$`-only regex failed on.
    chain_heads = [
        {"name": "Skirt_L.001", "role": "head", "depth": 3, "parent": "Hips_L"},
        {"name": "Skirt_L.006", "role": "head", "depth": 3, "parent": "Hips_L"},
        {"name": "Shoes ribbon_L.001.L", "role": "branch_head", "depth": 2, "parent": "x"},
        {"name": "Shoes ribbon_L.002.L", "role": "branch_head", "depth": 2, "parent": "x"},
        {"name": "Shoes ribbon.L_End", "role": "branch_head", "depth": 1, "parent": "y"},
        {"name": "Half twin tail_R.007", "role": "head", "depth": 5, "parent": "Head"},
    ]

    # Simulate the LLM grouping variants — it annotates ONE representative per
    # base, leaves all others with empty fields.  The fallback propagation must
    # fill them in.
    llm_response = [
        {"name": "Skirt_L.001", "guessed_nature": "裙子",
         "group": "cloth", "suggested_type": "cloth_skirt_waist"},
        {"name": "Skirt_L.006"},                       # unannotated sibling
        {"name": "Shoes ribbon_L.001.L", "guessed_nature": "鞋带",
         "group": "ribbon", "suggested_type": "accessory_ribbon"},
        {"name": "Shoes ribbon_L.002.L"},              # unannotated sibling
        {"name": "Shoes ribbon.L_End"},                # unannotated sibling, diff suffix shape
        {"name": "Half twin tail_R.007", "guessed_nature": "双马尾",
         "group": "hair", "suggested_type": "hair_twintail"},
    ]

    llm = MagicMock()
    llm.chat.return_value = MagicMock(
        content=_json.dumps(llm_response, ensure_ascii=False),
        has_tool_calls=False,
        tool_calls=[],
    )
    loop = _make_loop(sink=[], llm=llm)

    annotated = await loop._annotate_chains(chain_heads)
    by_name = {c["name"]: c for c in annotated}

    # All variants — even those the LLM left blank — must receive the
    # propagated suggested_type / group / guessed_nature.
    assert by_name["Skirt_L.001"]["suggested_type"] == "cloth_skirt_waist"
    assert by_name["Skirt_L.006"]["suggested_type"] == "cloth_skirt_waist"
    assert by_name["Skirt_L.006"]["group"] == "cloth"
    assert by_name["Shoes ribbon_L.001.L"]["suggested_type"] == "accessory_ribbon"
    assert by_name["Shoes ribbon_L.002.L"]["suggested_type"] == "accessory_ribbon"
    assert by_name["Shoes ribbon.L_End"]["suggested_type"] == "accessory_ribbon"
    assert by_name["Half twin tail_R.007"]["suggested_type"] == "hair_twintail"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_annotate_chains_fallback_recovers_dropped_lone_chain():
    """Single-chain bones with no sibling (e.g. Tail.001 — only tail in the
    whole rig) can't benefit from base-name propagation because there's no
    annotated sibling to copy from.  If the main pass drops them entirely
    (LLM returned shorter array, name match misses), the fallback retry
    must single-shot annotate the leftover and merge it back.

    Regression test for the Tail.001 empty-inferred-type bug.
    """
    import json as _json

    chain_heads = [
        {"name": "Back Hair.001", "role": "head", "depth": 5, "parent": "Head"},
        {"name": "Tail.001", "role": "head", "depth": 13, "parent": "Hips"},
    ]

    main_pass_response = [
        {"name": "Back Hair.001", "guessed_nature": "头发",
         "group": "hair", "suggested_type": "hair_long_straight"},
        # Note: Tail.001 deliberately ABSENT — LLM dropped it.
    ]
    fallback_pass_response = [
        {"name": "Tail.001", "guessed_nature": "尾巴",
         "group": "tail", "suggested_type": "fur_tail"},
    ]

    llm = MagicMock()
    llm.chat.side_effect = [
        MagicMock(content=_json.dumps(main_pass_response, ensure_ascii=False),
                  has_tool_calls=False, tool_calls=[]),
        MagicMock(content=_json.dumps(fallback_pass_response, ensure_ascii=False),
                  has_tool_calls=False, tool_calls=[]),
    ]
    loop = _make_loop(sink=[], llm=llm)

    annotated = await loop._annotate_chains(chain_heads)
    by_name = {c["name"]: c for c in annotated}

    assert by_name["Back Hair.001"]["suggested_type"] == "hair_long_straight"
    # The whole point of this fix: Tail.001 must NOT be left with empty
    # suggested_type even though the main LLM pass dropped it entirely.
    assert by_name["Tail.001"]["suggested_type"] == "fur_tail"
    assert by_name["Tail.001"]["group"] == "tail"
    # The fallback should have run exactly once (two total LLM calls).
    assert llm.chat.call_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_annotate_chains_skips_fallback_when_all_annotated():
    """If the main pass annotated everything, no fallback LLM call is made."""
    import json as _json

    chain_heads = [
        {"name": "Hair.001", "role": "head", "depth": 3, "parent": "Head"},
    ]
    response = [
        {"name": "Hair.001", "guessed_nature": "头发",
         "group": "hair", "suggested_type": "hair_long_straight"},
    ]
    llm = MagicMock()
    llm.chat.return_value = MagicMock(
        content=_json.dumps(response, ensure_ascii=False),
        has_tool_calls=False, tool_calls=[],
    )
    loop = _make_loop(sink=[], llm=llm)
    await loop._annotate_chains(chain_heads)
    # Exactly one LLM call — no fallback triggered.
    assert llm.chat.call_count == 1


# ── heal_history defensive self-heal ──────────────────────────────────────


@pytest.mark.unit
class TestHealHistory:
    """Regression tests for the Anthropic 400 'tool_use without tool_result'
    backstop.  heal_history must inject placeholder tool_results for any
    orphan tool_use blocks before the history is sent to the LLM."""

    def test_clean_history_unchanged(self):
        history = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "calling tool"},
                    {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            },
        ]
        snapshot = [dict(m) for m in history]
        injected = heal_history(history)
        assert injected == 0
        assert history == snapshot

    def test_missing_tool_result_user_message_inserted(self):
        """Assistant tool_use with NO following user message at all."""
        history = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "orphan-1", "name": "x", "input": {}},
                ],
            },
        ]
        injected = heal_history(history)
        assert injected == 1
        assert len(history) == 3
        assert history[2]["role"] == "user"
        result_blocks = history[2]["content"]
        assert any(
            b.get("type") == "tool_result" and b.get("tool_use_id") == "orphan-1"
            for b in result_blocks
        )

    def test_partial_tool_result_filled(self):
        """Two tool_use blocks but only one tool_result — fill the missing one."""
        history = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "a", "name": "x", "input": {}},
                    {"type": "tool_use", "id": "b", "name": "x", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "a", "content": "ok"}],
            },
        ]
        injected = heal_history(history)
        assert injected == 1
        ids = {
            b["tool_use_id"]
            for b in history[1]["content"]
            if b.get("type") == "tool_result"
        }
        assert ids == {"a", "b"}

    def test_next_message_is_plain_user_text_then_insert_before(self):
        """Assistant tool_use followed by plain-text user message — insert a
        synthetic tool_result message between them (otherwise the plain user
        message is misinterpreted by Anthropic as the tool_result slot)."""
        history = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "z", "name": "x", "input": {}},
                ],
            },
            {"role": "user", "content": "retry please"},
        ]
        injected = heal_history(history)
        assert injected == 1
        # The synthetic user message gets inserted at index 1; the original
        # plain-text user message slides to index 2.
        assert isinstance(history[1]["content"], list)
        assert history[1]["content"][0]["tool_use_id"] == "z"
        assert history[2]["content"] == "retry please"

    def test_orphan_tool_result_with_unknown_id_dropped(self):
        """Reverse failure mode: tool_result with id that doesn't match any
        preceding tool_use must be dropped, otherwise Anthropic 400s with
        "unexpected `tool_use_id` found in tool_result blocks".

        Real-world trigger: provider's `content_blocks` and `tool_calls` ids
        get desynchronised (e.g. OpenAI-compat / Ollama synthesizing call ids
        client-side), so the assistant message has one id and the loop's
        tool_result building uses another.
        """
        history = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "valid-a", "name": "scene_info", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "valid-a", "content": "ok"},
                    {"type": "tool_result", "tool_use_id": "unknown-b", "content": "orphan"},
                ],
            },
        ]
        injected = heal_history(history)
        assert injected == 1
        remaining_ids = [
            b["tool_use_id"] for b in history[1]["content"] if b.get("type") == "tool_result"
        ]
        assert remaining_ids == ["valid-a"]

    def test_orphan_tool_result_after_text_only_assistant_dropped(self):
        """Assistant text-only message followed by user message containing
        tool_result blocks — drop the orphan tool_result blocks entirely."""
        history = [
            {"role": "assistant", "content": [{"type": "text", "text": "Just a note."}]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "leaked", "content": "stale"},
                ],
            },
        ]
        injected = heal_history(history)
        assert injected == 1
        # Empty content list would itself be a 400 — must leave a marker block.
        assert history[1]["content"]
        assert all(b.get("type") != "tool_result" for b in history[1]["content"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_suggest_texture_mapping_filters_and_handles_failure():
    """Issue #11: _suggest_texture_mapping drops unknown materials/slots and
    invented file paths, and returns {} when the LLM raises or replies with
    non-JSON content.
    """
    llm = MagicMock()
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    loop = _make_loop(sink=[], llm=llm, blender=blender)

    materials = ["body_mat"]
    texture_files = ["C:/tex/body_diffuse.png"]

    # 1. Happy path: clean JSON; unknown mat, unknown slot, and invented file
    #    are all filtered out.
    llm.chat.return_value = MagicMock(
        content=(
            '{"body_mat": {"Base Color": "C:/tex/body_diffuse.png", '
            '"NotASlot": "anything", "Normal": "C:/tex/invented.png"}, '
            '"unknown_mat": {"Base Color": "C:/tex/body_diffuse.png"}}'
        ),
        has_tool_calls=False, tool_calls=[],
    )
    result = await loop._suggest_texture_mapping(materials, texture_files, {})
    assert result == {"body_mat": {"Base Color": "C:/tex/body_diffuse.png"}}

    # 2. Non-JSON content → {}
    llm.chat.return_value = MagicMock(
        content="sure, here's a guess: Base Color → body_diffuse.png",
        has_tool_calls=False, tool_calls=[],
    )
    result = await loop._suggest_texture_mapping(materials, texture_files, {})
    assert result == {}

    # 3. LLM raises → {}
    llm.chat.side_effect = RuntimeError("provider down")
    result = await loop._suggest_texture_mapping(materials, texture_files, {})
    assert result == {}

    # 4. No texture files / no materials → short-circuit, no LLM call
    llm.chat.reset_mock(side_effect=True)
    llm.chat.side_effect = AssertionError("must not call LLM with empty inputs")
    assert await loop._suggest_texture_mapping([], texture_files, {}) == {}
    assert await loop._suggest_texture_mapping(materials, [], {}) == {}
