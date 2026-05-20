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
import logging
import time
from collections.abc import Callable
from typing import Any

from app.agent.dsml import (
    looks_like_dsml,
    parse_dsml_tool_calls,
    sanitize_outbound,
    strip_dsml_block,
)

logger = logging.getLogger(__name__)
from app.agent.error_handler import ErrorHandler
from app.agent.history import (
    COMPACT_MARKER,
    QUERY_HISTORY_DEFAULT_LAST_N,
    QUERY_HISTORY_MAX_LAST_N,
    QUERY_HISTORY_TOOL_NAME,
    QUERY_HISTORY_TOOL_SCHEMA,
    MoveLog,
    compact_phase_range,
)
from app.agent.history_heal import heal_history
from app.agent.prompts import build_phase_prompt, build_system_prompt
from app.blender.client import BlenderClient
from app.blender.state import SceneCache
from app.llm.client import LLMClient, LLMResponse, Message
from app.phases.base import PhaseError, PhaseResult, PhaseTool
from app.phases.material import suggest_texture_mapping
from app.phases.physics_annotate import annotate_chains
from app.phases.query_tools import QueryTool

# ── constants ──────────────────────────────────────────────────────────────

_PHASE_SEQUENCE: list[str] = [
    "setup_import_source",  # SetupImportSource — FBX import from session.model_path
    "setup_validate",   # SetupValidateScene
    "setup_infer",      # InferModelType (issue #4 — auto-detect source model preset)
    "setup_import",     # SetupImportMHWilds
    "phase_1",
    "phase_2",
    "phase_3",
    "phase_35",
    "phase_4a",
    "phase_4b",
    "phase_5",
    "phase_6",          # BatchExport runs RE Mesh Tools cleanup internally before export
]

_NEGOTIATING_PHASES: frozenset[str] = frozenset()
# All phases run in RUNNING_PHASE so the LLM always has tool schemas visible.
# NEGOTIATING gives the LLM zero tools, causing it to ask users to operate
# Blender manually instead of calling the registered tools.

# Meta-tool: no Blender call; updates _phase_idx and emits phase sync events.
_SYNC_PHASE_TOOL_SCHEMA: dict = {
    "name": "sync_phase_state",
    "description": (
        "Sync the frontend phase progress tracker after a session resume or browser reload. "
        "Call this once you have determined the current phase from scene inspection. "
        "It marks all preceding phases as completed and the given phase as active. "
        "Do NOT call this when advancing phases normally — use phase tools for that."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "current_phase": {
                "type": "string",
                "description": (
                    "The phase currently in progress. "
                    f"Valid values: {_PHASE_SEQUENCE}"
                ),
            },
        },
        "required": ["current_phase"],
    },
}

_MAX_TOOL_ROUNDS = 15

# Appended to the system prompt for the Issue #15 wrap-up llm.chat call only.
# Without this, the model often writes "let me check" / "让我确认一下" at the end
# of a paused turn, which misleads the user into thinking work will continue —
# but the loop has already returned and is awaiting the next user message.
# This instruction makes the model treat the wrap-up as a true turn-end: report
# what just happened, then either ask a concrete question OR state the next
# checkpoint, but never promise further unilateral action.
_WRAP_UP_SYSTEM_ADDENDUM = (
    "\n\n## TURN-END WRAP-UP\n"
    "This is the FINAL message of the current turn. Tools are NOT available "
    "in this response. After you reply, the loop returns control to the user "
    "and waits for their next message.\n\n"
    "Therefore:\n"
    "1. Report what was just accomplished by the tool call(s) you made earlier "
    "   in this turn — concisely.\n"
    "2. Do NOT write anticipatory phrases like '让我检查一下' / '我接下来会...' / "
    "   'let me verify' / 'I will now...'. You will not get to act on them; "
    "   they only mislead the user into thinking the agent is still working.\n"
    "3. End the message with EITHER:\n"
    "   (a) a specific yes/no or multiple-choice question for the user, OR\n"
    "   (b) a clear statement that the phase is complete and what the next "
    "       checkpoint is (e.g. 'Phase 2 done. Say \"继续\" to start Phase 3.').\n"
    "4. If the user must visually inspect the viewport, name exactly what they "
    "   should look at — don't just say '请检查视口'."
)

_MAX_ASK_ROUNDS = 6  # raised from 3: user-facing ASK_MODE often needs 4-5 queries
                     # for a thorough diagnosis (list_objects + scene_info +
                     # get_mesh_info + get_material_info + a follow-up call).
_MAX_QUERY_ONLY_ROUNDS = 2  # after this many consecutive query-only rounds, drop query tools


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
        event_sink: Callable[[dict], None] | None = None,
        session_config: dict | None = None,
        session_id: str | None = None,
    ) -> None:
        self._llm = llm
        self._blender = blender
        self._cache = SceneCache(blender)
        self._error_handler = ErrorHandler()
        self._event_sink = event_sink
        self._session_config = session_config or {}

        # Off-prompt move log + phase-boundary compaction state.
        # session_id is optional: existing tests that don't care about
        # persistence pass nothing and disable the whole subsystem.
        self._move_log: MoveLog | None = MoveLog(session_id) if session_id else None
        # _phase_start_idx_global tracks the index in _global_history where the
        # current phase's messages begin. Updated on every compaction so the
        # next compact_phase_range call collapses only the new phase's span.
        self._phase_start_idx_global: int = 0
        # Set when _execute_tool_call advances _phase_idx; consumed at the top
        # of the NEXT _run_react_turn to trigger compaction. Cleared after.
        self._just_completed_phase: str | None = None

        self._phase_tools: dict[str, PhaseTool] = {}
        self._register_available_phases()

        # State
        self.state = LoopState.IDLE
        self._phase_idx: int = 0
        self._pending_error: PhaseError | None = None
        self._skipped_phases: set[str] = set()
        # Issue 3 — deferred widget emit.  Inspector tools (physics_classification,
        # material_inspect) used to emit their confirmation widget right when the
        # tool returned, which surfaced an empty-looking table to the user BEFORE
        # the LLM had a chance to comment on the result in chat.  We now stash the
        # widget payload here on tool return and emit only after the LLM's next
        # text-only response — so the chat-side analysis lands first, then the
        # widget appears alongside the proposal.
        self._pending_widget: tuple[str, dict] | None = None

        # Issue #14 — user-interrupt flag.  Set by AgentLoop.interrupt() from the
        # FastAPI route layer (Escape key in the frontend → POST /agent/interrupt).
        # Checked between rounds of _run_react_turn and between tool calls within
        # a round, so a long phase bails out without leaving orphan tool_use
        # blocks in history.  Cleared by the bail-out branch.
        self._interrupted: bool = False

        # Issue #15 — inter-phase pause flag.  Flipped True in `_execute_tool_call`
        # right after a phase-advancing tool succeeds; `_run_react_turn` then
        # breaks the tool-call loop, runs ONE tools=None wrap-up llm.chat to get
        # a completion report, and returns that text to the user.  Reset at the
        # top of every step() and right after the wrap-up branch fires.  The
        # rail enforces the Phase Transition Protocol from agent_workflow.md
        # even when the LLM ignores the prompt-level rule.
        self._phase_just_advanced: bool = False
        # Records `tool.requires_user_pause` for the tool that just flipped
        # _phase_just_advanced=True. When False, the wrap-up branch skips the
        # pause and lets the loop chain into the next tool in the same turn
        # (used for mechanical setup tools). Reset alongside _phase_just_-
        # advanced. Default True preserves the historical pause behavior.
        self._last_tool_requires_pause: bool = True

        # global_history: full conversation (all turns).
        # phase_history:  isolated per NEGOTIATING phase; reset at each phase boundary.
        self._global_history: list[Message] = []
        self._phase_history: list[Message] = []

        self._system_prompt = build_system_prompt(physics_presets, session_config)

        # Session recovery: if a move log already exists on disk for this
        # session_id, rebuild _phase_idx and a phase-granular _global_history
        # from it. Mid-phase detail is intentionally NOT replayed — the agent
        # re-queries Blender for live scene state and uses `query_history`
        # for past decisions. See _hydrate_from_move_log for the contract.
        self._hydrate_from_move_log()

    def _emit(self, event_type: str, **payload: Any) -> None:
        """Publish one structured event to the sink, if any.

        The sink must be thread-safe — emits may originate from threadpool
        workers when tools run via asyncio.to_thread. Sink installers in the
        route layer wrap a queue.put with loop.call_soon_threadsafe.
        """
        if self._event_sink is None:
            return
        # Single-chokepoint defense against DSML markup leaking to the UI.
        # Per-branch strippers may miss new variants; this one scrubs any
        # DSML-ish residue on every outbound message event.
        if event_type == "message":
            raw = payload.get("content")
            if isinstance(raw, str) and looks_like_dsml(raw):
                cleaned = sanitize_outbound(raw)
                if cleaned != raw:
                    logger.warning(
                        "DSML markup leaked to outbound message — sanitized at emit. "
                        "role=%s len_before=%d len_after=%d",
                        payload.get("role"),
                        len(raw),
                        len(cleaned),
                    )
                payload = {**payload, "content": cleaned}
        evt: dict[str, Any] = {
            "type": event_type,
            "ts": time.time(),
            "phase": self.current_phase,
            "state": self.state.value,
            **payload,
        }
        self._event_sink(evt)

    # ── public ────────────────────────────────────────────────────────────

    def interrupt(self) -> None:
        """Request graceful bail-out of an in-flight phase (issue #14).

        Safe to call from a concurrent FastAPI route handler — flipping a bool
        is atomic under the GIL.  `_run_react_turn` polls the flag between
        rounds and between tool calls within a round, then transitions to
        IDLE.  Idempotent: a second call while the flag is still set is a no-op
        (no duplicate `interrupted` event).
        """
        if self._interrupted:
            return
        self._interrupted = True
        self._emit("interrupted")
        self._log_move({"kind": "interrupt", "phase": self.current_phase})

    def _log_move(self, move: dict[str, Any]) -> None:
        """Append a move to the persistent log if one is configured.

        No-op when session_id was not supplied at construction. All log
        writes go through this helper so the None-check lives in one place.
        """
        if self._move_log is not None:
            self._move_log.append(move)

    def _classify_user_move_kind(self, user_message: str) -> str:
        """Map an incoming user message to the right `kind` field for the log.

        Widget-confirm and error-choice prefixes are protocol-level, not
        free-form chat. Surfacing them as distinct kinds lets the LLM
        (via query_history) page through past widget submissions and
        retry/skip decisions without first parsing every user message.
        """
        if self.state == LoopState.ERROR_HANDLING:
            return "error_choice"
        if user_message.startswith("[CONFIRMED_CLASSIFICATIONS]") or user_message.startswith(
            "[CONFIRMED_MATERIAL_MAPPING]"
        ):
            return "widget"
        return "user"

    @property
    def current_phase(self) -> str | None:
        if self._phase_idx >= len(_PHASE_SEQUENCE):
            return None
        return _PHASE_SEQUENCE[self._phase_idx]

    async def step(self, user_message: str) -> str:
        """Process one user turn. Returns the agent reply string."""
        # Issue #15: defensive reset.  The wrap-up branch in _run_react_turn
        # already clears this flag; reset here too so a leaked True from a
        # prior turn can't short-circuit the new one.
        self._phase_just_advanced = False
        self._last_tool_requires_pause = True
        # Classify BEFORE appending — _classify_user_move_kind reads self.state,
        # and _dispatch may transition state before the assistant reply lands.
        user_kind = self._classify_user_move_kind(user_message)
        self._global_history.append({"role": "user", "content": user_message})
        self._emit("message", role="user", content=user_message)
        self._log_move({
            "kind": user_kind,
            "phase": self.current_phase,
            "content": user_message,
        })
        try:
            reply = await self._dispatch(user_message)
            self._global_history.append({"role": "assistant", "content": reply})
            self._emit("message", role="assistant", content=reply)
            self._log_move({
                "kind": "assistant",
                "phase": self.current_phase,
                "content": reply,
            })
            return reply
        finally:
            # Issue 3: deferred widget emit.  Inspector tools stash their
            # widget payload on self._pending_widget during _dispatch; emit
            # it HERE so the SSE order is:
            #   user msg → tool_call → tool_result → assistant msg → widget
            # Putting the emit in step's finally guarantees that the assistant
            # message bubble lands BEFORE the table, and that the widget still
            # surfaces even if _dispatch raises mid-flight.
            self._flush_pending_widget()

    # ── dispatch ──────────────────────────────────────────────────────────

    async def _dispatch(self, user_message: str) -> str:
        match self.state:
            case LoopState.IDLE:
                self.state = LoopState.RUNNING_PHASE
                self._emit("state", state=self.state.value)
                if self.current_phase is not None:
                    self._emit(
                        "phase_started",
                        phase=self.current_phase,
                        index=self._phase_idx,
                        total=len(_PHASE_SEQUENCE),
                    )
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

        Query-tool throttle: after _MAX_QUERY_ONLY_ROUNDS consecutive rounds where
        every call was a query tool (scene_info, list_objects, etc.), the tool list
        is restricted to phase tools only. This forces the LLM to commit to a phase
        tool call instead of indefinitely querying scene state.
        """
        # Phase-boundary compaction (context management). If the previous turn
        # ended with a phase advance, the now-stale tool_use / tool_result /
        # narration / wrap-up messages still sit in _global_history and will
        # be sent to the LLM on every subsequent call. Collapse that span to
        # a single summary message reusing the wrap-up text the LLM already
        # produced for the user. Detail remains queryable via the
        # `query_history` tool — the on-disk MoveLog is the ground truth.
        self._compact_completed_phase_if_pending()

        history = self._global_history
        query_tool_names = {
            name for name, t in self._phase_tools.items() if isinstance(t, QueryTool)
        }
        query_only_rounds = 0  # consecutive rounds with only query tool calls

        # Issue #14 — short-circuit if interrupt() was called before step entry.
        if self._interrupted:
            return self._handle_interrupt_bailout()

        for _ in range(_MAX_TOOL_ROUNDS):
            # Issue #14 — between-round interrupt check.  Tool_use/tool_result
            # pairs from any previous round are already balanced in history at
            # this point, so bailing out here cannot leave orphan blocks.
            if self._interrupted:
                return self._handle_interrupt_bailout()

            # Restrict to phase tools after too many query-only rounds
            if query_only_rounds >= _MAX_QUERY_ONLY_ROUNDS:
                tools = self._build_phase_only_tool_list()
            else:
                tools = self._build_tool_list()

            # Defensive: backstop any unmatched assistant tool_use blocks
            # before sending to the LLM API (Anthropic 400 on mismatched ids).
            heal_history(history)

            response = await asyncio.to_thread(
                self._llm.chat,
                history,
                system=self._system_prompt,
                tools=tools if tools else None,
            )

            if not response.has_tool_calls:
                content = response.content
                # DeepSeek fallback: DSML markup in text instead of API tool_calls.
                # Parse the markup directly and execute — retrying doesn't help
                # because DeepSeek repeatedly outputs markup when in thinking mode.
                dsml_calls = parse_dsml_tool_calls(content)
                if dsml_calls:
                    clean = strip_dsml_block(content)
                    history.append({
                        "role": "assistant",
                        "content": clean or "[tool call via inline markup]",
                    })
                    all_query = all(c["name"] in query_tool_names for c in dsml_calls)
                    query_only_rounds = query_only_rounds + 1 if all_query else 0
                    tool_results_text: list[str] = []
                    error_reply: str | None = None
                    for tc in dsml_calls:
                        result_text, error_reply = await self._execute_tool_call(tc)
                        tool_results_text.append(result_text)
                        if error_reply or self.state != LoopState.RUNNING_PHASE:
                            break
                    history.append({"role": "user", "content": "\n".join(tool_results_text)})
                    if error_reply:
                        return error_reply
                    # Issue #15 — same pause rule applies to the DSML branch.
                    # Honor per-tool `requires_user_pause`: mechanical setup
                    # tools chain straight into the next tool without a wrap-up.
                    if self.state != LoopState.RUNNING_PHASE or (
                        self._phase_just_advanced and self._last_tool_requires_pause
                    ):
                        self._phase_just_advanced = False
                        self._last_tool_requires_pause = True
                        final = await asyncio.to_thread(
                            self._llm.chat,
                            history,
                            system=self._system_prompt + _WRAP_UP_SYSTEM_ADDENDUM,
                        )
                        return final.content
                    # No-pause path: keep _phase_just_advanced=True false-ified
                    # so we don't re-enter wrap-up next iteration on this same
                    # advance, but reset the per-tool flag for the next call.
                    self._phase_just_advanced = False
                    self._last_tool_requires_pause = True
                    continue
                # Detect propose-and-confirm proposal (may occur in RUNNING_PHASE
                # when LLM classifies chains before calling a tool).
                if (
                    '"requires_user_review": true' in content
                    or '"requires_user_review":true' in content
                ):
                    self.state = LoopState.AWAIT_CONFIRM
                    self._emit("state", state=self.state.value)
                return content

            # Append assistant message with tool_use blocks
            history.append(self._build_assistant_tool_msg(response))

            # Track whether this round was query-tools-only
            all_query = all(tc["name"] in query_tool_names for tc in response.tool_calls)
            query_only_rounds = query_only_rounds + 1 if all_query else 0

            # Execute each tool call; collect results.
            # try/finally guarantees tool_results are ALWAYS appended to history,
            # even if _execute_tool_call raises (e.g. error_handler LLM call fails).
            # An unmatched tool_use in history causes a 400 on the next API call.
            tool_results: list[dict[str, Any]] = []
            error_reply: str | None = None

            try:
                for tc in response.tool_calls:
                    try:
                        result_text, error_reply = await self._execute_tool_call(tc)
                    except Exception as exc:
                        result_text = f"Tool execution raised unexpectedly: {exc}"
                        error_reply = f"Unexpected error during {tc.get('name', '?')}: {exc}"
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": result_text,
                        }
                    )
                    if (
                        error_reply
                        or self.state != LoopState.RUNNING_PHASE
                        or self._interrupted  # issue #14 — bail mid-round
                    ):
                        break
            finally:
                # Fill placeholder tool_results for any tool_use IDs not yet executed.
                # The Anthropic API requires every tool_use block in an assistant message
                # to have a matching tool_result in the immediately following user message.
                # When a tool failure triggers `break`, remaining IDs would be left unmatched.
                executed_ids = {tr["tool_use_id"] for tr in tool_results}
                for tc in response.tool_calls:
                    if tc["id"] not in executed_ids:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tc["id"],
                                "content": "Skipped — a preceding tool call in this round failed.",
                            }
                        )
                history.append({"role": "user", "content": tool_results})

            if error_reply:
                return error_reply

            # Issue #14 takes priority over the issue #15 phase-advance pause —
            # an interrupted user does not want to wait through a wrap-up LLM
            # call.  Drain happened above so history is balanced.
            if self._interrupted:
                return self._handle_interrupt_bailout()

            if self.state != LoopState.RUNNING_PHASE or (
                self._phase_just_advanced and self._last_tool_requires_pause
            ):
                # Three cases that all funnel into the same wrap-up:
                #   1. State changed (DONE / NEGOTIATING / ERROR_HANDLING / AWAIT_CONFIRM)
                #   2. Issue #15 — phase-advancing tool succeeded AND the tool
                #      opted in to a user pause; tools that opt out (mechanical
                #      setup steps) chain straight into the next tool below.
                # In both cases ask the LLM for a text-only completion report
                # (tools=None), return it as the assistant reply, and let the
                # next user message re-enter the loop.
                self._phase_just_advanced = False
                self._last_tool_requires_pause = True
                heal_history(history)
                final = await asyncio.to_thread(
                    self._llm.chat,
                    history,
                    system=self._system_prompt + _WRAP_UP_SYSTEM_ADDENDUM,
                )
                return final.content
            # No-pause path: an opted-out phase tool just advanced. Clear the
            # flags so the next iteration's tool_use call is unrestricted.
            self._phase_just_advanced = False
            self._last_tool_requires_pause = True

        return "Reached the maximum number of tool-call rounds. Please try again."

    def _hydrate_from_move_log(self) -> None:
        """Rebuild _phase_idx and _global_history from the on-disk move log.

        Called once at the end of __init__. Idempotent and no-op when no log
        exists (cold-start new session).

        Recovery is intentionally phase-granular, not turn-granular:

          - `_phase_idx` ← count of `phase_advance` moves in the log
          - For each `phase_advance`, emit a (user_trigger, assistant_summary)
            PAIR into `_global_history`. The user trigger is the most recent
            user / widget / error_choice message logged for that phase (the
            request that drove the phase). When none was logged (cold backend
            crash, sync_phase_state recovery, etc.) we fall back to a
            synthetic "(continue)" placeholder. The assistant half is the
            first `assistant` move logged AFTER the advance; if missing,
            a generic "Phase X completed." string.
          - Emitting pairs (rather than assistant-only) is what keeps the
            rebuilt history alternating user→assistant→user→…, which the
            Anthropic API requires. A history starting with assistant — or
            with two assistant blocks back-to-back — 400s on the next call.
          - `_phase_start_idx_global` ← `len(_global_history)` so the next
            real turn starts a fresh span
          - Mid-phase work (tool moves without a following phase_advance) is
            NOT replayed into history. The agent recovers live scene state
            via Blender query tools next turn; past decisions are reachable
            via `query_history`.

        Why phase-granular: faithfully replaying tool_use/tool_result blocks
        requires preserving the exact tool_use_id strings the LLM generated.
        Anthropic API 400s on any id mismatch, so reconstructing them is
        fragile. Compacted summaries are flat strings and align with the
        steady-state shape of live `_global_history` after compaction runs.
        """
        if self._move_log is None:
            return
        moves = self._move_log.read()
        if not moves:
            return

        # Walk linearly so we can capture the trigger user msg seen most
        # recently within each phase before its phase_advance fires.
        pending_user_for_phase: dict[str, str] = {}
        for i, move in enumerate(moves):
            kind = move.get("kind")
            if kind in ("user", "widget", "error_choice"):
                phase = move.get("phase")
                content = move.get("content")
                if phase and isinstance(content, str) and content.strip():
                    pending_user_for_phase[phase] = content
                continue
            if kind != "phase_advance":
                continue
            completed = move.get("phase") or "unknown"
            # Find the first assistant move after this advance.
            summary = f"Phase {completed} completed."
            for follow in moves[i + 1 :]:
                if follow.get("kind") == "assistant":
                    content = follow.get("content")
                    if isinstance(content, str) and content.strip():
                        summary = content
                    break
            trigger = pending_user_for_phase.pop(completed, "(continue)")
            self._global_history.append({"role": "user", "content": trigger})
            self._global_history.append({
                "role": "assistant",
                "content": f"{COMPACT_MARKER} {summary}",
            })
            self._phase_idx += 1

        # Overshoot guard: a completed session (phase_idx == len(_PHASE_SEQUENCE))
        # left on disk would, on next hydrate, push _phase_idx past the sentinel
        # and crash _execute_tool_call's `_PHASE_SEQUENCE[_phase_idx]` lookup
        # on the very first tool call of a NEW conversation reusing the same
        # session_id. Treat that case as "already finished": mark the log so
        # the status endpoint can tell the FE, then reset state so a fresh run
        # starts at phase 0 with empty history. The old summary messages are
        # discarded — they belonged to a session that's done.
        if self._phase_idx >= len(_PHASE_SEQUENCE):
            self._move_log.append({"kind": "session_completed"})
            self._phase_idx = 0
            self._global_history = []
            logger.info(
                "Session %s was already complete on hydrate; reset to phase 0.",
                self._move_log.path,
            )

        self._phase_start_idx_global = len(self._global_history)

    def _compact_completed_phase_if_pending(self) -> None:
        """Collapse the just-completed phase's history span into one summary.

        Invariants relied on:
          - Called at the top of _run_react_turn, BEFORE any LLM call this
            turn. At this point history is balanced (no orphan tool_use), and
            the only message after the phase's span is the user message that
            step() just appended.
          - The last assistant message in the span is the wrap-up text
            (Issue #15) — already a natural-language summary of what the
            phase did. We reuse it as the compact summary so no extra LLM
            call is needed.
          - Leading user message(s) in the span are PRESERVED outside the
            compacted block. The compact summary is an assistant block, so
            absorbing the user trigger would either put assistant first in
            history or place two assistant blocks back-to-back — both 400
            from the Anthropic API. Keeping the user trigger as the compact
            block's left neighbour keeps history alternating across an
            arbitrary number of compactions.
        """
        if self._just_completed_phase is None:
            return
        history = self._global_history
        # End is exclusive of the user message just appended by step().
        end_idx = len(history) - 1
        start_idx = self._phase_start_idx_global
        # Preserve the leading user trigger(s) so the compact assistant block
        # is paired with a user neighbour. Without this, the very first
        # compaction would drop history[0] (the user msg that started phase 0)
        # and leave the rebuilt history starting with an assistant message.
        while start_idx < end_idx and history[start_idx].get("role") == "user":
            start_idx += 1
        if start_idx >= end_idx:
            # Nothing assistant-side to compact (e.g. NEGOTIATING phase never
            # wrote to _global_history, or the span was user-only). Clear
            # state and leave the user messages as-is.
            self._just_completed_phase = None
            return
        # Find the trailing wrap-up text. Walk backwards within the span for
        # the last assistant message whose content is a plain string — that
        # is the wrap-up llm.chat output. Tool_use blocks are list content.
        summary = f"Phase {self._just_completed_phase} completed."
        for i in range(end_idx - 1, start_idx - 1, -1):
            msg = history[i]
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                summary = msg["content"]
                break
        self._global_history = compact_phase_range(
            history, start_idx, end_idx, summary,
        )
        # After compaction the span is one message at start_idx. The user
        # message that was at end_idx is now at start_idx + 1 → next phase
        # begins one slot past that.
        self._phase_start_idx_global = start_idx + 1
        self._just_completed_phase = None

    def _handle_interrupt_bailout(self) -> str:
        """Clean-shutdown path for an interrupt observed in `_run_react_turn`.

        Resets the flag (so the next user turn proceeds normally), transitions
        to IDLE, emits a state event for the SSE clients, and returns the
        reply string that the agent message bubble will render.  Callers must
        only invoke this once history is balanced (no orphan tool_use blocks).
        """
        self._interrupted = False
        self.state = LoopState.IDLE
        self._emit("state", state=self.state.value)
        return "Interrupted by user."

    async def _execute_tool_call(self, tc: dict) -> tuple[str, str | None]:
        """
        Execute one LLM tool call.

        Returns (tool_result_text, error_reply).
        error_reply is non-None when the phase fails; it is the formatted
        user-facing error message and should be returned from step() directly.

        Query tools (isinstance(tool, QueryTool)) return their result directly
        without advancing phase state or triggering error handling.
        """
        tool_name = tc["name"]
        params = tc.get("input", {})

        self._emit("tool_call", id=tc.get("id"), name=tool_name, input=params)

        # Meta-tool: sync phase bubbles to the given phase without touching Blender.
        if tool_name == "sync_phase_state":
            phase_name = params.get("current_phase", "")
            self._sync_to_phase(phase_name)
            self._emit(
                "tool_result",
                id=tc.get("id"),
                name=tool_name,
                success=True,
                summary=f"Phase synced to: {phase_name}",
            )
            self._log_move({
                "kind": "tool",
                "phase": self.current_phase,
                "name": tool_name,
                "args": params,
                "result_summary": f"synced to {phase_name}",
                "success": True,
            })
            return f"Frontend phase state synced to '{phase_name}'.", None

        # Meta-tool: read from the off-prompt MoveLog. Re-injects detail the
        # LLM compacted away — never advances phase state, never touches
        # Blender. Schema lives in app.agent.history.
        if tool_name == QUERY_HISTORY_TOOL_NAME:
            if self._move_log is None:
                result_str = json.dumps({
                    "error": "Move log is not enabled for this session.",
                    "moves": [],
                })
            else:
                # Backstop the LLM-controlled `last_n` so a no-args call (or
                # one with an absurd value) cannot defeat compaction by yanking
                # the entire log back into the prompt. The system prompt asks
                # the LLM to keep last_n small; this enforces it.
                raw_last_n = params.get("last_n")
                if not isinstance(raw_last_n, int) or raw_last_n <= 0:
                    capped_last_n = QUERY_HISTORY_DEFAULT_LAST_N
                else:
                    capped_last_n = min(raw_last_n, QUERY_HISTORY_MAX_LAST_N)
                moves = self._move_log.read(
                    phase=params.get("phase"),
                    kind=params.get("kind"),
                    name=params.get("name"),
                    last_n=capped_last_n,
                )
                result_str = json.dumps(moves, ensure_ascii=False)
            self._emit(
                "tool_result",
                id=tc.get("id"),
                name=tool_name,
                success=True,
                summary=f"{result_str[:200]}",
            )
            self._log_move({
                "kind": "tool",
                "phase": self.current_phase,
                "name": tool_name,
                "args": params,
                "result_summary": f"{len(result_str)} bytes",
                "success": True,
            })
            return result_str, None

        tool = self._phase_tools.get(tool_name)
        if tool is None:
            self._emit(
                "tool_result",
                id=tc.get("id"),
                name=tool_name,
                success=False,
                summary=f"Tool '{tool_name}' is not available.",
            )
            return f"Tool '{tool_name}' is not available.", None

        # Query tools: read-only, no phase advancement
        if isinstance(tool, QueryTool):
            result_str = await asyncio.to_thread(tool.run, self._blender, params)
            self._emit(
                "tool_result",
                id=tc.get("id"),
                name=tool_name,
                success=True,
                summary=result_str[:500],
            )
            self._log_move({
                "kind": "tool",
                "phase": self.current_phase,
                "name": tool_name,
                "args": params,
                "result_summary": result_str[:500],
                "success": True,
            })
            return result_str, None

        # Phase-slot gate: refuse to execute a phase-advancing tool whose
        # declared slot does not match the current _phase_idx slot. Without
        # this gate, the loop blindly advances `_PHASE_SEQUENCE[_phase_idx]`
        # whenever any advances_phase=True tool succeeds — so a stray
        # post-phase_5 call to `setup_import_source` (Phase 0) would mark
        # `phase_6` completed and flip the pipeline to done without
        # batch_export ever running.
        #
        # Skip-this-check conditions:
        # - tool.phase_slot is None (legacy opt-in default; sub-step / query
        #   tools never set this).
        # - tool.advances_phase is False at declaration time (sub-step path —
        #   no risk of bumping _phase_idx). PhysicsChains is the one tool
        #   whose advances_phase is dynamic on prepare_only, but its
        #   phase_slot ("phase_4b") is correct for both branches.
        slot = tool.phase_slot
        if slot is not None:
            if self._phase_idx >= len(_PHASE_SEQUENCE):
                msg = (
                    f"Tool '{tool_name}' rejected: pipeline is already complete "
                    f"(all phases done). Use a query tool (e.g. list_collections, "
                    f"scene_info) or sync_phase_state to revisit a prior phase."
                )
                self._emit(
                    "tool_result",
                    id=tc.get("id"),
                    name=tool_name,
                    success=False,
                    summary=msg,
                )
                self._log_move({
                    "kind": "tool",
                    "phase": self.current_phase,
                    "name": tool_name,
                    "args": params,
                    "result_summary": msg,
                    "success": False,
                })
                return msg, None
            expected = _PHASE_SEQUENCE[self._phase_idx]
            if slot != expected:
                msg = (
                    f"Tool '{tool_name}' rejected: it advances slot '{slot}', "
                    f"but the loop is currently at slot '{expected}'. "
                    f"Pick a tool whose phase_slot matches '{expected}', or call "
                    f"a query tool to inspect scene state. Blender was NOT touched."
                )
                self._emit(
                    "tool_result",
                    id=tc.get("id"),
                    name=tool_name,
                    success=False,
                    summary=msg,
                )
                self._log_move({
                    "kind": "tool",
                    "phase": self.current_phase,
                    "name": tool_name,
                    "args": params,
                    "result_summary": msg,
                    "success": False,
                })
                return msg, None

        # Phase tools: execute, advance state on success.
        # Wrap in try/except so unexpected runtime errors (e.g. Blender
        # disconnecting mid-tool) are converted to PhaseResult.fail instead
        # of propagating as unhandled exceptions. An unhandled exception here
        # would leave the assistant's tool_use blocks in history without a
        # matching tool_result, causing a 400 on the next API call.
        try:
            result = await asyncio.to_thread(
                tool.run, self._blender, self._cache, params
            )
        except Exception as exc:
            result = PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=tool_name,
                    message=f"Unexpected error in {tool_name}: {exc}",
                    raw=type(exc).__name__,
                )
            )

        if result.success:
            diff = (
                json.dumps(result.state_diff, ensure_ascii=False)
                if result.state_diff
                else "no scene changes"
            )
            if tool.advances_phase:
                # Defensive: should be unreachable if state == DONE bounces
                # in `_dispatch` for fully-complete sessions, but guard the
                # IndexError so a future state-machine regression cannot brick
                # the session the same way the unrestored-DONE bug did.
                if self._phase_idx >= len(_PHASE_SEQUENCE):
                    self.state = LoopState.DONE
                    self._emit("state", state=self.state.value)
                    return (
                        f"Tool {tool_name} succeeded, but the workflow is "
                        "already past the final phase. All phases are complete."
                    ), None
                completed = _PHASE_SEQUENCE[self._phase_idx]
                self._phase_idx += 1
                # Issue #15 — signal the inter-phase pause rail.  The flag is
                # consumed by `_run_react_turn` AFTER the tool_result lands in
                # history (so the wrap-up llm.chat sees a balanced trace) and
                # is reset by the wrap-up branch itself.
                self._phase_just_advanced = True
                # Per-tool override of the Issue #15 pause rail. Mechanical
                # setup tools opt out so the loop can chain into the next
                # tool in the same turn.
                self._last_tool_requires_pause = tool.requires_user_pause
                # Context-management: mark this phase as ready for compaction
                # on the NEXT _run_react_turn (after the user resumes from the
                # wrap-up pause). Both flags coexist by design — _phase_just_-
                # advanced drives the in-turn wrap-up, _just_completed_phase
                # drives next-turn compaction.
                self._just_completed_phase = completed
                next_phase = (
                    _PHASE_SEQUENCE[self._phase_idx]
                    if self._phase_idx < len(_PHASE_SEQUENCE)
                    else None
                )
                self._emit(
                    "tool_result",
                    id=tc.get("id"),
                    name=tool_name,
                    success=True,
                    summary=f"Phase {completed} completed. {diff}",
                )
                self._log_move({
                    "kind": "tool",
                    "phase": completed,
                    "name": tool_name,
                    "args": params,
                    "result_summary": diff,
                    "success": True,
                })
                self._log_move({
                    "kind": "phase_advance",
                    "phase": completed,
                    "to_phase": next_phase,
                })
                await self._emit_widget_if_inspector(tool_name, result.state_diff, params)
                self._on_phase_advance(completed_phase=completed)
                return f"Phase {completed} completed. Scene diff: {diff}", None
            else:
                self._emit(
                    "tool_result",
                    id=tc.get("id"),
                    name=tool_name,
                    success=True,
                    summary=f"sub-step ok: {diff}",
                )
                self._log_move({
                    "kind": "tool",
                    "phase": self.current_phase,
                    "name": tool_name,
                    "args": params,
                    "result_summary": diff,
                    "success": True,
                })
                await self._emit_widget_if_inspector(tool_name, result.state_diff, params)
                return f"Tool {tool_name} succeeded (sub-step, phase not advanced). Result: {diff}", None
        else:
            self._pending_error = result.error
            self.state = LoopState.ERROR_HANDLING
            self._emit("state", state=self.state.value)
            self._emit(
                "tool_result",
                id=tc.get("id"),
                name=tool_name,
                success=False,
                summary=result.error.message,
            )
            self._log_move({
                "kind": "tool",
                "phase": self.current_phase,
                "name": tool_name,
                "args": params,
                "result_summary": result.error.message,
                "success": False,
            })
            self._emit(
                "error_choice",
                operator=result.error.operator,
                category=result.error.category,
                message=result.error.message,
                summary=result.error.message[:120],
            )
            try:
                error_reply = await asyncio.to_thread(
                    self._error_handler.format, result.error, self._llm
                )
            except Exception as exc:
                error_reply = (
                    f"[FAIL] {tool_name}: {result.error.message}\n"
                    f"（错误格式化失败: {exc}）\n"
                    "[Retry] — 重新执行  |  [Skip] — 跳过继续  |  [Ask] — 查看详情"
                )
            return f"Phase failed: {result.error.message}", error_reply

    async def _annotate_chains(self, chains: list[dict]) -> list[dict]:
        """Thin wrapper around `physics_annotate.annotate_chains` so tests can
        still `patch.object(loop, "_annotate_chains", ...)` and so the call
        site reads naturally. All prompt-engineering and JSON-recovery lives
        in `app.phases.physics_annotate`.
        """
        return await annotate_chains(self._llm, chains, emit=self._emit)

    async def _emit_widget_if_inspector(
        self,
        tool_name: str,
        state_diff: dict | None,
        params: dict | None = None,
    ) -> None:
        """Stage a widget event for tools whose result needs user confirmation.

        Issue #7: physics_classification's chain_topology and material_inspect's
        materials+texture_files+existing_connections feed structured editable
        UI widgets instead of free-text Q&A. The next user message arrives
        prefixed `[CONFIRMED_CLASSIFICATIONS]` / `[CONFIRMED_MATERIAL_MAPPING]`
        carrying the user's selections as JSON.

        Deferred emit (Issue 3): for physics_classification and material_inspect
        we DO NOT emit the widget event here.  Emitting at tool-return time
        surfaces an empty-looking table to the user BEFORE the LLM has a chance
        to comment on the result in chat — confusing UX.  Instead we stash the
        payload in `self._pending_widget` and `_run_react_turn` emits it after
        the LLM's next text-only response (so chat commentary lands first).

        Issue #11: for material_inspect, run the LLM pre-fill (suggest texture
        mapping) inline NOW so the deferred-emit payload already carries
        `suggestions` — the suggest call piggybacks on the same dead time as
        `_annotate_chains` (while the agent commentary renders).

        Other widget-like emits (model_type_inferred) remain immediate because
        they don't pair with an LLM commentary in the same way.
        """
        if not state_diff:
            return
        if tool_name == "physics_classification":
            chain_topology = state_diff.get("chain_topology") or {}
            chains = chain_topology.get("chain_heads") or []
            if chains:
                # Run the annotation LLM call now (slow) so the widget is
                # ready to emit immediately when the deferred-emit point hits.
                chains = await self._annotate_chains(chains)
                from app.phases.physics_bones import list_inferred_types
                self._pending_widget = (
                    "widget_classification",
                    {"chains": chains, "inferred_types": list_inferred_types()},
                )
        elif tool_name == "material_inspect":
            # Suppress the editing widget when the LLM marks this call as a
            # post-wire verification (Phase 5A Step 6). Otherwise the verify
            # inspect re-emits the widget, the user has no other reply channel,
            # and material_setup gets re-run in a self-feeding loop. See
            # docs/agent/agent_workflow.md Phase 5A Steps 3 and 6.
            purpose = (params or {}).get("purpose", "classify")
            if purpose == "verify":
                return
            materials = state_diff.get("materials") or []
            if materials:
                existing = state_diff.get("existing_connections") or {}
                texture_files = state_diff.get("texture_files") or []
                # Issue #11: run the suggest LLM call NOW so the deferred
                # widget arrives with `suggestions` populated.
                suggestions = await self._suggest_texture_mapping(
                    materials, texture_files, existing
                )
                self._pending_widget = (
                    "widget_material",
                    {
                        "materials": materials,
                        "existing_connections": existing,
                        "texture_files": texture_files,
                        "suggestions": suggestions,
                    },
                )
        elif tool_name == "setup_infer_model_type":
            # Issue #4: surface the inference outcome to the form's
            # `model_type` dropdown via SSE so the user can either accept the
            # auto-pick or override before the pipeline continues. Waves 3/4
            # (issues #5/#6) hook the non-exact decisions into widget flows;
            # for now we emit the same event for every decision and let the
            # frontend / LLM handle the next step.
            preset = state_diff.get("inferred_preset")
            decision = state_diff.get("decision")
            if preset and decision:
                self._emit(
                    "model_type_inferred",
                    preset=preset,
                    coverage=state_diff.get("coverage", 0.0),
                    decision=decision,
                    candidates=state_diff.get("candidates") or [],
                    uncovered_slots=state_diff.get("uncovered_slots") or [],
                )

    def _flush_pending_widget(self) -> None:
        """Emit any stashed widget event (Issue 3 deferred-emit path).

        Called at safe points in _run_react_turn — after the LLM has had a
        chance to produce text commentary about the inspector tool result.
        Idempotent: clears the slot after emitting so re-calls are no-ops.
        """
        if self._pending_widget is None:
            return
        event_type, payload = self._pending_widget
        self._pending_widget = None
        self._emit(event_type, **payload)

    async def _suggest_texture_mapping(
        self,
        materials: list[str],
        texture_files: list[str],
        existing_connections: dict[str, dict[str, str]],
    ) -> dict[str, dict[str, str]]:
        """Thin wrapper around `material.suggest_texture_mapping` so tests can
        still `patch.object(AgentLoop, "_suggest_texture_mapping", ...)` and
        the call site reads naturally. Prompt + filtering live in
        `app.phases.material`.
        """
        return await suggest_texture_mapping(
            self._llm, materials, texture_files, existing_connections,
        )

    def _on_phase_advance(self, completed_phase: str | None = None) -> None:
        """Update state after a phase completes successfully."""
        if completed_phase is not None:
            self._emit(
                "phase_completed",
                phase=completed_phase,
                index=self._phase_idx - 1,
                total=len(_PHASE_SEQUENCE),
            )
        if self._phase_idx >= len(_PHASE_SEQUENCE):
            self.state = LoopState.DONE
            self._emit("state", state=self.state.value)
            return
        next_phase = _PHASE_SEQUENCE[self._phase_idx]
        self._emit(
            "phase_started",
            phase=next_phase,
            index=self._phase_idx,
            total=len(_PHASE_SEQUENCE),
        )
        if next_phase in _NEGOTIATING_PHASES:
            self.state = LoopState.NEGOTIATING
            self._emit("state", state=self.state.value)
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

        heal_history(self._phase_history)
        response = await asyncio.to_thread(
            self._llm.chat,
            self._phase_history,
            system=self._system_prompt,
        )

        reply = strip_dsml_block(response.content)
        self._phase_history.append({"role": "assistant", "content": reply})

        # Detect structured proposal from LLM (propose_and_confirm protocol)
        if '"requires_user_review": true' in reply or '"requires_user_review":true' in reply:
            self.state = LoopState.AWAIT_CONFIRM
            self._emit("state", state=self.state.value)

        return reply

    async def _handle_await_confirm(self, user_message: str) -> str:
        """
        User replied to a proposal (confirm or correction).

        Transitions back to RUNNING_PHASE so the LLM can call the phase tool
        with the confirmed parameters.  The full conversation history provides
        the classification context needed to build the correct tool call.
        If the user made a correction the LLM will re-propose; the proposal
        detection in _run_react_turn will set AWAIT_CONFIRM again.
        """
        self.state = LoopState.RUNNING_PHASE
        return await self._run_react_turn()

    # ── error handling ────────────────────────────────────────────────────

    async def _handle_error_choice(self, user_message: str) -> str:
        """Route user's [Retry] / [Skip] / [Ask] choice after a phase failure."""
        choice = await asyncio.to_thread(
            self._error_handler.parse_user_choice, user_message, self._llm
        )

        match choice:
            case "retry":
                self.state = LoopState.RUNNING_PHASE
                self._pending_error = None
                return await self._run_react_turn()

            case "skip":
                skipped = self.current_phase or "unknown"
                self._skipped_phases.add(skipped)
                self._phase_idx += 1
                next_phase = (
                    _PHASE_SEQUENCE[self._phase_idx]
                    if self._phase_idx < len(_PHASE_SEQUENCE)
                    else None
                )
                # Mirror the bookkeeping a successful phase tool would have
                # done: record the advance on disk so session recovery counts
                # this phase, and queue context-management compaction so the
                # failed tool's tool_use/tool_result + error reply blocks do
                # not linger in _global_history forever.
                self._log_move({
                    "kind": "phase_advance",
                    "phase": skipped,
                    "to_phase": next_phase,
                    "skipped": True,
                })
                self._just_completed_phase = skipped
                # The success path returns to RUNNING_PHASE via the wrap-up
                # branch; the skip path has no LLM call to ride on, so set
                # the state explicitly. _on_phase_advance can still override
                # to DONE / NEGOTIATING when warranted.
                self.state = LoopState.RUNNING_PHASE
                self._on_phase_advance(completed_phase=skipped)
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
                return await self._handle_ask_mode(user_message)

            case _:
                return (
                    "Please choose one of the options: "
                    "[Retry] to try again, [Skip] to skip this phase, "
                    "[Ask] for more information about the error."
                )

    async def _handle_ask_mode(self, user_message: str) -> str:
        """
        Explanation + scene-query mode (A2).

        The LLM may call read-only query tools (scene_info, list_objects,
        get_bone_info, list_collections) to fetch live data for its answer.
        Phase-advancing tools are NOT available — the loop rejects them.
        Exits back to ERROR_HANDLING when user mentions retry/skip/continue.

        Error details from _pending_error are injected into the system prompt
        so the LLM can explain the actual failure rather than guessing.
        """
        system = self._system_prompt + (
            "\n\n[ASK MODE] You are in explanation-and-query mode. "
            "You may call ANY of the read-only query tools in your tool list "
            "(scene_info, list_objects, get_bone_info, list_collections, "
            "get_mesh_info, get_material_info, get_object_props, inspect_material_nodes, "
            "list_mdf_presets, physics_read) "
            "to fetch live Blender data for diagnosis. "
            "You CANNOT call phase-advancing tools (pose_correction, skeleton_align, "
            "vertex_groups, physics_chains, physics_transplant, physics_classification, "
            "physics_adjust, material_setup, material_generate, batch_export, etc.) "
            "in this mode. "
            "Do NOT output DSML tool-call markup."
        )
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
            system += "\n".join(detail_lines)

        query_tools = self._build_query_tool_list()
        history = self._global_history

        for _ in range(_MAX_ASK_ROUNDS):
            heal_history(history)
            response = await asyncio.to_thread(
                self._llm.chat,
                history,
                system=system,
                tools=query_tools if query_tools else None,
            )

            if not response.has_tool_calls:
                # Check for DSML markup even in ASK_MODE (DeepSeek fallback)
                dsml_calls = parse_dsml_tool_calls(response.content)
                if dsml_calls:
                    # Filter to query tools only; reject phase tools silently
                    query_names = {t["name"] for t in query_tools}
                    dsml_calls = [c for c in dsml_calls if c["name"] in query_names]
                if dsml_calls:
                    clean = strip_dsml_block(response.content)
                    history.append({
                        "role": "assistant",
                        "content": clean or "[querying scene]",
                    })
                    results = []
                    for tc in dsml_calls:
                        res, _ = await self._execute_tool_call(tc)
                        results.append(res)
                    history.append({"role": "user", "content": "\n".join(results)})
                    continue
                # Plain text answer — done
                reply = strip_dsml_block(response.content)
                break

            # Structured tool calls
            history.append(self._build_assistant_tool_msg(response))
            results: list[dict] = []
            query_names = {t["name"] for t in query_tools}
            for tc in response.tool_calls:
                if tc["name"] not in query_names:
                    res_text = (
                        f"Tool '{tc['name']}' is not available in ASK MODE. "
                        "Only scene inspection tools may be called here."
                    )
                else:
                    res_text, _ = await self._execute_tool_call(tc)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": res_text,
                })
            history.append({"role": "user", "content": results})
        else:
            reply = "已达到查询轮数上限。请根据以上信息告诉我您想 [重试] 还是 [跳过]。"

        exit_keywords = ("continue", "retry", "skip", "back", "exit",
                         "继续", "重试", "跳过", "返回", "退出", "开始", "执行")
        if any(kw in user_message.lower() for kw in exit_keywords):
            self.state = LoopState.ERROR_HANDLING
        return reply

    # ── helpers ───────────────────────────────────────────────────────────

    def _register_available_phases(self) -> None:
        from app.phases.batch_export import BatchExport
        from app.phases.material import (
            MaterialConsolidate,
            MaterialGenerate,
            MaterialInspect,
            MaterialSetup,
        )
        from app.phases.physics_bones import (
            PhysicsAdjust,
            PhysicsChains,
            PhysicsClassification,
            PhysicsTransplant,
        )
        from app.phases.pose_correction import PoseCorrection
        from app.phases.query_tools import (
            GetBoneInfo,
            GetMaterialInfo,
            GetMeshInfo,
            GetObjectProps,
            InspectMaterialNodes,
            ListCollections,
            ListMdfPresets,
            ListObjects,
            PhysicsRead,
            SceneInfo,
        )
        from app.phases.infer_model_type import InferModelType
        from app.phases.preset_write import PresetCustomWrite, PresetSupplementWrite
        from app.phases.setup import SetupImportMHWilds, SetupImportSource, SetupValidateScene
        from app.phases.skeleton_align import SkeletonAlign
        from app.phases.vertex_groups import VertexGroups

        for tool in (
            # Phase tools (advance _phase_idx on success)
            SetupImportSource(),
            SetupValidateScene(),
            InferModelType(),
            PresetSupplementWrite(),  # issue #5 — write _extended.json after user confirm
            PresetCustomWrite(),      # issue #6 — write _custom.json after user confirm
            SetupImportMHWilds(),
            PoseCorrection(),
            SkeletonAlign(),
            VertexGroups(),
            PhysicsTransplant(),
            PhysicsClassification(),
            PhysicsChains(),
            PhysicsAdjust(),
            MaterialConsolidate(),
            MaterialInspect(),
            MaterialSetup(),
            MaterialGenerate(),
            BatchExport(),
            # Query tools (read-only, always available)
            SceneInfo(),
            ListObjects(),
            GetBoneInfo(),
            ListCollections(),
            GetMeshInfo(),
            GetMaterialInfo(),
            GetObjectProps(),
            InspectMaterialNodes(),
            ListMdfPresets(),
            PhysicsRead(),
        ):
            self._phase_tools[tool.name] = tool

    def emit_phase_sync(self) -> None:
        """Re-emit phase progress events so a reconnecting frontend can sync its bubbles."""
        self._sync_to_phase(_PHASE_SEQUENCE[self._phase_idx] if self._phase_idx < len(_PHASE_SEQUENCE) else None)

    def _sync_to_phase(self, phase_name: str | None) -> None:
        """Update _phase_idx to phase_name and emit phase_completed/phase_started events."""
        if phase_name is None or phase_name not in _PHASE_SEQUENCE:
            return
        target_idx = _PHASE_SEQUENCE.index(phase_name)
        self._phase_idx = target_idx
        for i in range(target_idx):
            self._emit("phase_completed", phase=_PHASE_SEQUENCE[i], index=i, total=len(_PHASE_SEQUENCE))
        self._emit("phase_started", phase=phase_name, index=target_idx, total=len(_PHASE_SEQUENCE))

    def _build_tool_list(self) -> list[dict]:
        return (
            [t.tool_schema() for t in self._phase_tools.values()]
            + [_SYNC_PHASE_TOOL_SCHEMA, QUERY_HISTORY_TOOL_SCHEMA]
        )

    def _build_phase_only_tool_list(self) -> list[dict]:
        """Phase tools only — excludes query tools. Used when the LLM has spent
        too many rounds on scene inspection and must commit to a phase tool call."""
        return [
            t.tool_schema()
            for t in self._phase_tools.values()
            if not isinstance(t, QueryTool)
        ]

    def _build_query_tool_list(self) -> list[dict]:
        return [
            t.tool_schema()
            for t in self._phase_tools.values()
            if isinstance(t, QueryTool)
        ]

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
