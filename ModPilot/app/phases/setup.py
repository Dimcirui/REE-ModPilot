"""
Setup phase — scene validation and MHWilds Female armature import.

Runs before Phase 1. Two sequential tools:
  setup_validate_scene  : checks source model scene state; fails on invalid layout
  setup_import_mhwilds  : imports MHWilds_Female.mesh collection via mbt.import_mhwilds_fmesh

After setup_validate_scene succeeds the agent must ask the user to confirm before
calling setup_import_mhwilds — the two tools intentionally run in separate turns.
"""

from __future__ import annotations

import json
from typing import Any

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.state import SceneCache
from app.phases.base import PhaseError, PhaseResult, PhaseTool

_OP_IMPORT = "mbt.import_mhwilds_fmesh"
_MHWILDS_COLLECTION = "MHWilds_Female.mesh"
_MHWILDS_ARMATURE = "MHWilds_Female Armature"


class SetupValidateScene(PhaseTool):
    """
    Validate that the scene contains a single source model.

    Exclusion: objects inside the MHWilds_Female.mesh collection are ignored.
    Valid layout: exactly 1 ARMATURE + N MESH children of that armature, nothing else.
    """

    @property
    def name(self) -> str:
        return "setup_validate_scene"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "setup_validate_scene",
            "description": (
                "Setup step 1: validate the Blender scene contains exactly one source model "
                "(one ARMATURE with MESH children, no stray objects), excluding the "
                "MHWilds_Female.mesh collection if already present. "
                "Call this first on any session start. "
                "Report the result to the user and wait for confirmation before calling "
                "setup_import_mhwilds."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    def run(self, client: BlenderClient, cache: SceneCache, params: dict) -> PhaseResult:
        code = (
            "import bpy, json\n"
            f"_SEN = {BLENDER_SENTINEL!r}\n"
            f"_MHWILDS = {_MHWILDS_COLLECTION!r}\n"
            # Collect names of objects that belong to the MHWilds collection
            "mhwilds_col = bpy.data.collections.get(_MHWILDS)\n"
            "excluded = set()\n"
            "if mhwilds_col:\n"
            "    for _o in mhwilds_col.all_objects: excluded.add(_o.name)\n"
            # Remaining scene objects
            "remaining = [o for o in bpy.context.scene.objects if o.name not in excluded]\n"
            "arms   = [o for o in remaining if o.type == 'ARMATURE']\n"
            "meshes = [o for o in remaining if o.type == 'MESH']\n"
            "others = [o for o in remaining if o.type not in ('ARMATURE', 'MESH')]\n"
            "valid = False; errors = []; src = None; kids = []\n"
            "if not arms:\n"
            "    errors.append('No armature found (excluding MHWilds collection).')\n"
            "elif len(arms) > 1:\n"
            "    errors.append('Expected 1 armature, found ' + str(len(arms))"
            " + ': ' + str([a.name for a in arms]))\n"
            "else:\n"
            "    src = arms[0].name\n"
            "    kids = [m.name for m in meshes if m.parent == arms[0]]\n"
            "    no_par = [m.name for m in meshes if m.parent != arms[0]]\n"
            "    strays = [o.name for o in others]\n"
            "    if no_par: errors.append('Meshes not parented to armature: ' + str(no_par))\n"
            "    if strays: errors.append('Stray non-mesh/armature objects: ' + str(strays))\n"
            "    if not errors: valid = True\n"
            "print(_SEN)\n"
            "print(json.dumps({'valid': valid, 'errors': errors,\n"
            "    'source_armature': src, 'child_meshes': kids,\n"
            "    'mhwilds_imported': mhwilds_col is not None}))\n"
        )

        try:
            lines = client.execute_and_extract(code)
        except BlenderError as exc:
            return PhaseResult.fail(PhaseError(
                category="unexpected",
                operator="setup_validate_scene",
                message="Blender error during scene validation.",
                raw=str(exc),
            ))
        except OSError as exc:
            return PhaseResult.fail(PhaseError(
                category="timeout",
                operator="setup_validate_scene",
                message="Lost connection to Blender during scene validation.",
                raw=str(exc),
            ))

        if not lines:
            return PhaseResult.fail(PhaseError(
                category="unexpected",
                operator="setup_validate_scene",
                message="Blender returned no output from scene validation.",
            ))

        try:
            result = json.loads(lines[0])
        except json.JSONDecodeError:
            return PhaseResult.fail(PhaseError(
                category="unexpected",
                operator="setup_validate_scene",
                message=f"Unparseable validation output: {lines[0]!r}",
            ))

        if not result.get("valid"):
            errors = result.get("errors", [])
            return PhaseResult.fail(PhaseError(
                category="precondition",
                operator="setup_validate_scene",
                message="Scene validation failed: " + "; ".join(errors),
                suggestion=(
                    "Ensure the scene contains exactly one source armature whose child objects "
                    "are all MESH type, with no other objects (EMPTYs, cameras, lights, etc.) "
                    "outside the MHWilds_Female.mesh collection."
                ),
            ))

        return PhaseResult.ok(result)


class SetupImportMHWilds(PhaseTool):
    """
    Import the MHWilds Female reference skeleton via Modder-Batch-Tool.

    Creates the MHWilds_Female.mesh collection, which is the central target
    collection for all downstream phases (VertexGroups, Material, BatchExport).
    Skips the import operator if the collection already exists.
    """

    @property
    def name(self) -> str:
        return "setup_import_mhwilds"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "setup_import_mhwilds",
            "description": (
                "Setup step 2: import the MHWilds Female reference skeleton "
                "(creates MHWilds_Female.mesh collection) via mbt.import_mhwilds_fmesh. "
                "Skips if the collection already exists. "
                "Call only after setup_validate_scene succeeds AND the user confirms. "
                "The resulting MHWilds_Female.mesh collection is the central target "
                "collection used by all downstream phases."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "convert_to_tpose": {
                        "type": "boolean",
                        "description": "Convert imported skeleton to T-pose. Default: true.",
                    },
                    "merge_facial_bones": {
                        "type": "boolean",
                        "description": "Merge ~200 facial bones into their parents. Default: true.",
                    },
                },
                "required": [],
            },
        }

    def run(self, client: BlenderClient, cache: SceneCache, params: dict) -> PhaseResult:
        convert_to_tpose = bool(params.get("convert_to_tpose", True))
        merge_facial_bones = bool(params.get("merge_facial_bones", True))

        state_before = cache.refresh()

        code = (
            "import bpy, json\n"
            f"_SEN = {BLENDER_SENTINEL!r}\n"
            f"_COL = {_MHWILDS_COLLECTION!r}\n"
            # Skip if already present
            "if bpy.data.collections.get(_COL) is not None:\n"
            "    print(_SEN)\n"
            "    print(json.dumps({'status': 'already_imported'}))\n"
            "else:\n"
            # Ensure Object mode before calling the operator
            "    if bpy.context.mode != 'OBJECT':\n"
            "        bpy.ops.object.mode_set(mode='OBJECT')\n"
            "    panel = bpy.context.scene.mbt_toolpanel\n"
            f"    panel.mhwilds_convert_to_tpose = {convert_to_tpose}\n"
            f"    panel.mhwilds_merge_facial_bones = {merge_facial_bones}\n"
            "    ret = bpy.ops.mbt.import_mhwilds_fmesh()\n"
            "    ok = 'FINISHED' in str(ret)\n"
            "    print(_SEN)\n"
            "    print(json.dumps({'status': 'imported' if ok else 'cancelled',\n"
            "        'operator_result': str(ret)}))\n"
        )

        try:
            lines = client.execute_and_extract(code)
        except BlenderError as exc:
            return PhaseResult.fail(PhaseError(
                category="unexpected",
                operator=_OP_IMPORT,
                message="Blender error during MHWilds armature import.",
                raw=str(exc),
            ))
        except OSError as exc:
            return PhaseResult.fail(PhaseError(
                category="timeout",
                operator=_OP_IMPORT,
                message="Lost connection to Blender during MHWilds armature import.",
                raw=str(exc),
            ))

        if not lines:
            return PhaseResult.fail(PhaseError(
                category="unexpected",
                operator=_OP_IMPORT,
                message="Blender returned no output from import operation.",
            ))

        try:
            result = json.loads(lines[0])
        except json.JSONDecodeError:
            return PhaseResult.fail(PhaseError(
                category="unexpected",
                operator=_OP_IMPORT,
                message=f"Unparseable import output: {lines[0]!r}",
            ))

        if result.get("status") == "cancelled":
            return PhaseResult.fail(PhaseError(
                category="operator_failed",
                operator=_OP_IMPORT,
                message="MHWilds armature import was cancelled by Blender.",
                suggestion=(
                    "Verify Modder-Batch-Tool addon is installed and enabled. "
                    "Check that games/MHWilds/model/MHWilds_Female.fbx exists "
                    "relative to the addon's installation directory."
                ),
                raw=result.get("operator_result", ""),
            ))

        # "imported" or "already_imported" — both are success
        status = result.get("status")
        state_after = cache.refresh()
        diff = state_before.diff(state_after) if status == "imported" else {}
        diff.update({
            "mhwilds_collection": _MHWILDS_COLLECTION,
            "mhwilds_armature": _MHWILDS_ARMATURE,
            "import_status": status,
        })
        return PhaseResult.ok(diff)
