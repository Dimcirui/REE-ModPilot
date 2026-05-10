"""
Hand-rolled ReAct agent loop (design decision C9).

State machine:

  IDLE ──(first message)──► RUNNING_PHASE
                                │  ▲
                    phase ok    │  │ retry
                                ▼  │
                            RUNNING_PHASE ──(phase 4+)──► NEGOTIATING ◄──┐
                                │                              │           │ correction
                          PhaseError                  proposal presented  │
                                ▼                              ▼           │
                         ERROR_HANDLING               AWAIT_CONFIRM ──────┘
                          │   │   │                        │
                        retry skip ask                  confirm
                                   │                       │
                               ASK_MODE              RUNNING_PHASE (execute)
                                                          │
                                              phase 6 done ▼
                                                        DONE

Phases 1-3 (fast batch, E22):
  LLM calls phase tools directly. global_history is the active history.

Phases 4+ (NEGOTIATING, E22/E23):
  Each NEGOTIATING phase opens an isolated phase_history seeded with the
  phase prompt. Handoff between phases passes only structured JSON, not history.
"""

from __future__ import annotations

import asyncio
import enum
import json
from typing import Any

from app.agent.error_handler import ErrorHandler
from app.agent.prompts import build_phase_prompt, build_system_prompt
from app.blender.client import BlenderClient
from app.blender.state import SceneCache
from app.llm.client import LLMClient, LLMResponse, Message
from app.phases.base import PhaseError, PhaseTool

# ── constants ──────────────────────────────────────────────────────────────

_PHASE_SEQUENCE: list[str] = [
    "setup_validate",   # SetupValidateScene
    "setup_import",     # SetupImportMHWilds
    "phase_1",
    "phase_2",
    "phase_3",
    "phase_35",
    "phase_4a",
    "phase_4b",
    "phase_5",
    "phase_6",
]

_NEGOTIATING_PHASES: frozenset[str] = frozenset(
    {"phase_35", "phase_4a", "phase_4b", "phase_5", "phase_6"}
)

_MAX_TOOL_ROUNDS = 8


# ── state enum ─────────────────────────────────────────────────────────────


class LoopState(enum.Enum):
    IDLE = "idle"
    RUNNING_PHASE = "running_phase"
    NEGOTIATING = "negotiating"
    AWAIT_CONFIRM = "await_confirm"
    ERROR_HANDLING = "error_handling"
    ASK_MODE = "ask_mode"
    DONE = "done"


# ── loop ───────────────────────────────────────────────────────────────────


class AgentLoop:
    """
    Hand-rolled ReAct loop for the ModPilot agent.

    Usage:
        loop = AgentLoop(llm=..., blender=..., physics_presets={...})
        reply = await loop.step("Let's start the mod workflow.")
    """

    def __init__(
        self,
        llm: LLMClient,
        blender: BlenderClient,
        physics_presets: dict | None = None,
    ) -> None:
        self._llm = llm
        self._blender = blender
        self._cache = SceneCache(blender)
        self._error_handler = ErrorHandler()

        self._phase_tools: dict[str, PhaseTool] = {}
        self._register_available_phases()

        # State
        self.state = LoopState.IDLE
        self._phase_idx: int = 0
        self._pending_error: PhaseError | None = None
        self._skipped_phases: set[str] = set()

        # global_history: full conversation (all turns).
        # phase_history:  isolated per NEGOTIATING phase; reset at each phase boundary.
        self._global_history: list[Message] = []
        self._phase_history: list[Message] = []

        self._system_prompt = build_system_prompt(physics_presets)

    # ── public ────────────────────────────────────────────────────────────

    @property
    def current_phase(self) -> str | None:
        if self._phase_idx >= len(_PHASE_SEQUENCE):
            return None
        return _PHASE_SEQUENCE[self._phase_idx]

    async def step(self, user_message: str) -> str:
        """Process one user turn. Returns the agent reply string."""
        self._global_history.append({"role": "user", "content": user_message})
        reply = await self._dispatch(user_message)
        self._global_history.append({"role": "assistant", "content": reply})
        return reply

    # ── dispatch ──────────────────────────────────────────────────────────

    async def _dispatch(self, user_message: str) -> str:
        match self.state:
            case LoopState.IDLE:
                self.state = LoopState.RUNNING_PHASE
                return await self._run_react_turn()
            case LoopState.RUNNING_PHASE:
                return await self._run_react_turn()
            case LoopState.NEGOTIATING:
                return await self._run_negotiating_turn(user_message)
            case LoopState.AWAIT_CONFIRM:
                return await self._handle_await_confirm(user_message)
            case LoopState.ERROR_HANDLING:
                return await self._handle_error_choice(user_message)
            case LoopState.ASK_MODE:
                return await self._handle_ask_mode(user_message)
            case LoopState.DONE:
                return "All phases are complete. The mod export is finished."

    # ── ReAct turn (phases 1-3) ───────────────────────────────────────────

    async def _run_react_turn(self) -> str:
        """
        Drive the LLM in a tool-call loop until it produces a text-only response.
        Uses global_history as the active conversation context.
        Returns the final text reply.
        """
        history = self._global_history
        tools = self._build_tool_list()

        for _ in range(_MAX_TOOL_ROUNDS):
            response = await asyncio.to_thread(
                self._llm.chat,
                history,
                system=self._system_prompt,
                tools=tools if tools else None,
            )

            if not response.has_tool_calls:
                return response.content

            # Append assistant message with tool_use blocks
            history.append(self._build_assistant_tool_msg(response))

            # Execute each tool call; collect results
            tool_results: list[dict[str, Any]] = []
            error_reply: str | None = None

            for tc in response.tool_calls:
                result_text, error_reply = await self._execute_tool_call(tc)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": result_text,
                    }
                )
                if error_reply or self.state != LoopState.RUNNING_PHASE:
                    break

            history.append({"role": "user", "content": tool_results})

            if error_reply:
                return error_reply

            if self.state != LoopState.RUNNING_PHASE:
                # State changed (e.g., DONE or NEGOTIATING). Let LLM produce a
                # transition summary without tool calls.
                final = await asyncio.to_thread(
                    self._llm.chat,
                    history,
                    system=self._system_prompt,
                )
                return final.content

        return "Reached the maximum number of tool-call rounds. Please try again."

    async def _execute_tool_call(self, tc: dict) -> tuple[str, str | None]:
        """
        Execute one LLM tool call.

        Returns (tool_result_text, error_reply).
        error_reply is non-None when the phase fails; it is the formatted
        user-facing error message and should be returned from step() directly.
        """
        tool_name = tc["name"]
        params = tc.get("input", {})

        phase = self._phase_tools.get(tool_name)
        if phase is None:
            return f"Tool '{tool_name}' is not yet available in this version.", None

        result = await asyncio.to_thread(
            phase.run, self._blender, self._cache, params
        )

        if result.success:
            completed = _PHASE_SEQUENCE[self._phase_idx]
            self._phase_idx += 1
            self._on_phase_advance()
            diff = (
                json.dumps(result.state_diff, ensure_ascii=False)
                if result.state_diff
                else "no scene changes"
            )
            return f"Phase {completed} completed. Scene diff: {diff}", None
        else:
            self._pending_error = result.error
            self.state = LoopState.ERROR_HANDLING
            error_reply = await asyncio.to_thread(
                self._error_handler.format, result.error, self._llm
            )
            return f"Phase failed: {result.error.message}", error_reply

    def _on_phase_advance(self) -> None:
        """Update state after a phase completes successfully."""
        if self._phase_idx >= len(_PHASE_SEQUENCE):
            self.state = LoopState.DONE
            return
        next_phase = _PHASE_SEQUENCE[self._phase_idx]
        if next_phase in _NEGOTIATING_PHASES:
            self.state = LoopState.NEGOTIATING
            self._phase_history = []

    # ── NEGOTIATING turn (phases 4+) ──────────────────────────────────────

    async def _run_negotiating_turn(self, user_message: str) -> str:
        """
        One NEGOTIATING turn: inject phase prompt on first entry, add user message,
        call LLM with phase_history, detect proposal → transition to AWAIT_CONFIRM.
        """
        # First turn: seed phase_history with phase instructions
        if not self._phase_history:
            phase_prompt = build_phase_prompt(self.current_phase or "")
            if phase_prompt:
                self._phase_history.append(
                    {
                        "role": "user",
                        "content": f"[Phase instructions]\n{phase_prompt}",
                    }
                )
                self._phase_history.append(
                    {
                        "role": "assistant",
                        "content": "Understood. I will follow these phase instructions.",
                    }
                )

        self._phase_history.append({"role": "user", "content": user_message})

        response = await asyncio.to_thread(
            self._llm.chat,
            self._phase_history,
            system=self._system_prompt,
        )

        reply = response.content
        self._phase_history.append({"role": "assistant", "content": reply})

        # Detect structured proposal from LLM (propose_and_confirm protocol)
        if '"requires_user_review": true' in reply or '"requires_user_review":true' in reply:
            self.state = LoopState.AWAIT_CONFIRM

        return reply

    async def _handle_await_confirm(self, user_message: str) -> str:
        """
        User replied to a proposal. Both confirm and correction re-enter NEGOTIATING:
        the LLM handles the distinction from the message content.
        """
        self.state = LoopState.NEGOTIATING
        return await self._run_negotiating_turn(user_message)

    # ── error handling ────────────────────────────────────────────────────

    async def _handle_error_choice(self, user_message: str) -> str:
        """Route user's [Retry] / [Skip] / [Ask] choice after a phase failure."""
        choice = self._error_handler.parse_user_choice(user_message)

        match choice:
            case "retry":
                self.state = LoopState.RUNNING_PHASE
                self._pending_error = None
                return await self._run_react_turn()

            case "skip":
                skipped = self.current_phase or "unknown"
                self._skipped_phases.add(skipped)
                self._phase_idx += 1
                self._on_phase_advance()
                self._pending_error = None
                warning = (
                    f"Skipping {skipped}. "
                    "Warning: downstream phases may fail if they depend on this step."
                )
                if self.state == LoopState.DONE:
                    return warning + " All other phases are complete."
                return warning + f" Continuing to {self.current_phase}."

            case "ask":
                self.state = LoopState.ASK_MODE
                response = await asyncio.to_thread(
                    self._llm.chat,
                    self._global_history,
                    system=self._system_prompt,
                )
                return response.content

            case _:
                return (
                    "Please choose one of the options: "
                    "[Retry] to try again, [Skip] to skip this phase, "
                    "[Ask] for more information about the error."
                )

    async def _handle_ask_mode(self, user_message: str) -> str:
        """
        Free Q&A mode: LLM answers without calling any tools (A2).
        Exits back to ERROR_HANDLING when user mentions retry/skip/continue.

        Raw error details from _pending_error are injected into the system
        prompt so the LLM can explain the actual failure, not guess at it.
        """
        system = self._system_prompt
        if self._pending_error:
            err = self._pending_error
            detail_lines = [
                "\n\n[PENDING ERROR — use this to answer the user's question]",
                f"operator: {err.operator}",
                f"message: {err.message}",
            ]
            if err.suggestion:
                detail_lines.append(f"suggestion: {err.suggestion}")
            if err.raw:
                detail_lines.append(f"raw_output: {err.raw}")
            system = system + "\n".join(detail_lines)

        response = await asyncio.to_thread(
            self._llm.chat,
            self._global_history,
            system=system,
            # No tools passed — pure explanation mode
        )
        exit_keywords = ("continue", "retry", "skip", "back", "继续", "重试", "跳过", "返回")
        if any(kw in user_message.lower() for kw in exit_keywords):
            self.state = LoopState.ERROR_HANDLING
        return response.content

    # ── helpers ───────────────────────────────────────────────────────────

    def _register_available_phases(self) -> None:
        from app.phases.pose_correction import PoseCorrection
        from app.phases.setup import SetupImportMHWilds, SetupValidateScene
        from app.phases.skeleton_align import SkeletonAlign
        from app.phases.vertex_groups import VertexGroups

        for phase in (
            SetupValidateScene(),
            SetupImportMHWilds(),
            PoseCorrection(),
            SkeletonAlign(),
            VertexGroups(),
        ):
            self._phase_tools[phase.name] = phase

    def _build_tool_list(self) -> list[dict]:
        return [p.tool_schema() for p in self._phase_tools.values()]

    @staticmethod
    def _build_assistant_tool_msg(response: LLMResponse) -> Message:
        """Build an Anthropic-format assistant message containing tool_use blocks.

        Uses response.content_blocks when available so that opaque blocks
        (e.g. thinking) are round-tripped verbatim and not stripped out.
        """
        if response.content_blocks:
            return {"role": "assistant", "content": response.content_blocks}
        # Fallback for providers that do not populate content_blocks (OpenAI).
        content: list[dict[str, Any]] = []
        if response.content:
            content.append({"type": "text", "text": response.content})
        for tc in response.tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                }
            )
        return {"role": "assistant", "content": content}
