"""
Phase 6 — Batch Export (MHWs)

Exports mesh + mdf2 + chain2 files to the Natives directory, then runs
BoneSystem export for the MHWs armature — all in a single operator call.

All configuration is done via Blender scene properties; no dialog interaction
is required. bpy.ops.mhws.batch_export() reads everything from scene state.

Part IDs (games/mhws/batch_export.py:17-24):
  "1" = Arms, "2" = Body, "3" = Helmet, "4" = Legs, "5" = Waist

Binding key format:
  scene["mhws_{armor_id}_{part_id}_{filetype}"] = collection_name
  Filetypes: "mesh", "mdf2", "chain2"  (clsp → leave unbound = empty model)

Armor variant encodes both hunter gender and armor gender (main_panel.py:123-138):
  "ff" = female hunter + female armor  (default for character mods)
  "fm" = female hunter + male armor
  "mf" = male hunter + female armor
  "mm" = male hunter + male armor

BoneSystem is triggered by setting mhws_use_bonesystem = True before calling
batch_export(). The operator calls _do_bonesystem_export() at the end of its
execute() (batch_export.py:358-365). No separate bonesystem operator exists.
"""

from __future__ import annotations

from typing import Any

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.state import SceneCache
from app.phases.base import PhaseError, PhaseResult, PhaseTool, require_finished

# ── constants ─────────────────────────────────────────────────────────────────

VALID_PARTS: frozenset[str] = frozenset({"1", "2", "3", "4", "5"})
PART_NAMES: dict[str, str] = {
    "1": "Arms",
    "2": "Body",
    "3": "Helmet",
    "4": "Legs",
    "5": "Waist",
}
VALID_VARIANTS: frozenset[str] = frozenset({"mm", "mf", "fm", "ff"})

#: Filetypes bound per part; clsp is intentionally excluded (empty model)
EXPORT_FILETYPES: tuple[str, ...] = ("mesh", "mdf2", "chain2")

DEFAULT_ARMOR_SCHEME: str = "mhws_armor_sets.json"

_OP_EXPORT = "mhws.batch_export"


# ── Phase 6 ───────────────────────────────────────────────────────────────────


class BatchExport(PhaseTool):
    """
    Phase 6: Batch export MHWs mod files (mesh + mdf2 + chain2) and BoneSystem.

    Pipeline:
      Step 1 — Validate all named collections and armature exist in the scene.
      Step 2 — Clear all previous bindings for this armor_id (all 5 parts ×
                all filetypes) to prevent stale data from polluting the export.
      Step 3 — Write collection bindings for target_parts only; other parts
                remain unbound so the exporter auto-generates empty placeholder
                files for them.
      Step 4 — Configure armor + BoneSystem scene properties.
      Step 5 — Call bpy.ops.mhws.batch_export() once; this operator exports
                all bound parts and runs BoneSystem at the end when
                mhws_use_bonesystem=True.
    """

    @property
    def name(self) -> str:
        return "batch_export"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "batch_export",
            "description": (
                "Phase 6: Export MHWs mod files (mesh, mdf2, chain2) to the Natives "
                "directory and run BoneSystem export. Requires all Phase 1-5 outputs "
                "to be complete."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "armor_id": {
                        "type": "string",
                        "description": (
                            "Armor equipment ID as it appears in the armor scheme "
                            "(e.g. 'pl001'). Case-sensitive."
                        ),
                    },
                    "armor_variant": {
                        "type": "string",
                        "enum": ["ff", "fm", "mf", "mm"],
                        "description": (
                            "Hunter gender + armor gender: first letter = hunter "
                            "(f=female, m=male), second = armor. 'ff' is default "
                            "for female character mods."
                        ),
                    },
                    "target_parts": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["1", "2", "3", "4", "5"]},
                        "minItems": 1,
                        "description": (
                            "Part IDs to export: 1=Arms 2=Body 3=Helmet 4=Legs 5=Waist. "
                            "Bound collections are written to these parts; all other parts "
                            "receive empty placeholder files automatically."
                        ),
                    },
                    "mesh_collection": {
                        "type": "string",
                        "description": "Blender Collection name containing RE_MESH objects.",
                    },
                    "mdf2_collection": {
                        "type": "string",
                        "description": "Blender Collection name containing RE_MDF objects (from Phase 5).",
                    },
                    "chain2_collection": {
                        "type": "string",
                        "description": "Blender Collection name containing RE_CHAIN objects (from Phase 4B).",
                    },
                    "target_armature": {
                        "type": "string",
                        "description": "MHWs ARMATURE object name for BoneSystem export.",
                    },
                    "fbxskel_name": {
                        "type": "string",
                        "description": (
                            "BoneSystem FBXSkel definition name (e.g. 'ch03_000_9000'). "
                            "Must match the fbxskel file in assets/mhws/bonesystem/."
                        ),
                    },
                    "natives_root": {
                        "type": "string",
                        "description": (
                            "Absolute path to the directory that CONTAINS the 'natives' "
                            "folder (i.e. the mod root). Use forward slashes or raw paths."
                        ),
                    },
                    "armor_scheme": {
                        "type": "string",
                        "description": (
                            f"Armor scheme JSON filename. Defaults to '{DEFAULT_ARMOR_SCHEME}'."
                        ),
                    },
                },
                "required": [
                    "armor_id",
                    "armor_variant",
                    "target_parts",
                    "mesh_collection",
                    "mdf2_collection",
                    "chain2_collection",
                    "target_armature",
                    "fbxskel_name",
                    "natives_root",
                ],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        # ── param validation ───────────────────────────────────────────────
        armor_id = params.get("armor_id", "")
        if not armor_id:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'armor_id' is required (e.g. 'pl001').",
                )
            )

        armor_variant = params.get("armor_variant", "")
        if armor_variant not in VALID_VARIANTS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=(
                        f"'armor_variant' must be one of {sorted(VALID_VARIANTS)}, "
                        f"got {armor_variant!r}."
                    ),
                )
            )

        target_parts: list[str] = params.get("target_parts", [])
        if not target_parts:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'target_parts' must be a non-empty list.",
                    suggestion="e.g. ['2'] for Body, ['1','2','3','4','5'] for all parts.",
                )
            )
        invalid_parts = [p for p in target_parts if p not in VALID_PARTS]
        if invalid_parts:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Invalid part IDs: {invalid_parts}. Valid: {sorted(VALID_PARTS)}",
                )
            )

        mesh_col = params.get("mesh_collection", "")
        mdf2_col = params.get("mdf2_collection", "")
        chain2_col = params.get("chain2_collection", "")
        target_arm = params.get("target_armature", "")
        fbxskel_name = params.get("fbxskel_name", "")
        natives_root = params.get("natives_root", "")
        armor_scheme = params.get("armor_scheme", DEFAULT_ARMOR_SCHEME)

        missing_str = [
            name
            for name, val in [
                ("mesh_collection", mesh_col),
                ("mdf2_collection", mdf2_col),
                ("chain2_collection", chain2_col),
                ("target_armature", target_arm),
                ("fbxskel_name", fbxskel_name),
                ("natives_root", natives_root),
            ]
            if not val
        ]
        if missing_str:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Required params missing or empty: {missing_str}",
                )
            )

        state_before = cache.refresh()

        try:
            # Step 1 — validate scene objects
            err = self._validate_scene(client, mesh_col, mdf2_col, chain2_col, target_arm)
            if err is not None:
                return PhaseResult.fail(err)

            # Step 2+3+4 — configure scene (clear old, write new bindings + settings)
            err = self._configure_scene(
                client,
                armor_id=armor_id,
                armor_variant=armor_variant,
                armor_scheme=armor_scheme,
                target_parts=target_parts,
                mesh_col=mesh_col,
                mdf2_col=mdf2_col,
                chain2_col=chain2_col,
                target_arm=target_arm,
                fbxskel_name=fbxskel_name,
                natives_root=natives_root,
            )
            if err is not None:
                return PhaseResult.fail(err)

            # Step 5 — run export
            err = self._run_export(client)
            if err is not None:
                return PhaseResult.fail(err)

        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=_OP_EXPORT,
                    message="Blender error during batch export.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator=_OP_EXPORT,
                    message="Lost connection to Blender during batch export.",
                    raw=str(exc),
                )
            )

        state_after = cache.refresh()
        diff = state_before.diff(state_after)
        diff["exported_parts"] = {p: PART_NAMES[p] for p in target_parts}
        diff["armor_id"] = armor_id
        diff["armor_variant"] = armor_variant
        diff["fbxskel_name"] = fbxskel_name
        return PhaseResult.ok(diff)

    # ── private helpers ────────────────────────────────────────────────────

    def _validate_scene(
        self,
        client: BlenderClient,
        mesh_col: str,
        mdf2_col: str,
        chain2_col: str,
        target_arm: str,
    ) -> PhaseError | None:
        code = (
            f"import bpy\n"
            f"missing = []\n"
            f"if bpy.data.collections.get({mesh_col!r}) is None:\n"
            f"    missing.append('mesh_collection:{mesh_col}')\n"
            f"if bpy.data.collections.get({mdf2_col!r}) is None:\n"
            f"    missing.append('mdf2_collection:{mdf2_col}')\n"
            f"if bpy.data.collections.get({chain2_col!r}) is None:\n"
            f"    missing.append('chain2_collection:{chain2_col}')\n"
            f"if bpy.data.objects.get({target_arm!r}) is None:\n"
            f"    missing.append('target_armature:{target_arm}')\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"if missing:\n"
            f"    print('PRECONDITION:' + '|'.join(missing))\n"
            f"else:\n"
            f"    print('OK')\n"
        )
        lines = client.execute_and_extract(code)
        if not lines:
            return PhaseError(
                category="operator_failed",
                operator="",
                message="Scene validation returned no output from Blender.",
            )
        if lines[0].startswith("PRECONDITION:"):
            detail = lines[0][len("PRECONDITION:"):]
            return PhaseError(
                category="precondition",
                operator="",
                message=f"Required scene objects not found: {detail}",
                suggestion=(
                    "Ensure all Phase 1-5 outputs exist: merged mesh collection, "
                    "MDF2 collection (Phase 5), chain2 collection (Phase 4B), "
                    "and MHWs armature."
                ),
            )
        return None

    def _configure_scene(
        self,
        client: BlenderClient,
        *,
        armor_id: str,
        armor_variant: str,
        armor_scheme: str,
        target_parts: list[str],
        mesh_col: str,
        mdf2_col: str,
        chain2_col: str,
        target_arm: str,
        fbxskel_name: str,
        natives_root: str,
    ) -> PhaseError | None:
        # Build the binding statements inline:
        # 1. Clear all existing bindings for this armor_id (all 5 parts × all filetypes)
        # 2. Write bindings only for target_parts
        clear_lines = "\n".join(
            f"scene.pop('mhws_{armor_id}_{part}_{ft}', None)"
            for part in ("1", "2", "3", "4", "5")
            for ft in EXPORT_FILETYPES
        )
        bind_lines = "\n".join(
            f"scene['mhws_{armor_id}_{part}_{ft}'] = {col!r}"
            for part in target_parts
            for ft, col in [
                ("mesh", mesh_col),
                ("mdf2", mdf2_col),
                ("chain2", chain2_col),
            ]
        )
        code = (
            f"import bpy\n"
            f"scene = bpy.context.scene\n"
            f"s = scene.mhw_suite_settings\n"
            # ── clear old bindings ────────────────────────────────────────
            f"{clear_lines}\n"
            # ── write new bindings ────────────────────────────────────────
            f"{bind_lines}\n"
            # ── armor settings ────────────────────────────────────────────
            f"scene['mhws_natives_root'] = {natives_root!r}\n"
            f"s.mhws_armor_scheme   = {armor_scheme!r}\n"
            f"s.mhws_selected_armor = {armor_id!r}\n"
            f"s.mhws_armor_variant  = {armor_variant!r}\n"
            # ── bonesystem settings ───────────────────────────────────────
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            f"s.mhws_fbxskel_name  = {fbxskel_name!r}\n"
            f"s.mhws_bs_armature   = arm_obj\n"
            f"s.mhws_use_bonesystem = True\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"print('CONFIGURED')\n"
        )
        lines = client.execute_and_extract(code)
        if not lines or lines[0] != "CONFIGURED":
            return PhaseError(
                category="unexpected",
                operator="",
                message=f"Scene configuration failed. Output: {lines!r}",
            )
        return None

    def _run_export(self, client: BlenderClient) -> PhaseError | None:
        code = (
            f"import bpy\n"
            f"try:\n"
            f"    ret = bpy.ops.{_OP_EXPORT}()\n"
            f"except Exception as exc:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('EXCEPTION:' + str(exc))\n"
            f"else:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(str(ret))\n"
        )
        lines = client.execute_and_extract(code)
        if not lines:
            return PhaseError(
                category="operator_failed",
                operator=_OP_EXPORT,
                message="Batch export returned no output.",
            )
        if lines[0].startswith("EXCEPTION:"):
            exc_msg = lines[0][len("EXCEPTION:"):]
            return PhaseError(
                category="unexpected",
                operator=_OP_EXPORT,
                message=f"Batch export raised an exception: {exc_msg}",
                suggestion=(
                    "Check that RE Mesh Editor, RE Chain Editor, and MDF Tools "
                    "are all installed and enabled in Blender."
                ),
            )
        return require_finished(lines, _OP_EXPORT)
