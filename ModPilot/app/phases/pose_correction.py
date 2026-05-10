"""
Phase 1 — Pose Correction (plan.md video 1).

A three-step deterministic pipeline applied before skeleton alignment (Phase 2):

  Step 1 — Pose reset
    Clear all pose transforms on the source armature so any residual bone
    rotations/locations from prior Blender operations do not pollute the result.
    Uses the built-in operator: bpy.ops.pose.transforms_clear()

  Step 2 — Mesh-bbox scale alignment  (skipped when skip_scale_align=True)
    Scale the source armature uniformly so its mesh height matches the target.
    Height = world-space Z_max across all MESH objects whose Armature modifier
    points to the given armature.  Assumption: feet are at Z≈0 for both models
    (holds for >95% of source models; set skip_scale_align=True for outliers).
    Applies the scale transform immediately so downstream operators see clean data.

  Step 3 — Deterministic pose conversion  (driven by x_preset, no LLM needed)
    MMD    → modder.tpose_direction
    VRChat → skip (already in T-pose)
    终末地 → modder.apply_transform_forward(transform_name="终末地")

Required params:
  x_preset         : str   — "MMD" | "VRChat" | "终末地"
  source_armature  : str   — Blender ARMATURE object name (source model)
  target_armature  : str   — Blender ARMATURE object name (MHWs reference skeleton)

Optional params:
  skip_scale_align : bool  — default False; set True if models are already scaled
"""

from __future__ import annotations

from typing import Any

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.state import SceneCache
from app.phases.base import (
    X_PRESETS,
    PhaseError,
    PhaseResult,
    PhaseTool,
    require_finished,
)

_OP_RESET = "pose.transforms_clear"
_OP_APPLY_SCALE = "object.transform_apply"
_OP_TPOSE_DIRECTION = "modder.tpose_direction"
_OP_APPLY_FORWARD = "modder.apply_transform_forward"
_ENDFIELD_TRANSFORM = "终末地"


class PoseCorrection(PhaseTool):
    """Phase 1: Deterministic 3-step pose correction pipeline."""

    @property
    def name(self) -> str:
        return "pose_correction"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "pose_correction",
            "description": (
                "Phase 1: Clear source armature pose transforms, scale model to match "
                "MHWs bounding box height, then apply T-pose conversion based on source "
                "type (MMD: arm rotation, VRChat: skip, Endfield: recorded pose). "
                "Run before skeleton_align."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "x_preset": {
                        "type": "string",
                        "enum": ["MMD", "VRChat", "终末地"],
                        "description": "Source model type.",
                    },
                    "source_armature": {
                        "type": "string",
                        "description": "Blender ARMATURE object name for the source model.",
                    },
                    "target_armature": {
                        "type": "string",
                        "description": (
                            "Blender ARMATURE object name for the MHWs reference skeleton. "
                            "Always 'MHWilds_Female Armature' after setup_import_mhwilds — "
                            "use this fixed value without asking the user."
                        ),
                    },
                    "skip_scale_align": {
                        "type": "boolean",
                        "description": "Skip bbox scale step if models are already scaled. Default: false.",
                    },
                },
                "required": ["x_preset", "source_armature", "target_armature"],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        # ── param validation ───────────────────────────────────────────────
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

        target_arm = params.get("target_armature", "")
        if not target_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'target_armature' param is required (MHWs reference skeleton).",
                    suggestion=(
                        "Import the MHWs female skeleton first via "
                        "bpy.ops.mbt.import_mhwilds_fmesh() with convert_to_tpose=True."
                    ),
                )
            )

        skip_scale = bool(params.get("skip_scale_align", False))

        # ── entry spot-check ───────────────────────────────────────────────
        state_before = cache.refresh()

        # ── pipeline ───────────────────────────────────────────────────────
        try:
            # Step 1 — pose reset
            err = self._pose_reset(client, source_arm)
            if err is not None:
                return PhaseResult.fail(err)

            # Step 2 — mesh-bbox scale alignment
            if not skip_scale:
                err = self._scale_align(client, source_arm, target_arm)
                if err is not None:
                    return PhaseResult.fail(err)

            # Step 3 — deterministic pose conversion
            err = self._pose_convert(client, source_arm, x_preset)
            if err is not None:
                return PhaseResult.fail(err)

        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator="pose_correction pipeline",
                    message="Blender returned an error during pose correction.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator="pose_correction pipeline",
                    message="Lost connection to Blender during pose correction.",
                    raw=str(exc),
                )
            )

        # ── exit cache update ──────────────────────────────────────────────
        state_after = cache.refresh()
        return PhaseResult.ok(state_before.diff(state_after))

    # ── private helpers ────────────────────────────────────────────────────

    def _pose_reset(
        self,
        client: BlenderClient,
        source_arm: str,
    ) -> PhaseError | None:
        """
        Switch to Pose Mode, select all bones, clear all transforms.
        Removes any residual pose rotations/locations before scale/pose conversion.
        """
        code = (
            f"import bpy\n"
            f"arm = bpy.data.objects.get({source_arm!r})\n"
            f"if arm is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:armature_not_found')\n"
            f"elif arm.type != 'ARMATURE':\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:not_an_armature')\n"
            f"else:\n"
            f"    bpy.context.view_layer.objects.active = arm\n"
            f"    bpy.ops.object.mode_set(mode='POSE')\n"
            f"    bpy.ops.pose.select_all(action='SELECT')\n"
            f"    ret = bpy.ops.pose.transforms_clear()\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(ret)\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("PRECONDITION:"):
            detail = lines[0][len("PRECONDITION:"):]
            return PhaseError(
                category="precondition",
                operator=_OP_RESET,
                message=f"Armature issue during pose reset ({detail}): {source_arm!r}",
                suggestion="Verify the armature name in Blender's Outliner.",
            )
        return require_finished(lines, _OP_RESET)

    def _scale_align(
        self,
        client: BlenderClient,
        source_arm: str,
        target_arm: str,
    ) -> PhaseError | None:
        """
        Compute world-space Z_max of bound meshes for both armatures, derive a
        uniform scale ratio, apply it to the source armature, then bake the scale
        with object.transform_apply so downstream operators see clean data.

        Uses Z_max as the height proxy under the assumption that feet are at Z≈0.
        Returns None on success; PhaseError on PRECONDITION or zero-height edge case.
        """
        code = (
            f"import bpy\n"
            f"from mathutils import Vector\n"
            f"src_arm = bpy.data.objects.get({source_arm!r})\n"
            f"tgt_arm = bpy.data.objects.get({target_arm!r})\n"
            f"missing = []\n"
            f"if src_arm is None: missing.append({source_arm!r})\n"
            f"if tgt_arm is None: missing.append({target_arm!r})\n"
            f"if missing:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:objects_not_found:' + ','.join(missing))\n"
            f"else:\n"
            # Step 0: apply any unapplied scale on source armature + mesh children so
            # that matrix_world reflects the true visual size before computing the ratio.
            # Output from transform_apply lands before the sentinel and is discarded.
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    src_arm.select_set(True)\n"
            f"    for _ch in src_arm.children:\n"
            f"        if _ch.type == 'MESH': _ch.select_set(True)\n"
            f"    bpy.context.view_layer.objects.active = src_arm\n"
            f"    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)\n"
            f"    bpy.context.view_layer.update()\n"
            # Helper: collect meshes bound to arm_obj via Armature modifier
            f"    def bound_meshes(arm_obj):\n"
            f"        return [o for o in bpy.data.objects\n"
            f"                if o.type == 'MESH' and any(\n"
            f"                    m.type == 'ARMATURE' and m.object == arm_obj\n"
            f"                    for m in o.modifiers)]\n"
            # Helper: world-space Z_max across all bound meshes
            f"    def z_max(meshes):\n"
            f"        zmax = 0.0\n"
            f"        for mesh in meshes:\n"
            f"            for corner in mesh.bound_box:\n"
            f"                z = (mesh.matrix_world @ Vector(corner)).z\n"
            f"                if z > zmax:\n"
            f"                    zmax = z\n"
            f"        return zmax\n"
            f"    src_meshes = bound_meshes(src_arm)\n"
            f"    tgt_meshes = bound_meshes(tgt_arm)\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    if not src_meshes:\n"
            f"        print('PRECONDITION:no_source_meshes')\n"
            f"    elif not tgt_meshes:\n"
            f"        print('PRECONDITION:no_target_meshes')\n"
            f"    else:\n"
            f"        src_h = z_max(src_meshes)\n"
            f"        tgt_h = z_max(tgt_meshes)\n"
            f"        if src_h <= 0:\n"
            f"            print('PRECONDITION:source_height_zero')\n"
            f"        elif tgt_h <= 0:\n"
            f"            print('PRECONDITION:target_height_zero')\n"
            f"        else:\n"
            f"            ratio = tgt_h / src_h\n"
            f"            src_arm.scale = (ratio, ratio, ratio)\n"
            f"            bpy.context.view_layer.objects.active = src_arm\n"
            f"            bpy.ops.object.select_all(action='DESELECT')\n"
            f"            src_arm.select_set(True)\n"
            f"            bpy.ops.object.transform_apply(\n"
            f"                location=False, rotation=False, scale=True)\n"
            f"            print(f'SCALE_OK:{{ratio:.4f}}')\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("PRECONDITION:"):
            detail = lines[0][len("PRECONDITION:"):]
            return PhaseError(
                category="precondition",
                operator=_OP_APPLY_SCALE,
                message=f"Mesh-bbox scale alignment failed: {detail}",
                suggestion=(
                    "Ensure both armatures have MESH objects with an Armature modifier. "
                    "If models are already correctly scaled, set skip_scale_align=True."
                ),
            )
        # SCALE_OK or any non-PRECONDITION output → success
        return None

    def _pose_convert(
        self,
        client: BlenderClient,
        source_arm: str,
        x_preset: str,
    ) -> PhaseError | None:
        """
        Apply preset-specific pose conversion.

        VRChat : no-op (already T-pose).
        MMD    : modder.tpose_direction (rotates upper arms to T-pose).
        终末地 : modder.apply_transform_forward with transform_name="终末地".
        """
        if x_preset == "VRChat":
            return None  # T-pose by convention; no operator needed

        if x_preset == "MMD":
            op = _OP_TPOSE_DIRECTION
            code = (
                f"import bpy\n"
                f"bpy.context.scene.mhw_suite_settings.pose_import_preset_enum = {(x_preset + '.json')!r}\n"
                f"obj = bpy.data.objects.get({source_arm!r})\n"
                f"if obj is None:\n"
                f"    print({BLENDER_SENTINEL!r})\n"
                f"    print('PRECONDITION:armature_not_found')\n"
                f"else:\n"
                f"    bpy.context.view_layer.objects.active = obj\n"
                f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
                f"    ret = bpy.ops.{op}()\n"
                f"    print({BLENDER_SENTINEL!r})\n"
                f"    print(ret)\n"
            )
        else:  # 终末地
            op = _OP_APPLY_FORWARD
            code = (
                f"import bpy\n"
                f"settings = bpy.context.scene.mhw_suite_settings\n"
                f"settings.pose_import_preset_enum = {(x_preset + '.json')!r}\n"
                f"settings.pose_preset_enum = {(_ENDFIELD_TRANSFORM + '.json')!r}\n"
                f"obj = bpy.data.objects.get({source_arm!r})\n"
                f"if obj is None:\n"
                f"    print({BLENDER_SENTINEL!r})\n"
                f"    print('PRECONDITION:armature_not_found')\n"
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
                message=f"Pose conversion ({x_preset!r}) failed: armature {source_arm!r} not found.",
                suggestion="Check the armature name in Blender's Outliner.",
            )
        return require_finished(lines, op)
