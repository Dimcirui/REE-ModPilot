"""
Phase tool base contract (design decisions B6, E16, E17).

Architecture:
  - PhaseTool is a pure executor: receives params from agent loop, runs
    Blender operators, returns PhaseResult. No LLM calls inside phase tools.
  - Classification decisions (which tool / which preset) are made by the
    agent loop before calling phase.run() (E17).
  - BlenderClient is synchronous; callers use asyncio.to_thread() to avoid
    blocking the FastAPI event loop (E18).

Valid preset identifiers:
  X_PRESETS  — source model type presets (import_preset_enum)
  Y_PRESETS  — target game presets      (target_preset_enum)

Chinese preset names (e.g. "终末地", "怪猎荒野") are transmitted as UTF-8
JSON — Python's json module handles encoding transparently.  The Blender-side
exec receives the decoded Unicode string unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.blender.client import BlenderClient, BlenderError
from app.blender.state import SceneCache, SceneState

# ── valid preset identifiers ───────────────────────────────────────────────

#: Source-model X presets currently available in the toolkit's
#: `assets/presets/import/` folder. Mutable so the FastAPI lifespan
#: handler can replace the contents with the actual enumeration at
#: startup, and so Waves 3-4 (issues #5 / #6) can add user-supplemented
#: or user-created presets without a server restart.
#:
#: Default contents are the 13 X-preset names shipped with Modding-Toolkit
#: so unit tests that don't go through the lifespan still see a populated
#: set, and so server boot is non-fatal when Blender isn't reachable.
#:
#: Phase tools should `from app.phases.base import X_PRESETS` and read it
#: as a set. Do not rebind — mutate in place via `update_x_presets()` so
#: existing module-level references stay valid.
X_PRESETS: set[str] = {
    "MMD",
    "VRChat",
    "Valve社",
    "怪猎世界",
    "怪猎崛起",
    "怪猎荒野",
    "生化危机4",
    "生化危机9",
    "碧蓝幻想",
    "终末地",
    "绝地潜兵2",
    "赛马娘",
    "鬼泣5",
}


def update_x_presets(names: Iterable[str]) -> None:
    """Replace X_PRESETS contents in place. Used by the FastAPI lifespan
    handler to seed the runtime set from the live toolkit folder, and by
    Waves 3 / 4 to register newly-created presets."""
    X_PRESETS.clear()
    X_PRESETS.update(names)


def add_x_preset(name: str) -> None:
    """Register one newly-created preset (Waves 3 / 4)."""
    X_PRESETS.add(name)


#: Target-game Y presets available in assets/presets/bone/ (MVP: MHWs only).
#: Kept frozen for now — issues #4/#5/#6 are X-preset-only; Y is single-target
#: until post-MVP per design.md A4.
Y_PRESETS: frozenset[str] = frozenset({"怪猎荒野"})

#: Default Y preset for MHWs — the only MVP target game
DEFAULT_Y_PRESET: str = "怪猎荒野"


# ── result types ───────────────────────────────────────────────────────────


@dataclass
class PhaseError:
    """
    Structured error from a phase tool (B7).

    Fields are factual, not phrased for the user — the agent loop calls
    LLM to translate `message` + `suggestion` into user-facing language.
    """

    category: str
    """
    Error category for routing:
      "precondition"    — required object/setting not ready before op ran
      "operator_failed" — operator returned CANCELLED instead of FINISHED
      "timeout"         — Blender did not respond within the socket timeout
      "unexpected"      — unhandled exception (BlenderError / OSError)
    """

    operator: str
    """The bpy.ops.* call that failed, or '' if failure was pre-op."""

    message: str
    """Short technical description for LLM to phrase."""

    suggestion: str = ""
    """Known fix hint, if any (e.g. 'Select an ARMATURE object first')."""

    raw: str = ""
    """Raw exception text — for debug logs, never shown to user directly."""


@dataclass
class PhaseResult:
    """
    Return value from every phase tool (E16).

    On success: state_diff describes what changed (from SceneState.diff()).
    On failure: error is populated; state_diff is {}.
    """

    success: bool
    state_diff: dict = field(default_factory=dict)
    error: PhaseError | None = None

    @classmethod
    def ok(cls, state_diff: dict) -> "PhaseResult":
        return cls(success=True, state_diff=state_diff)

    @classmethod
    def fail(cls, error: PhaseError) -> "PhaseResult":
        return cls(success=False, state_diff={}, error=error)


# ── base class ─────────────────────────────────────────────────────────────


class PhaseTool(ABC):
    """
    Abstract base for all phase tools.

    Subclasses implement run() and declare a unique `name` identifier.
    They must NOT call LLMClient — all classification is done by the
    agent loop before run() is called.

    Typical run() pattern:
        1. Validate params → PhaseResult.fail() on bad input
        2. cache.refresh()  (entry spot-check, B5)
        3. Build Blender code string
        4. client.execute_and_extract(code)
        5. Parse operator return value
        6. cache.refresh()  (exit cache update)
        7. Compute and return PhaseResult.ok(state_diff)

    Phase advancement:
        By default, a successful run() call increments _phase_idx in the loop.
        Sub-step tools (e.g. MaterialInspect, PhysicsAdjust) should override
        advances_phase to return False so that intermediate steps within a phase
        do not prematurely push the loop past the current phase slot.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier used by the agent loop (e.g. 'pose_correction')."""
        ...

    @property
    def advances_phase(self) -> bool:
        """
        Whether a successful run() call should increment _phase_idx.

        Override to False for tools that are sub-steps within a phase
        (e.g. inspection / classification / parameter adjustment tools).
        Default: True.
        """
        return True

    @property
    def phase_slot(self) -> str | None:
        """
        Slot name in `_PHASE_SEQUENCE` that this tool advances through.

        Only consulted when `advances_phase` returns True. When set, the
        agent loop validates `tool.phase_slot == _PHASE_SEQUENCE[_phase_idx]`
        BEFORE executing the tool — a mismatch is rejected with a structured
        message so the LLM can reroute, and Blender state is never touched.

        Default None preserves the legacy "blind index advance" behavior for
        tools that haven't opted in yet. Sub-step tools (`advances_phase=False`)
        and query tools never need this.

        Note that one slot may legitimately have multiple advancing tools
        (e.g. issue #5 / #6 alternative `setup_infer` writers), so this is a
        many-tools-to-one-slot mapping.
        """
        return None

    @property
    def requires_user_pause(self) -> bool:
        """
        Whether a successful phase-advancing run() should pause for a user
        wrap-up turn before the loop continues.

        Default True preserves the Issue #15 inter-phase pause rail — most
        phase tools land at a point where the user wants to review results
        (widget confirmation, free-text Q&A, or simply a checkpoint) before
        the agent calls the next phase tool.

        Override to False on purely mechanical setup-style tools whose
        success is self-evident and whose downstream tool is unambiguous,
        so the agent can chain them in one turn. Only meaningful when
        advances_phase is also True.
        """
        return True

    @classmethod
    @abstractmethod
    def tool_schema(cls) -> dict[str, Any]:
        """
        LLM tool definition passed to LLMClient.chat(tools=[...]).

        Must return a dict with keys:
          "name"         — matches self.name
          "description"  — one-sentence description for the LLM
          "input_schema" — JSON Schema object describing run() params
        """
        ...

    @abstractmethod
    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        """
        Execute this phase synchronously.

        Args:
            client: Connected BlenderClient (caller ensures connection).
            cache:  SceneCache backed by the same client.
            params: Phase-specific parameter dict (validated inside run()).

        Returns:
            PhaseResult — always returned, never raises.
        """
        ...


# ── shared helpers ─────────────────────────────────────────────────────────


def require_finished(
    output_lines: list[str],
    operator: str,
) -> PhaseError | None:
    """
    Check that the first output line after SENTINEL contains 'FINISHED'.

    Returns None on success, PhaseError on operator failure.
    Blender operators return frozensets like {'FINISHED'} or {'CANCELLED'}.
    """
    if not output_lines:
        return PhaseError(
            category="operator_failed",
            operator=operator,
            message="Operator produced no output — may have errored silently.",
        )
    ret_str = output_lines[0]
    if "FINISHED" not in ret_str:
        return PhaseError(
            category="operator_failed",
            operator=operator,
            message=f"Operator returned {ret_str!r} instead of FINISHED.",
            suggestion="Check Blender's Info editor for the full error message.",
        )
    return None


def object_exists_code(name: str) -> str:
    """
    Return a Blender code snippet that prints 'OK' or 'NOT_FOUND:<name>'
    for a named object. Used for precondition checks.
    """
    from app.blender.client import BLENDER_SENTINEL

    return (
        f"import bpy\n"
        f"obj = bpy.data.objects.get({name!r})\n"
        f"print({BLENDER_SENTINEL!r})\n"
        f"print('OK' if obj is not None else 'NOT_FOUND:{name}')\n"
    )


def wrap_with_sentinel(inner: str) -> str:
    """
    Wrap Blender code so all output before SENTINEL is discarded,
    protecting against Modding-Toolkit operator stdout noise.
    """
    from app.blender.client import BLENDER_SENTINEL

    return f"{inner}\nprint({BLENDER_SENTINEL!r})\n"
