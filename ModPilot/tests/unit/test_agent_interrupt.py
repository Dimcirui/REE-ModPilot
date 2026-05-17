"""
Unit tests for the user-interrupt mechanism (issue #14).

The contract:
  - AgentLoop exposes a public `interrupt()` that flips a private flag and
    emits one `interrupted` SSE event.
  - The flag is checked between rounds of `_run_react_turn` (and at the top of
    each inner tool-call iteration) so an in-flight phase bails out without
    leaving orphan `tool_use` blocks in history.
  - On bail-out the loop transitions to IDLE, clears the flag, and returns a
    short user-facing reply.
  - POST /agent/interrupt/{session_id} on the FastAPI route layer surfaces
    `loop.interrupt()` to the frontend; returns 404 for unknown sessions.

Run with: uv run pytest -m unit tests/unit/test_agent_interrupt.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore

from app.agent.loop import AgentLoop, LoopState
from app.phases.base import PhaseResult


# ── helpers (mirrors test_agent_loop_events.py) ────────────────────────────


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


# ── AgentLoop.interrupt() + flag ───────────────────────────────────────────


@pytest.mark.unit
def test_init_starts_with_flag_cleared():
    loop = _make_loop(sink=[])
    assert loop._interrupted is False


@pytest.mark.unit
def test_interrupt_sets_flag_and_emits_event():
    events: list[dict] = []
    loop = _make_loop(sink=events)
    loop.interrupt()
    assert loop._interrupted is True
    matched = [e for e in events if e["type"] == "interrupted"]
    assert len(matched) == 1


@pytest.mark.unit
def test_interrupt_is_idempotent():
    """Calling interrupt() twice should not raise nor emit a second event."""
    events: list[dict] = []
    loop = _make_loop(sink=events)
    loop.interrupt()
    loop.interrupt()
    assert loop._interrupted is True
    matched = [e for e in events if e["type"] == "interrupted"]
    assert len(matched) == 1


# ── _run_react_turn bail-out semantics ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_interrupt_before_step_short_circuits():
    """Setting the flag before step() yields an immediate bail-out with state=IDLE."""
    events: list[dict] = []
    loop = _make_loop(sink=events)
    loop.state = LoopState.RUNNING_PHASE
    loop.interrupt()
    reply = await loop.step("anything")
    assert loop.state == LoopState.IDLE
    assert "interrupt" in reply.lower()
    # Flag should reset so the next user message is not silently swallowed.
    assert loop._interrupted is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_interrupt_during_first_round_bails_after_tools_drained():
    """If the flag is flipped during the LLM call of round 1, the round
    finishes draining tool_results into history (so no orphan tool_use), then
    the loop bails before a second round."""

    captured: dict = {"loop": None}

    def chat_side_effect(*args, **kwargs):
        # Simulate Escape pressed concurrently with the first LLM call.
        if captured["loop"] is not None and not captured["loop"]._interrupted:
            captured["loop"].interrupt()
        return MagicMock(
            content="",
            has_tool_calls=True,
            tool_calls=[
                {
                    "id": "t1",
                    "name": "pose_correction",
                    "input": {
                        "x_preset": "MMD",
                        "source_armature": "Body",
                        "target_armature": "MHWs",
                    },
                }
            ],
            content_blocks=[],
        )

    llm = MagicMock()
    llm.chat.side_effect = chat_side_effect
    blender = MagicMock()
    blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

    events: list[dict] = []
    loop = _make_loop(sink=events, llm=llm, blender=blender)
    captured["loop"] = loop
    loop.state = LoopState.RUNNING_PHASE

    from app.phases.pose_correction import PoseCorrection
    with patch.object(PoseCorrection, "run", return_value=PhaseResult.ok({"k": 1})):
        reply = await loop.step("run phase 1")

    # Only one LLM call — the bail-out happened before a second round.
    assert llm.chat.call_count == 1
    # No orphan tool_use: locate the assistant message that carried tool_use
    # blocks and verify the very next history entry is a user message whose
    # tool_result blocks cover every tool_use id.  (step() also appends a
    # plain-text assistant reply after the bail-out, so we can't just look at
    # the tail.)
    history = loop._global_history
    asst_idx = None
    for i, msg in enumerate(history):
        if msg["role"] == "assistant" and isinstance(msg["content"], list):
            if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in msg["content"]):
                asst_idx = i
                break
    assert asst_idx is not None, "expected an assistant tool_use message in history"
    tool_use_ids = [
        b["id"] for b in history[asst_idx]["content"]
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    follow = history[asst_idx + 1]
    assert follow["role"] == "user"
    tool_result_ids = [
        b["tool_use_id"] for b in follow["content"]
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert set(tool_use_ids).issubset(set(tool_result_ids))

    assert loop.state == LoopState.IDLE
    assert "interrupt" in reply.lower()
    assert loop._interrupted is False


# ── POST /agent/interrupt/{session_id} ─────────────────────────────────────


def _patch_blender(monkeypatch):
    from app.blender.client import BlenderClient
    monkeypatch.setattr(BlenderClient, "connect", lambda self: None)
    monkeypatch.setattr(BlenderClient, "connected", property(lambda self: True))
    monkeypatch.setattr(BlenderClient, "close", lambda self: None)


def _patch_llm(monkeypatch):
    from app.llm.client import LLMClient
    monkeypatch.setattr(
        LLMClient, "from_settings", classmethod(lambda cls: MagicMock())
    )


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_interrupt_route_calls_interrupt_on_existing_session(monkeypatch):
    _patch_blender(monkeypatch)
    _patch_llm(monkeypatch)

    from app.main import app

    with TestClient(app) as client:
        fake = MagicMock(spec=AgentLoop)
        app.state.agent_sessions["sid_for_test"] = fake
        resp = client.post("/agent/interrupt/sid_for_test")

    assert resp.status_code == 200
    fake.interrupt.assert_called_once()
    body = resp.json()
    assert body["session_id"] == "sid_for_test"
    assert body["interrupted"] is True


@pytest.mark.unit
@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient unavailable")
def test_interrupt_route_returns_404_for_unknown_session(monkeypatch):
    _patch_blender(monkeypatch)
    _patch_llm(monkeypatch)

    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/agent/interrupt/never_existed_sid")

    assert resp.status_code == 404
