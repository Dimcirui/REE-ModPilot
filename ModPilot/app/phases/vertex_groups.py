"""
Phase 3 — Vertex Group Conversion (plan.md video 3).

Renames mesh vertex groups from source naming (X) to target game naming (Y),
and renames armature bones to match. Also handles aux bone weight merging.

Must run AFTER Phase 2 (skeleton alignment).

Operators (in sequence):
  1. modder.direct_convert     — on selected MESH objects (vertex group rename)
  2. modder.rename_bones_to_target — on source ARMATURE (bone rename in EDIT mode)

Required params:
  x_preset        : str        — source model preset ("MMD" | "VRChat" | "终末地")
  mesh_objects    : list[str]  — Blender MESH object names to process
  source_armature : str        — ARMATURE object name whose bones get renamed
  y_preset        : str        — target game preset (default: "怪猎荒野")

Notes:
  - modder.direct_convert requires MESH objects selected, not the armature.
  - modder.rename_bones_to_target requires the ARMATURE as active_object.
  - Both operators use fuzzy name matching for bone names (_, ., spaces normalized).
  - spine_03 auto-fallback: if Y preset lacks spine_03, weights go to spine_02.
  - Name conflicts: conflicting target bones get _old suffix automatically.
"""

from __future__ import annotations

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.state import SceneCache
from app.phases.base import (
    DEFAULT_Y_PRESET,
    X_PRESETS,
    Y_PRESETS,
    PhaseError,
    PhaseResult,
    PhaseTool,
    require_finished,
)

_OP_CONVERT = "modder.direct_convert"
_OP_RENAME = "modder.rename_bones_to_target"


class VertexGroups(PhaseTool):
    """Phase 3: Vertex group rename + base bone rename."""

    @property
    def name(self) -> str:
        return "vertex_groups"

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

        y_preset = params.get("y_preset", DEFAULT_Y_PRESET)
        if y_preset not in Y_PRESETS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Unknown Y preset {y_preset!r}. Valid: {sorted(Y_PRESETS)}",
                )
            )

        mesh_objects: list[str] = params.get("mesh_objects", [])
        if not mesh_objects:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'mesh_objects' must be a non-empty list of MESH object names.",
                    suggestion=(
                        "Select the body/hair/clothing mesh objects, not the armature."
                    ),
                )
            )

        source_arm = params.get("source_armature", "")
        if not source_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'source_armature' param is required for bone renaming.",
                )
            )

        # ── entry spot-check ───────────────────────────────────────────────
        state_before = cache.refresh()

        # ── step 1: vertex group rename ────────────────────────────────────
        try:
            error = self._convert_vertex_groups(
                client, mesh_objects, x_preset, y_preset
            )
            if error is not None:
                return PhaseResult.fail(error)

            # ── step 2: bone rename ────────────────────────────────────────
            error = self._rename_bones(client, source_arm, x_preset, y_preset)
            if error is not None:
                return PhaseResult.fail(error)

        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=f"{_OP_CONVERT} / {_OP_RENAME}",
                    message="Blender returned an error during vertex group conversion.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator=f"{_OP_CONVERT} / {_OP_RENAME}",
                    message="Lost connection to Blender during vertex group conversion.",
                    raw=str(exc),
                )
            )

        # ── exit cache update ──────────────────────────────────────────────
        state_after = cache.refresh()
        return PhaseResult.ok(state_before.diff(state_after))

    # ── private helpers ────────────────────────────────────────────────────

    def _convert_vertex_groups(
        self,
        client: BlenderClient,
        mesh_objects: list[str],
        x_preset: str,
        y_preset: str,
    ) -> PhaseError | None:
        """
        Set presets, select mesh objects, run direct_convert.
        direct_convert processes all selected MESH objects at once.
        """
        # Build selection lines for each mesh object
        select_lines = "\n".join(
            f"    obj = bpy.data.objects.get({name!r})\n"
            f"    if obj is None:\n"
            f"        missing.append({name!r})\n"
            f"    elif obj.type != 'MESH':\n"
            f"        not_mesh.append({name!r})\n"
            f"    else:\n"
            f"        obj.select_set(True)\n"
            f"        last_mesh = obj\n"
            for name in mesh_objects
        )

        code = (
            f"import bpy\n"
            f"settings = bpy.context.scene.mhw_suite_settings\n"
            f"settings.import_preset_enum = {x_preset!r}\n"
            f"settings.target_preset_enum = {y_preset!r}\n"
            f"bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"bpy.ops.object.select_all(action='DESELECT')\n"
            f"missing = []\n"
            f"not_mesh = []\n"
            f"last_mesh = None\n"
            f"{select_lines}\n"
            f"if missing or not_mesh:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    detail = []\n"
            f"    if missing: detail.append('not_found:' + ','.join(missing))\n"
            f"    if not_mesh: detail.append('not_mesh:' + ','.join(not_mesh))\n"
            f"    print('PRECONDITION:' + '|'.join(detail))\n"
            f"elif last_mesh is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:no_valid_mesh_selected')\n"
            f"else:\n"
            f"    bpy.context.view_layer.objects.active = last_mesh\n"
            f"    ret = bpy.ops.{_OP_CONVERT}()\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(ret)\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("PRECONDITION:"):
            detail = lines[0][len("PRECONDITION:") :]
            return PhaseError(
                category="precondition",
                operator=_OP_CONVERT,
                message=f"Mesh object issue: {detail}",
                suggestion=(
                    "Select MESH objects (body, hair, clothing), not the ARMATURE. "
                    "Check names in Blender's Outliner."
                ),
            )
        return require_finished(lines, _OP_CONVERT)

    def _rename_bones(
        self,
        client: BlenderClient,
        source_arm: str,
        x_preset: str,
        y_preset: str,
    ) -> PhaseError | None:
        """
        Set presets, activate armature, run rename_bones_to_target.
        Conflicting target bone names are auto-resolved with _old suffix by the operator.
        """
        code = (
            f"import bpy\n"
            f"settings = bpy.context.scene.mhw_suite_settings\n"
            f"settings.import_preset_enum = {x_preset!r}\n"
            f"settings.target_preset_enum = {y_preset!r}\n"
            f"arm = bpy.data.objects.get({source_arm!r})\n"
            f"if arm is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:armature_not_found')\n"
            f"elif arm.type != 'ARMATURE':\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:not_an_armature')\n"
            f"else:\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    arm.select_set(True)\n"
            f"    bpy.context.view_layer.objects.active = arm\n"
            f"    ret = bpy.ops.{_OP_RENAME}()\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(ret)\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("PRECONDITION:"):
            detail = lines[0]
            return PhaseError(
                category="precondition",
                operator=_OP_RENAME,
                message=f"Armature issue: {detail}",
                suggestion=(
                    f"Ensure {source_arm!r} exists in the scene and is an ARMATURE object."
                ),
            )
        return require_finished(lines, _OP_RENAME)
