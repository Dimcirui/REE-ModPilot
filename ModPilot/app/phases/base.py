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
from dataclasses import dataclass, field
from typing import Any

from app.blender.client import BlenderClient, BlenderError
from app.blender.state import SceneCache, SceneState

# ── valid preset identifiers ───────────────────────────────────────────────

#: Source-model X presets available in assets/presets/import/
X_PRESETS: frozenset[str] = frozenset({"MMD", "VRChat", "终末地"})

#: Target-game Y presets available in assets/presets/bone/ (MVP: MHWs only)
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
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier used by the agent loop (e.g. 'pose_correction')."""
        ...

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
