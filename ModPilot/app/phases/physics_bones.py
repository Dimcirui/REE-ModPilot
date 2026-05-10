"""
Phase 3.5 — Physics Bone Transplant
Phase 4A  — Physics Bone Classification (scene inspector; data for NEGOTIATING loop)
Phase 4B  — Physics Chain File Creation  (applies calibrated params from physics_presets.json)

Data flow:
  Phase 3.5  modder.smart_graft        → physics bones transplanted to target armature
  Phase 4A   bone inspection            → chain topology dict for LLM classification
  Phase 4B   mhws.auto_create_chains   → RE Chain objects created in Blender
             + physics_presets.json    → parameters written to chain settings objects
             (no preset files required; parameters are set directly)

Phase 4B writes params via RE Chain Editor's PropertyGroup:
  obj.re_chain_chainsettings.{field} = value
Chain settings objects are identified by obj["TYPE"] == "RE_CHAIN_CHAINSETTINGS"
and are named CHAIN_SETTINGS_00, CHAIN_SETTINGS_01, etc.
Enum fields (windDelayType, springCalcType, chainType, muzzleDirection,
motionForceCalcType) require str() conversion before assignment. Fields that
cannot be set are silently skipped and logged in state_diff["skipped_params"].
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

# ── physics presets loader ────────────────────────────────────────────────────

_PRESETS_PATH = Path(__file__).resolve().parent.parent / "data" / "physics_presets.json"
_PRESETS_CACHE: dict | None = None


def _load_presets() -> dict:
    global _PRESETS_CACHE
    if _PRESETS_CACHE is None:
        _PRESETS_CACHE = json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
    return _PRESETS_CACHE


def get_physics_params(inferred_type: str) -> dict | None:
    """
    Return the 'params' dict for the given inferred_type, or None if not found.
    Types prefixed with '_' are reference-only and are also returned if requested.
    """
    presets = _load_presets()
    entry = presets.get("types", {}).get(inferred_type)
    if entry is None:
        return None
    return dict(entry.get("params", {}))


def list_inferred_types() -> list[str]:
    """Return all valid inferred_type keys (excluding reference-only '_' prefixed types)."""
    presets = _load_presets()
    return [k for k in presets.get("types", {}) if not k.startswith("_")]


# ── operator constants ────────────────────────────────────────────────────────

_OP_SMART_GRAFT = "modder.smart_graft"
_OP_REFRESH_COLORS = "modder.refresh_physics_bone_colors"
_OP_AUTO_CHAINS = "mhws.auto_create_chains"

# colliderFilterInfoPath used for all MHWilds chain settings objects
_DEFAULT_COLLIDER_PATH = (
    "System/Collision/Filter/Character/Character_Chain.cfil"
)


# ── Phase 3.5 ─────────────────────────────────────────────────────────────────


class PhysicsTransplant(PhaseTool):
    """
    Phase 3.5: Transplant physics bones from source armature to MHWilds target.

    Wraps modder.smart_graft which:
      - Copies all non-preset bones from source to target at world-space positions.
      - Auto-generates _End bones, verticalizes them (Z+).
      - Rebuilds parent hierarchy using standard key bridging.
      - Copies chain_role custom properties from source to target.
    """

    @property
    def name(self) -> str:
        return "physics_transplant"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "physics_transplant",
            "description": (
                "Phase 3.5: Transplant physics bones from the source armature to the "
                "MHWilds target armature. Preserves chain_role properties. Run before "
                "physics bone classification (Phase 4A)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_armature": {
                        "type": "string",
                        "description": "Source ARMATURE object name (contains physics bones).",
                    },
                    "target_armature": {
                        "type": "string",
                        "description": "MHWilds target ARMATURE object name.",
                    },
                    "x_preset": {
                        "type": "string",
                        "enum": ["MMD", "VRChat", "终末地"],
                        "description": "Source model preset (X) for standard key bridging.",
                    },
                    "y_preset": {
                        "type": "string",
                        "enum": ["怪猎荒野"],
                        "description": "Target game preset (Y). Always 怪猎荒野 for MHWs.",
                    },
                },
                "required": ["source_armature", "target_armature", "x_preset"],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        source_arm = params.get("source_armature", "")
        target_arm = params.get("target_armature", "")
        x_preset = params.get("x_preset", "")
        y_preset = params.get("y_preset", DEFAULT_Y_PRESET)

        if not source_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'source_armature' is required.",
                    suggestion="Provide the source model's armature object name.",
                )
            )
        if not target_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'target_armature' is required.",
                    suggestion="Provide the MHWilds reference armature object name.",
                )
            )
        if x_preset not in X_PRESETS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Unknown X preset {x_preset!r}. Valid: {sorted(X_PRESETS)}",
                )
            )

        state_before = cache.refresh()

        try:
            err = self._run_smart_graft(client, source_arm, target_arm, x_preset, y_preset)
            if err is not None:
                return PhaseResult.fail(err)
        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=_OP_SMART_GRAFT,
                    message="Blender error during physics bone transplant.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator=_OP_SMART_GRAFT,
                    message="Lost connection to Blender during physics transplant.",
                    raw=str(exc),
                )
            )

        state_after = cache.refresh()
        return PhaseResult.ok(state_before.diff(state_after))

    def _run_smart_graft(
        self,
        client: BlenderClient,
        source_arm: str,
        target_arm: str,
        x_preset: str,
        y_preset: str,
    ) -> PhaseError | None:
        code = (
            f"import bpy\n"
            f"src = bpy.data.objects.get({source_arm!r})\n"
            f"tgt = bpy.data.objects.get({target_arm!r})\n"
            f"missing = []\n"
            f"if src is None: missing.append({source_arm!r})\n"
            f"if tgt is None: missing.append({target_arm!r})\n"
            f"if missing:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:not_found:' + ','.join(missing))\n"
            f"else:\n"
            f"    settings = bpy.context.scene.mhw_suite_settings\n"
            f"    settings.import_preset_enum = {x_preset!r}\n"
            f"    settings.target_preset_enum = {y_preset!r}\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    src.select_set(True)\n"
            f"    tgt.select_set(True)\n"
            f"    bpy.context.view_layer.objects.active = tgt\n"
            f"    ret = bpy.ops.{_OP_SMART_GRAFT}()\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(ret)\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("PRECONDITION:"):
            detail = lines[0][len("PRECONDITION:"):]
            return PhaseError(
                category="precondition",
                operator=_OP_SMART_GRAFT,
                message=f"Armature not found: {detail}",
                suggestion="Ensure both source and MHWilds armatures are in the scene.",
            )
        return require_finished(lines, _OP_SMART_GRAFT)


# ── Phase 4A ─────────────────────────────────────────────────────────────────


class PhysicsClassification(PhaseTool):
    """
    Phase 4A: Inspect physics bones on the target armature; refresh chain_role colors.

    This phase does NOT classify — it gathers the data the NEGOTIATING loop needs
    to ask the LLM to classify each chain head. Returns:
      {
        "chain_heads": [
          {"name": "hair_001", "role": "head", "depth": 5, "children": 0},
          ...
        ],
        "branch_heads": [...]
      }
    The agent loop injects this into the NEGOTIATING conversation for LLM classification.
    """

    @property
    def name(self) -> str:
        return "physics_classification"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "physics_classification",
            "description": (
                "Phase 4A: Refresh chain_role colors on the target armature and return "
                "physics bone chain topology data for the agent to classify."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_armature": {
                        "type": "string",
                        "description": "MHWilds ARMATURE object name (post-transplant).",
                    },
                    "x_preset": {
                        "type": "string",
                        "enum": ["MMD", "VRChat", "终末地"],
                        "description": "X preset for physics bone detection.",
                    },
                },
                "required": ["target_armature", "x_preset"],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        target_arm = params.get("target_armature", "")
        x_preset = params.get("x_preset", "")

        if not target_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'target_armature' is required.",
                )
            )
        if x_preset not in X_PRESETS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Unknown X preset {x_preset!r}.",
                )
            )

        state_before = cache.refresh()

        try:
            err, chain_data = self._inspect_and_refresh(client, target_arm, x_preset)
            if err is not None:
                return PhaseResult.fail(err)
        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=_OP_REFRESH_COLORS,
                    message="Blender error during physics bone inspection.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator=_OP_REFRESH_COLORS,
                    message="Lost connection to Blender during physics inspection.",
                    raw=str(exc),
                )
            )

        state_after = cache.refresh()
        diff = state_before.diff(state_after)
        diff["chain_topology"] = chain_data
        return PhaseResult.ok(diff)

    def _inspect_and_refresh(
        self,
        client: BlenderClient,
        target_arm: str,
        x_preset: str,
    ) -> tuple[PhaseError | None, dict]:
        code = (
            f"import bpy, json\n"
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            f"if arm_obj is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:not_found:{target_arm}')\n"
            f"else:\n"
            f"    settings = bpy.context.scene.mhw_suite_settings\n"
            f"    settings.import_preset_enum = {x_preset!r}\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    arm_obj.select_set(True)\n"
            f"    bpy.context.view_layer.objects.active = arm_obj\n"
            f"    bpy.ops.object.mode_set(mode='POSE')\n"
            f"    bpy.ops.{_OP_REFRESH_COLORS}()\n"
            # Collect chain topology: bones with chain_role = 'head' or 'branch_head'
            f"    chains = []\n"
            f"    arm = arm_obj.data\n"
            f"    for bone in arm.bones:\n"
            f"        role = arm_obj.pose.bones[bone.name].get('chain_role', '')\n"
            f"        if role in ('head', 'branch_head'):\n"
            # Walk chain to measure depth
            f"            depth = 0\n"
            f"            cur = bone\n"
            f"            while cur is not None:\n"
            f"                depth += 1\n"
            f"                children_roles = [\n"
            f"                    arm_obj.pose.bones[c.name].get('chain_role', '')\n"
            f"                    for c in cur.children\n"
            f"                ]\n"
            f"                cont = [c for c, r in zip(cur.children, children_roles)\n"
            f"                        if r not in ('head', 'branch_head') and r != '']\n"
            f"                cur = cont[0] if cont else None\n"
            f"            parent_name = bone.parent.name if bone.parent else ''\n"
            f"            chains.append({{'name': bone.name, 'role': role,\n"
            f"                           'depth': depth, 'parent': parent_name}})\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('CHAINS:' + json.dumps(chains))\n"
        )
        lines = client.execute_and_extract(code)
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator=_OP_REFRESH_COLORS,
                    message="Physics inspection returned no output.",
                ),
                {},
            )
        if lines[0].startswith("PRECONDITION:"):
            return (
                PhaseError(
                    category="precondition",
                    operator=_OP_REFRESH_COLORS,
                    message=f"Armature {target_arm!r} not found in scene.",
                    suggestion="Run physics_transplant first.",
                ),
                {},
            )
        if lines[0].startswith("CHAINS:"):
            try:
                chain_data = json.loads(lines[0][len("CHAINS:"):])
                return None, {"chain_heads": chain_data}
            except json.JSONDecodeError:
                return (
                    PhaseError(
                        category="unexpected",
                        operator=_OP_REFRESH_COLORS,
                        message="Could not parse chain topology JSON from Blender.",
                    ),
                    {},
                )
        return (
            PhaseError(
                category="unexpected",
                operator=_OP_REFRESH_COLORS,
                message=f"Unexpected output from physics inspection: {lines[0]!r}",
            ),
            {},
        )


# ── Phase 4B ─────────────────────────────────────────────────────────────────


class PhysicsChains(PhaseTool):
    """
    Phase 4B: Create RE Chain structure and write calibrated physics parameters.

    Uses physics_presets.json to set chain settings parameters directly on newly
    created RE_CHAIN_CHAINSETTINGS objects — no preset files required on disk.

    Pipeline:
      Step 1 — Validate inferred_types and resolve each to a params dict.
      Step 2 — Verify armature + chain collection exist in Blender.
      Step 3 — Snapshot existing CHAINSETTINGS objects before creation.
      Step 4 — Call mhws.auto_create_chains (SEPARATE or SHARED mode).
      Step 5 — Identify newly created CHAINSETTINGS objects.
      Step 6 — Apply physics params from JSON to each new object.
    """

    @property
    def name(self) -> str:
        return "physics_chains"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "physics_chains",
            "description": (
                "Phase 4B: Create RE Chain structures for all classified physics bone "
                "chains and write calibrated physics parameters from the built-in "
                "physics_presets.json knowledge base. Run after physics_classification."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_armature": {
                        "type": "string",
                        "description": "MHWilds ARMATURE object name (with transplanted physics bones).",
                    },
                    "chain_collection": {
                        "type": "string",
                        "description": "Blender Collection name containing the RE_CHAIN_HEADER object.",
                    },
                    "inferred_types": {
                        "type": "object",
                        "description": (
                            "Map of chain_head_bone_name → inferred_type. "
                            f"Valid types: {list_inferred_types()}"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "x_preset": {
                        "type": "string",
                        "enum": ["MMD", "VRChat", "终末地"],
                        "description": "X preset for physics bone detection.",
                    },
                    "settings_mode": {
                        "type": "string",
                        "enum": ["SEPARATE", "SHARED"],
                        "description": (
                            "SEPARATE: one chain settings object per chain (required when "
                            "chains have different inferred_types). SHARED: all chains share "
                            "one settings object."
                        ),
                    },
                },
                "required": [
                    "target_armature",
                    "chain_collection",
                    "inferred_types",
                    "x_preset",
                ],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        target_arm = params.get("target_armature", "")
        chain_col = params.get("chain_collection", "")
        inferred_types: dict[str, str] = params.get("inferred_types", {})
        x_preset = params.get("x_preset", "")
        settings_mode = params.get("settings_mode", "SEPARATE")

        # ── param validation ───────────────────────────────────────────────
        if not target_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'target_armature' is required.",
                )
            )
        if not chain_col:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'chain_collection' is required.",
                    suggestion="Create a collection with a RE_CHAIN_HEADER object first.",
                )
            )
        if not inferred_types:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'inferred_types' must be a non-empty dict of bone → type pairs.",
                    suggestion="Run physics_classification first to get chain heads.",
                )
            )
        if x_preset not in X_PRESETS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Unknown X preset {x_preset!r}.",
                )
            )
        if settings_mode not in ("SEPARATE", "SHARED"):
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"settings_mode must be 'SEPARATE' or 'SHARED', got {settings_mode!r}.",
                )
            )

        # Resolve inferred_types → params dicts (fail fast on unknown types)
        resolved: dict[str, dict] = {}
        unknown_types: list[str] = []
        for bone_name, itype in inferred_types.items():
            p = get_physics_params(itype)
            if p is None:
                unknown_types.append(f"{bone_name!r}={itype!r}")
            else:
                resolved[bone_name] = p
        if unknown_types:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Unknown inferred_type(s): {', '.join(unknown_types)}",
                    suggestion=f"Valid types are: {list_inferred_types()}",
                )
            )

        state_before = cache.refresh()

        try:
            # Step 1 — validate scene objects
            err = self._validate_scene(client, target_arm, chain_col)
            if err is not None:
                return PhaseResult.fail(err)

            # Step 2 — snapshot, create chains, find new objects
            err, new_cs_names = self._create_chains(
                client, target_arm, chain_col, x_preset, settings_mode
            )
            if err is not None:
                return PhaseResult.fail(err)

            # Step 3 — apply physics params to newly created chain settings
            skipped = self._apply_params_to_chain_settings(
                client, new_cs_names, inferred_types, resolved, settings_mode
            )

        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=_OP_AUTO_CHAINS,
                    message="Blender error during physics chain creation.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator=_OP_AUTO_CHAINS,
                    message="Lost connection to Blender during physics chain creation.",
                    raw=str(exc),
                )
            )

        state_after = cache.refresh()
        diff = state_before.diff(state_after)
        diff["chain_settings_created"] = new_cs_names
        if skipped:
            diff["skipped_params"] = skipped
        return PhaseResult.ok(diff)

    # ── private helpers ────────────────────────────────────────────────────

    def _validate_scene(
        self,
        client: BlenderClient,
        target_arm: str,
        chain_col: str,
    ) -> PhaseError | None:
        code = (
            f"import bpy\n"
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            f"col = bpy.data.collections.get({chain_col!r})\n"
            f"missing = []\n"
            f"if arm_obj is None: missing.append('armature:' + {target_arm!r})\n"
            f"if col is None: missing.append('collection:' + {chain_col!r})\n"
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
                message=f"Required objects not found: {detail}",
                suggestion=(
                    "Ensure the MHWilds armature is in the scene and the chain "
                    "collection with RE_CHAIN_HEADER has been created."
                ),
            )
        return None

    def _create_chains(
        self,
        client: BlenderClient,
        target_arm: str,
        chain_col: str,
        x_preset: str,
        settings_mode: str,
    ) -> tuple[PhaseError | None, list[str]]:
        """
        Snapshot CHAINSETTINGS objects, call mhws.auto_create_chains,
        return names of newly created CHAINSETTINGS objects.
        """
        code = (
            f"import bpy\n"
            # RE Chain Editor marks chain settings objects with TYPE custom property
            # and names them CHAIN_SETTINGS_00, CHAIN_SETTINGS_01, etc.
            f"def _is_cs(obj):\n"
            f"    return obj.get('TYPE') == 'RE_CHAIN_CHAINSETTINGS'\n"
            f"existing_cs = set(obj.name for obj in bpy.data.objects if _is_cs(obj))\n"
            # Set up scene
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            f"settings = bpy.context.scene.mhw_suite_settings\n"
            f"settings.import_preset_enum = {x_preset!r}\n"
            f"bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"bpy.ops.object.select_all(action='DESELECT')\n"
            f"arm_obj.select_set(True)\n"
            f"bpy.context.view_layer.objects.active = arm_obj\n"
            f"bpy.ops.object.mode_set(mode='POSE')\n"
            # Call operator
            f"ret = bpy.ops.{_OP_AUTO_CHAINS}(\n"
            f"    chain_collection={chain_col!r},\n"
            f"    settings_mode={settings_mode!r},\n"
            f")\n"
            # Find new CHAINSETTINGS objects
            f"new_cs = [obj.name for obj in bpy.data.objects\n"
            f"          if _is_cs(obj) and obj.name not in existing_cs]\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"if 'FINISHED' not in str(ret):\n"
            f"    print('CANCELLED:' + str(ret))\n"
            f"else:\n"
            f"    import json\n"
            f"    print('NEW_CS:' + json.dumps(new_cs))\n"
        )
        lines = client.execute_and_extract(code)
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator=_OP_AUTO_CHAINS,
                    message="auto_create_chains returned no output.",
                ),
                [],
            )
        if lines[0].startswith("CANCELLED:"):
            return (
                PhaseError(
                    category="operator_failed",
                    operator=_OP_AUTO_CHAINS,
                    message=f"auto_create_chains did not finish: {lines[0]}",
                    suggestion=(
                        "Check that the chain collection has a RE_CHAIN_HEADER object, "
                        "physics bones have chain_role set, and RE Chain Editor is installed."
                    ),
                ),
                [],
            )
        if lines[0].startswith("NEW_CS:"):
            try:
                new_cs_names: list[str] = json.loads(lines[0][len("NEW_CS:"):])
                return None, new_cs_names
            except json.JSONDecodeError:
                return (
                    PhaseError(
                        category="unexpected",
                        operator=_OP_AUTO_CHAINS,
                        message="Could not parse new chain settings names from Blender.",
                    ),
                    [],
                )
        return (
            PhaseError(
                category="unexpected",
                operator=_OP_AUTO_CHAINS,
                message=f"Unexpected output: {lines[0]!r}",
            ),
            [],
        )

    def _apply_params_to_chain_settings(
        self,
        client: BlenderClient,
        cs_names: list[str],
        inferred_types: dict[str, str],
        resolved_params: dict[str, dict],
        settings_mode: str,
    ) -> list[str]:
        """
        Apply physics parameters from physics_presets.json to each chain settings object.

        For SEPARATE mode: assigns each cs_name a params dict based on the matching
        inferred_type. Chain heads are ordered alphabetically (consistent with operator
        iteration order). If lengths mismatch, the last resolved params dict is reused.

        For SHARED mode: applies the params for the first (or only) inferred_type to
        the single shared chain settings object.

        Returns a list of skipped param keys (those that could not be set).

        NOTE: Parameter assignment uses Blender's custom property interface (obj[key]).
        RE Chain Editor stores chain settings fields as custom RNA properties accessible
        this way. If a field cannot be set (read-only / wrong type), it is silently
        skipped and logged in the return list.
        """
        if not cs_names:
            return []

        # Build ordered list of params — one per cs_name
        ordered_bone_names = sorted(inferred_types.keys())
        params_list: list[dict] = []

        if settings_mode == "SHARED":
            # Apply the first inferred_type's params to the single shared object
            first_bone = ordered_bone_names[0] if ordered_bone_names else ""
            shared_params = resolved_params.get(first_bone, {})
            params_list = [shared_params] * len(cs_names)
        else:
            # SEPARATE: match cs_names[i] to ordered_bone_names[i]
            for i in range(len(cs_names)):
                bone_name = ordered_bone_names[i] if i < len(ordered_bone_names) else ordered_bone_names[-1]
                params_list.append(resolved_params.get(bone_name, {}))

        # Add default collider path to each params dict if not already present
        collider_default = (
            _load_presets()
            .get("_usage_guide", {})
            .get("colliderFilterInfoPath_default", _DEFAULT_COLLIDER_PATH)
        )
        for p in params_list:
            p.setdefault("colliderFilterInfoPath", collider_default)

        # Serialize params_list for injection into Blender code
        params_json = json.dumps(params_list, ensure_ascii=False)
        cs_names_json = json.dumps(cs_names, ensure_ascii=False)

        # Enum fields stored as strings in RE Chain Editor PropertyGroup
        enum_fields_repr = repr({
            "windDelayType", "springCalcType", "chainType",
            "muzzleDirection", "motionForceCalcType",
        })
        code = (
            f"import bpy, json\n"
            f"cs_names = json.loads({cs_names_json!r})\n"
            f"params_list = json.loads({params_json!r})\n"
            f"ENUM_FIELDS = {enum_fields_repr}\n"
            f"skipped = []\n"
            f"for i, cs_name in enumerate(cs_names):\n"
            f"    cs_obj = bpy.data.objects.get(cs_name)\n"
            f"    if cs_obj is None:\n"
            f"        skipped.append(cs_name + '.(not found)')\n"
            f"        continue\n"
            f"    pg = cs_obj.re_chain_chainsettings\n"
            f"    p = params_list[i] if i < len(params_list) else params_list[-1]\n"
            f"    for key, val in p.items():\n"
            f"        if isinstance(val, list):\n"
            f"            val = tuple(val)\n"
            f"        if key in ENUM_FIELDS:\n"
            f"            val = str(int(val))\n"
            f"        try:\n"
            f"            setattr(pg, key, val)\n"
            f"        except (AttributeError, TypeError):\n"
            f"            skipped.append(cs_name + '.' + key)\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"print('APPLIED:' + json.dumps(skipped))\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("APPLIED:"):
            try:
                return json.loads(lines[0][len("APPLIED:"):])
            except json.JSONDecodeError:
                pass
        return []
