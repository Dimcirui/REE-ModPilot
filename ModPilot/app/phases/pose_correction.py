"""
Phase 1 — Pose Correction (plan.md video 1).

Adjusts the source model's pose to roughly match the target game skeleton,
before skeleton alignment (Phase 2).

Three sub-tools — the agent loop classifies which to use (E17):
  "direction"      modder.tpose_direction       Simple A→T: rotate upper arms
  "matrix_zero"    modder.tpose_matrix_zero     RE Engine limb matrix reset (not RE9)
  "apply_forward"  modder.apply_transform_forward  Apply recorded pose transform A→B
  "apply_inverse"  modder.apply_transform_inverse  Apply recorded pose transform B→A
  "record"         modder.record_transform      Record delta between two poses

Classification heuristic (for agent loop):
  MMD / simple A-Pose          → "direction" first, manual touch-up
  RE Engine game target        → "matrix_zero"
  Multiple same-type models    → "record" then "apply_forward"

Required params per tool:
  All tools:
    x_preset        : str  — one of X_PRESETS ("MMD" | "VRChat" | "终末地")
    source_armature : str  — Blender object name of the source ARMATURE

  "record" additionally:
    target_armature : str  — B-pose ARMATURE object name (becomes active)
    transform_name  : str  — save filename (no extension) for the JSON

  "apply_forward" / "apply_inverse" additionally:
    transform_name  : str  — name of saved JSON to apply
"""

from __future__ import annotations

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.state import SceneCache
from app.phases.base import (
    DEFAULT_Y_PRESET,
    X_PRESETS,
    PhaseError,
    PhaseResult,
    PhaseTool,
    require_finished,
)

_VALID_TOOLS = frozenset(
    {"direction", "matrix_zero", "apply_forward", "apply_inverse", "record"}
)


class PoseCorrection(PhaseTool):
    """Phase 1: Pose correction before skeleton alignment."""

    @property
    def name(self) -> str:
        return "pose_correction"

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        # ── param validation ───────────────────────────────────────────────
        tool = params.get("tool", "")
        if tool not in _VALID_TOOLS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Unknown pose tool {tool!r}. Valid: {sorted(_VALID_TOOLS)}",
                )
            )

        x_preset = params.get("x_preset", "")
        if x_preset not in X_PRESETS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Unknown X preset {x_preset!r}. Valid: {sorted(X_PRESETS)}",
                    suggestion="Choose from MMD, VRChat, or 终末地.",
                )
            )

        source_arm = params.get("source_armature", "")
        if not source_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'source_armature' param is required.",
                )
            )

        # ── entry spot-check ───────────────────────────────────────────────
        state_before = cache.refresh()

        # ── dispatch ───────────────────────────────────────────────────────
        try:
            if tool in ("direction", "matrix_zero"):
                result = self._run_single_arm_tool(client, tool, source_arm, x_preset)
            elif tool in ("apply_forward", "apply_inverse"):
                transform_name = params.get("transform_name", "")
                if not transform_name:
                    return PhaseResult.fail(
                        PhaseError(
                            category="precondition",
                            operator="",
                            message="'transform_name' required for apply_forward/apply_inverse.",
                        )
                    )
                result = self._run_apply_tool(client, tool, source_arm, x_preset, transform_name)
            else:  # "record"
                target_arm = params.get("target_armature", "")
                transform_name = params.get("transform_name", "")
                if not target_arm or not transform_name:
                    return PhaseResult.fail(
                        PhaseError(
                            category="precondition",
                            operator="",
                            message="'target_armature' and 'transform_name' required for record.",
                        )
                    )
                result = self._run_record(client, source_arm, target_arm, transform_name)
        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=_operator_name(tool),
                    message="Blender returned an error during pose correction.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator=_operator_name(tool),
                    message="Lost connection to Blender during pose correction.",
                    raw=str(exc),
                )
            )

        if result is not None:
            return PhaseResult.fail(result)

        # ── exit cache update ──────────────────────────────────────────────
        state_after = cache.refresh()
        return PhaseResult.ok(state_before.diff(state_after))

    # ── private helpers ────────────────────────────────────────────────────

    def _run_single_arm_tool(
        self,
        client: BlenderClient,
        tool: str,
        source_arm: str,
        x_preset: str,
    ) -> PhaseError | None:
        """Run tpose_direction or tpose_matrix_zero."""
        op = "modder.tpose_direction" if tool == "direction" else "modder.tpose_matrix_zero"
        code = (
            f"import bpy\n"
            f"bpy.context.scene.mhw_suite_settings.pose_import_preset_enum = {x_preset!r}\n"
            f"obj = bpy.data.objects.get({source_arm!r})\n"
            f"if obj is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:object_not_found')\n"
            f"else:\n"
            f"    bpy.context.view_layer.objects.active = obj\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    ret = bpy.ops.{op}()\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(ret)\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("PRECONDITION:"):
            return PhaseError(
                category="precondition",
                operator=op,
                message=f"Object {source_arm!r} not found in Blender scene.",
                suggestion="Check the object name in Blender's outliner.",
            )
        return require_finished(lines, op)

    def _run_apply_tool(
        self,
        client: BlenderClient,
        tool: str,
        source_arm: str,
        x_preset: str,
        transform_name: str,
    ) -> PhaseError | None:
        """Run apply_transform_forward or apply_transform_inverse."""
        op = (
            "modder.apply_transform_forward"
            if tool == "apply_forward"
            else "modder.apply_transform_inverse"
        )
        code = (
            f"import bpy\n"
            f"settings = bpy.context.scene.mhw_suite_settings\n"
            f"settings.pose_import_preset_enum = {x_preset!r}\n"
            f"settings.pose_preset_enum = {transform_name!r}\n"
            f"obj = bpy.data.objects.get({source_arm!r})\n"
            f"if obj is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:object_not_found')\n"
            f"else:\n"
            f"    bpy.context.view_layer.objects.active = obj\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    ret = bpy.ops.{op}()\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(ret)\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("PRECONDITION:"):
            return PhaseError(
                category="precondition",
                operator=op,
                message=f"Object {source_arm!r} not found in Blender scene.",
                suggestion="Check the object name in Blender's outliner.",
            )
        return require_finished(lines, op)

    def _run_record(
        self,
        client: BlenderClient,
        source_arm: str,
        target_arm: str,
        transform_name: str,
    ) -> PhaseError | None:
        """Run record_transform (requires two armatures selected)."""
        op = "modder.record_transform"
        code = (
            f"import bpy\n"
            f"src = bpy.data.objects.get({source_arm!r})\n"
            f"tgt = bpy.data.objects.get({target_arm!r})\n"
            f"if src is None or tgt is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    missing = [] \n"
            f"    if src is None: missing.append({source_arm!r})\n"
            f"    if tgt is None: missing.append({target_arm!r})\n"
            f"    print('PRECONDITION:objects_not_found:' + ','.join(missing))\n"
            f"else:\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    src.select_set(True)\n"
            f"    tgt.select_set(True)\n"
            f"    bpy.context.view_layer.objects.active = tgt\n"
            f"    ret = bpy.ops.{op}(preset_name={transform_name!r})\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(ret)\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("PRECONDITION:"):
            missing = lines[0].split(":", 2)[-1] if ":" in lines[0] else "unknown"
            return PhaseError(
                category="precondition",
                operator=op,
                message=f"Objects not found in scene: {missing}",
                suggestion="Verify both armature names in Blender's outliner.",
            )
        return require_finished(lines, op)


# ── helpers ────────────────────────────────────────────────────────────────


def _operator_name(tool: str) -> str:
    return {
        "direction": "modder.tpose_direction",
        "matrix_zero": "modder.tpose_matrix_zero",
        "apply_forward": "modder.apply_transform_forward",
        "apply_inverse": "modder.apply_transform_inverse",
        "record": "modder.record_transform",
    }.get(tool, tool)
