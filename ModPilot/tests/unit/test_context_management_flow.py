"""
Mock-driven end-to-end exercise of the context-management layer.

Where the other history tests cover individual mechanisms in isolation
(`test_history.py` for the pure pieces, `test_agent_loop_history.py` for
per-method AgentLoop wiring), this file walks the full lifecycle as a
session would experience it — multiple phases, an AgentLoop restart with
the same session_id, a fully-complete recovery — using a stubbed LLM and
Blender. No real services. The goal is to catch regressions that only
surface when several pieces interact across turn boundaries.

Run with:
    uv run pytest -m unit tests/unit/test_context_management_flow.py -v -s
The `-s` flag surfaces the per-step prints so you can watch the in-memory
history shape evolve.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.agent.history import COMPACT_MARKER, MoveLog
from app.agent.loop import _PHASE_SEQUENCE, AgentLoop, LoopState
from app.phases.base import PhaseResult


# ── fixtures + helpers ───────────────────────────────────────────────────────


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _make_blender() -> MagicMock:
    b = MagicMock()
    b.get_scene_info.return_value = {"name": "Scene", "object_count": 0}
    return b


def _tool_use(tool_id: str, name: str, input_: dict | None = None) -> MagicMock:
    return MagicMock(
        content="",
        has_tool_calls=True,
        tool_calls=[{"id": tool_id, "name": name, "input": input_ or {}}],
        content_blocks=[],
    )


def _text(text: str) -> MagicMock:
    return MagicMock(content=text, has_tool_calls=False, tool_calls=[])


def _llm(responses: list[MagicMock]) -> MagicMock:
    m = MagicMock()
    m.chat.side_effect = responses
    return m


# Mapping from phase index → (phase name, tool name that advances it,
# PhaseTool subclass to patch). Picked from `_register_available_phases`
# so the stub advances the same `_phase_idx` the live loop would.
_PHASE_TOOL_OF: dict[int, tuple[str, str, str]] = {
    0: ("setup_import_source", "setup_import_source", "SetupImportSource"),
    1: ("setup_validate", "setup_validate_scene", "SetupValidateScene"),
    2: ("setup_infer", "setup_infer_model_type", "InferModelType"),
}


def _patch_phase_tool(phase_idx: int):
    """Patch the PhaseTool whose tool advances `_PHASE_SEQUENCE[phase_idx]`
    so its `run()` returns a successful PhaseResult without touching
    Blender."""
    _, _, cls_name = _PHASE_TOOL_OF[phase_idx]
    if cls_name == "SetupImportSource":
        from app.phases.setup import SetupImportSource as Cls
    elif cls_name == "SetupValidateScene":
        from app.phases.setup import SetupValidateScene as Cls
    elif cls_name == "InferModelType":
        from app.phases.infer_model_type import InferModelType as Cls
    else:  # pragma: no cover
        raise AssertionError(f"no patch hook for {cls_name}")
    return patch.object(Cls, "run", return_value=PhaseResult.ok({"k": phase_idx}))


def _snapshot(label: str, loop: AgentLoop) -> dict:
    """Compact snapshot of the loop's externally-visible state. Returned as
    a dict so the assert-on-shape test paths can use it; also printed
    inline so `-s` runs read like a story."""
    roles = [m["role"] for m in loop._global_history]
    snap = {
        "label": label,
        "phase_idx": loop._phase_idx,
        "current_phase": loop.current_phase,
        "state": loop.state.value,
        "history_len": len(loop._global_history),
        "roles": roles,
        "compact_count": sum(
            1 for m in loop._global_history
            if isinstance(m.get("content"), str) and COMPACT_MARKER in m["content"]
        ),
    }
    print(
        f"\n  [{label}] phase_idx={snap['phase_idx']} "
        f"current_phase={snap['current_phase']} state={snap['state']} "
        f"len(history)={snap['history_len']} roles={roles} "
        f"compacted={snap['compact_count']}"
    )
    return snap


def _assert_well_formed(roles: list[str]) -> None:
    """Anthropic-acceptable shape: first message is user, no two consecutive
    messages share the same role. Run this anywhere the history is about
    to be handed to the LLM."""
    assert roles, "history must not be empty"
    assert roles[0] == "user", f"first message must be user, got {roles}"
    for i in range(len(roles) - 1):
        assert roles[i] != roles[i + 1], (
            f"consecutive same-role messages at index {i}: {roles}"
        )


# ── flow 1: walk multiple phases in one session, then restart ───────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_multi_phase_flow_with_restart(fake_home):
    """Cold session → advance 2 phases → tear down → rehydrate from disk →
    advance a 3rd phase. At every llm.chat boundary the in-memory history
    must satisfy the Anthropic invariants (`_assert_well_formed`), and
    the on-disk move log must accumulate the expected phase_advance
    moves across the restart."""
    sid = "flow_multi_phase"

    # ── stage 1: cold start, run phase 0 then phase 1 ───────────────────
    llm1 = _llm([
        # turn 1: tool_use → setup_import_source → wrap-up
        _tool_use("t0", "setup_import_source", {"file_path": "x.fbx"}),
        _text("Source imported."),
        # turn 2: tool_use → setup_validate_scene → wrap-up
        _tool_use("t1", "setup_validate_scene", {}),
        _text("Scene validated."),
    ])
    loop = AgentLoop(llm=llm1, blender=_make_blender(), session_id=sid)
    _snapshot("cold start", loop)

    with _patch_phase_tool(0):
        await loop.step("start the import")
    snap = _snapshot("after phase 0", loop)
    assert snap["phase_idx"] == 1
    _assert_well_formed(snap["roles"])

    with _patch_phase_tool(1):
        await loop.step("continue")
    snap = _snapshot("after phase 1", loop)
    assert snap["phase_idx"] == 2
    # Phase 0's verbose blocks should be compacted; phase 1's still live.
    assert snap["compact_count"] == 1, "phase 0 should be the only compacted block yet"
    _assert_well_formed(snap["roles"])

    # ── stage 2: tear down, rebuild from disk, advance phase 2 ───────────
    # Same session_id → AgentLoop construction triggers _hydrate_from_move_log.
    del loop

    llm2 = _llm([
        _tool_use("t2", "setup_infer_model_type", {"source_armature": "A"}),
        _text("Model type inferred."),
    ])
    loop2 = AgentLoop(llm=llm2, blender=_make_blender(), session_id=sid)
    snap = _snapshot("after restart (pre-step)", loop2)
    # Hydration must replay the 2 prior phase_advances as user/asst pairs.
    assert snap["phase_idx"] == 2, "hydration must preserve advance count"
    assert snap["state"] == "idle", "post-hydration state is IDLE until a step fires"
    assert snap["compact_count"] == 2, "both prior phases come back as compacted summaries"
    _assert_well_formed(snap["roles"])

    with _patch_phase_tool(2):
        await loop2.step("next phase")
    snap = _snapshot("after phase 2 (post-restart)", loop2)
    assert snap["phase_idx"] == 3
    _assert_well_formed(snap["roles"])

    # On-disk log must show 3 phase_advance moves total (2 pre-restart + 1
    # post-restart). This is the load-bearing check for "the session is
    # actually durable across restarts."
    log = MoveLog(sid)
    advances = log.read(kind="phase_advance")
    advance_phases = [m["phase"] for m in advances]
    print(f"\n  on-disk phase_advance moves: {advance_phases}")
    assert advance_phases == ["setup_import_source", "setup_validate", "setup_infer"]


# ── flow 2: run all phases → restart → upstream resets the session ──────────


@pytest.mark.unit
def test_fully_completed_session_resets_on_recovery(fake_home):
    """The IndexError repro: a session that finished every phase, when
    rehydrated, used to land with `_phase_idx == len(_PHASE_SEQUENCE)` and
    `state == IDLE`. The first user message then crashed phase-advance
    bookkeeping with `list index out of range`.

    Upstream's fix is stronger than a state restore: hydration treats a
    completed session as 'start fresh' — writes a `session_completed`
    marker, resets `_phase_idx = 0`, and clears `_global_history`. This
    test pins that contract from the flow harness."""
    sid = "flow_full_pipeline"

    log = MoveLog(sid)
    for i, phase in enumerate(_PHASE_SEQUENCE):
        to_phase = _PHASE_SEQUENCE[i + 1] if i + 1 < len(_PHASE_SEQUENCE) else None
        log.append({"kind": "user", "phase": phase, "content": f"run {phase}"})
        log.append({"kind": "phase_advance", "phase": phase, "to_phase": to_phase})
        log.append({
            "kind": "assistant",
            "phase": to_phase if to_phase else phase,
            "content": f"{phase} wrap-up.",
        })
    print(f"\n  seeded {len(_PHASE_SEQUENCE)} phase_advances on disk for sid={sid!r}")

    loop = AgentLoop(
        llm=MagicMock(), blender=_make_blender(), session_id=sid,
    )
    snap = _snapshot("after restart (full pipeline)", loop)
    # Upstream contract: completed session becomes a fresh start.
    assert snap["phase_idx"] == 0
    assert snap["state"] == "idle"
    assert snap["history_len"] == 0
    assert snap["current_phase"] == _PHASE_SEQUENCE[0]
    markers = loop._move_log.read(kind="session_completed")
    assert len(markers) == 1, "session_completed marker should land on disk"
