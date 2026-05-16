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
import re
import time
from collections.abc import Callable
from typing import Any

# DeepSeek-specific markup patterns.
# DeepSeek V4 sometimes emits tool calls as inline XML-like markup in the text
# content instead of using the OpenAI API's function-call fields.  We strip this
# markup in NEGOTIATING mode (no tools available) and *parse + execute* it in
# RUNNING_PHASE mode so the tool call is not silently dropped.

_RAW_TOOL_CALL_RE = re.compile(
    r"<｜｜DSML｜｜tool_calls>.*?</｜｜DSML｜｜tool_calls>",
    re.DOTALL,
)
_DSML_INVOKE_RE = re.compile(
    r'<｜｜DSML｜｜invoke name="(?P<name>[^"]+)">(?P<body>.*?)</｜｜DSML｜｜invoke>',
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    r'<｜｜DSML｜｜parameter name="(?P<name>[^"]+)" string="(?P<is_str>true|false)">'
    r"(?P<value>.*?)</｜｜DSML｜｜parameter>",
    re.DOTALL,
)


_DSML_OPEN_TAG = "<｜｜DSML｜｜tool_calls>"
_DSML_CLOSE_TAG = "</｜｜DSML｜｜tool_calls>"


def _strip_dsml_block(text: str) -> str:
    """
    Remove the DSML tool-call block from text, returning only the prose part.

    Tries regex first; if it leaves any DSML behind (due to invisible Unicode
    differences), falls back to plain-string truncation at the open tag.
    """
    text = _RAW_TOOL_CALL_RE.sub("", text).strip()
    # Greedy fallback: regex may miss the block if chars look identical but differ
    start = text.find(_DSML_OPEN_TAG)
    if start != -1:
        text = text[:start].rstrip()
    return text


def _parse_dsml_tool_calls(content: str) -> list[dict]:
    """
    Extract tool calls from DeepSeek DSML markup embedded in text content.

    Returns a list of canonical tool-call dicts ({id, name, input}) that can
    be passed directly to AgentLoop._execute_tool_call().  Returns [] when
    no DSML markup block is found.

    Uses regex first; falls back to plain str.find() when the outer block
    regex fails (e.g. due to invisible Unicode differences between the source
    pattern and the model's output that visually appear identical).
    """
    tc_match = _RAW_TOOL_CALL_RE.search(content)
    if tc_match:
        block = tc_match.group(0)
    else:
        # Plain-string fallback — tolerates invisible character differences
        start = content.find(_DSML_OPEN_TAG)
        end = content.find(_DSML_CLOSE_TAG)
        if start == -1 or end == -1 or end < start:
            return []
        block = content[start : end + len(_DSML_CLOSE_TAG)]

    calls = []
    for i, invoke in enumerate(_DSML_INVOKE_RE.finditer(block)):
        tool_name = invoke.group("name")
        params: dict = {}
        for pm in _DSML_PARAM_RE.finditer(invoke.group("body")):
            raw = pm.group("value").strip()
            if pm.group("is_str") == "true":
                params[pm.group("name")] = raw
            else:
                try:
                    params[pm.group("name")] = json.loads(raw)
                except json.JSONDecodeError:
                    params[pm.group("name")] = raw
        calls.append({"id": f"dsml_{i}_{tool_name}", "name": tool_name, "input": params})
    return calls

from app.agent.error_handler import ErrorHandler
from app.agent.prompts import build_phase_prompt, build_system_prompt
from app.blender.client import BlenderClient
from app.blender.state import SceneCache
from app.llm.client import LLMClient, LLMResponse, Message
from app.phases.base import PhaseError, PhaseTool
from app.phases.query_tools import QueryTool

# ── constants ──────────────────────────────────────────────────────────────

_PHASE_SEQUENCE: list[str] = [
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

_MAX_TOOL_ROUNDS = 15
_MAX_ASK_ROUNDS = 3
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
    ) -> None:
        self._llm = llm
        self._blender = blender
        self._cache = SceneCache(blender)
        self._error_handler = ErrorHandler()
        self._event_sink = event_sink
        self._session_config = session_config or {}

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

        self._system_prompt = build_system_prompt(physics_presets, session_config)

    def _emit(self, event_type: str, **payload: Any) -> None:
        """Publish one structured event to the sink, if any.

        The sink must be thread-safe — emits may originate from threadpool
        workers when tools run via asyncio.to_thread. Sink installers in the
        route layer wrap a queue.put with loop.call_soon_threadsafe.
        """
        if self._event_sink is None:
            return
        evt: dict[str, Any] = {
            "type": event_type,
            "ts": time.time(),
            "phase": self.current_phase,
            "state": self.state.value,
            **payload,
        }
        self._event_sink(evt)

    # ── public ────────────────────────────────────────────────────────────

    @property
    def current_phase(self) -> str | None:
        if self._phase_idx >= len(_PHASE_SEQUENCE):
            return None
        return _PHASE_SEQUENCE[self._phase_idx]

    async def step(self, user_message: str) -> str:
        """Process one user turn. Returns the agent reply string."""
        self._global_history.append({"role": "user", "content": user_message})
        self._emit("message", role="user", content=user_message)
        reply = await self._dispatch(user_message)
        self._global_history.append({"role": "assistant", "content": reply})
        self._emit("message", role="assistant", content=reply)
        return reply

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
        history = self._global_history
        query_tool_names = {
            name for name, t in self._phase_tools.items() if isinstance(t, QueryTool)
        }
        query_only_rounds = 0  # consecutive rounds with only query tool calls

        for _ in range(_MAX_TOOL_ROUNDS):
            # Restrict to phase tools after too many query-only rounds
            if query_only_rounds >= _MAX_QUERY_ONLY_ROUNDS:
                tools = self._build_phase_only_tool_list()
            else:
                tools = self._build_tool_list()

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
                dsml_calls = _parse_dsml_tool_calls(content)
                if dsml_calls:
                    clean = _strip_dsml_block(content)
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
                    if self.state != LoopState.RUNNING_PHASE:
                        final = await asyncio.to_thread(
                            self._llm.chat,
                            history,
                            system=self._system_prompt,
                        )
                        return final.content
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

        Query tools (isinstance(tool, QueryTool)) return their result directly
        without advancing phase state or triggering error handling.
        """
        tool_name = tc["name"]
        params = tc.get("input", {})

        self._emit("tool_call", id=tc.get("id"), name=tool_name, input=params)

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
            return result_str, None

        # Phase tools: execute, advance state on success
        result = await asyncio.to_thread(
            tool.run, self._blender, self._cache, params
        )

        if result.success:
            diff = (
                json.dumps(result.state_diff, ensure_ascii=False)
                if result.state_diff
                else "no scene changes"
            )
            if tool.advances_phase:
                completed = _PHASE_SEQUENCE[self._phase_idx]
                self._phase_idx += 1
                self._emit(
                    "tool_result",
                    id=tc.get("id"),
                    name=tool_name,
                    success=True,
                    summary=f"Phase {completed} completed. {diff}",
                )
                self._emit_widget_if_inspector(tool_name, result.state_diff)
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
                self._emit_widget_if_inspector(tool_name, result.state_diff)
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
            self._emit(
                "error_choice",
                operator=result.error.operator,
                category=result.error.category,
                message=result.error.message,
                summary=result.error.message[:120],
            )
            error_reply = await asyncio.to_thread(
                self._error_handler.format, result.error, self._llm
            )
            return f"Phase failed: {result.error.message}", error_reply

    def _emit_widget_if_inspector(self, tool_name: str, state_diff: dict | None) -> None:
        """Emit a widget event for tools whose result needs user confirmation.

        Issue #7: physics_classification's chain_topology and material_inspect's
        materials+texture_files+existing_connections feed structured editable
        UI widgets instead of free-text Q&A. The next user message arrives
        prefixed `[CONFIRMED_CLASSIFICATIONS]` / `[CONFIRMED_MATERIAL_MAPPING]`
        carrying the user's selections as JSON.
        """
        if not state_diff:
            return
        if tool_name == "physics_classification":
            chain_topology = state_diff.get("chain_topology") or {}
            chains = chain_topology.get("chain_heads") or []
            if chains:
                self._emit("widget_classification", chains=chains)
        elif tool_name == "material_inspect":
            materials = state_diff.get("materials") or []
            if materials:
                self._emit(
                    "widget_material",
                    materials=materials,
                    existing_connections=state_diff.get("existing_connections") or {},
                    texture_files=state_diff.get("texture_files") or [],
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

        response = await asyncio.to_thread(
            self._llm.chat,
            self._phase_history,
            system=self._system_prompt,
        )

        reply = _strip_dsml_block(response.content)
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
            response = await asyncio.to_thread(
                self._llm.chat,
                history,
                system=system,
                tools=query_tools if query_tools else None,
            )

            if not response.has_tool_calls:
                # Check for DSML markup even in ASK_MODE (DeepSeek fallback)
                dsml_calls = _parse_dsml_tool_calls(response.content)
                if dsml_calls:
                    # Filter to query tools only; reject phase tools silently
                    query_names = {t["name"] for t in query_tools}
                    dsml_calls = [c for c in dsml_calls if c["name"] in query_names]
                if dsml_calls:
                    clean = _strip_dsml_block(response.content)
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
                reply = _strip_dsml_block(response.content)
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
        from app.phases.setup import SetupImportMHWilds, SetupValidateScene
        from app.phases.skeleton_align import SkeletonAlign
        from app.phases.vertex_groups import VertexGroups

        for tool in (
            # Phase tools (advance _phase_idx on success)
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

    def _build_tool_list(self) -> list[dict]:
        return [t.tool_schema() for t in self._phase_tools.values()]

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
