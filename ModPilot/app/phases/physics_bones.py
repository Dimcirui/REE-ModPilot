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

mhws.auto_create_chains calling convention (post-toolkit patch):
  Set toolpanel.chainCollection via PointerProperty BEFORE calling the operator.
  Do NOT pass chain_collection= as a kwarg — the dynamic enum is unreliable in
  scripted context; the operator now uses the PointerProperty as fallback.
"""

from __future__ import annotations

import json
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
from app.resources import app_data_dir

# ── physics presets loader ────────────────────────────────────────────────────

_PRESETS_PATH = app_data_dir() / "physics_presets.json"
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

    @property
    def phase_slot(self) -> str | None:
        return "phase_35"

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
            f"    settings.import_preset_enum = {(x_preset + '.json')!r}\n"
            f"    settings.target_preset_enum = {(y_preset + '.json')!r}\n"
            f"    bpy.context.view_layer.objects.active = tgt\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    src.select_set(True)\n"
            f"    tgt.select_set(True)\n"
            f"    ret = bpy.ops.{_OP_SMART_GRAFT}()\n"
            f"    if 'FINISHED' in str(ret):\n"
            # Step 4: hide source armature — no longer needed after transplant
            f"        src.hide_viewport = True\n"
            # Step 5: switch X preset to MHWilds for all downstream ops
            f"        settings.import_preset_enum = {(y_preset + '.json')!r}\n"
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

    @property
    def phase_slot(self) -> str | None:
        return "phase_4a"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "physics_classification",
            "description": (
                "Phase 4A: Refresh chain_role colors on the MHWilds target armature "
                "(using MHWilds preset, which was set at the end of Phase 3.5) and "
                "return physics bone chain topology data for the agent to classify."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_armature": {
                        "type": "string",
                        "description": "MHWilds ARMATURE object name (post-transplant).",
                    },
                },
                "required": ["target_armature"],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        target_arm = params.get("target_armature", "")

        if not target_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'target_armature' is required.",
                )
            )

        state_before = cache.refresh()

        try:
            err, chain_data = self._inspect_and_refresh(client, target_arm)
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
    ) -> tuple[PhaseError | None, dict]:
        # After Phase 3.5, X preset is switched to MHWilds (怪猎荒野).
        # refresh_physics_bone_colors identifies physics bones as those NOT in the
        # MHWilds preset list — exactly what we want post-transplant.
        # Each step is wrapped in try/except so Blender errors surface as readable
        # STEP_ERR messages instead of opaque {"status": "error"} responses.
        code = (
            f"import bpy, json, traceback\n"
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            f"if arm_obj is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:not_found:{target_arm}')\n"
            f"else:\n"
            f"    settings = bpy.context.scene.mhw_suite_settings\n"
            f"    settings.import_preset_enum = '怪猎荒野.json'\n"
            f"    bpy.context.view_layer.objects.active = arm_obj\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    arm_obj.select_set(True)\n"
            # refresh_physics_bone_colors is a visualization step; if it fails,
            # log the error but continue to collect topology data.
            f"    refresh_err = ''\n"
            f"    try:\n"
            f"        bpy.ops.object.mode_set(mode='POSE')\n"
            f"        bpy.ops.{_OP_REFRESH_COLORS}()\n"
            f"    except Exception as _e:\n"
            f"        refresh_err = traceback.format_exc(limit=3)\n"
            f"        try: bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"        except: pass\n"
            # Collect chain topology: bones with chain_role = 'head' or 'branch_head'
            f"    chains = []\n"
            f"    chain_err = ''\n"
            f"    try:\n"
            f"        arm = arm_obj.data\n"
            f"        for bone in arm.bones:\n"
            f"            pb = arm_obj.pose.bones.get(bone.name)\n"
            f"            if pb is None: continue\n"
            f"            role = pb.get('chain_role', '')\n"
            f"            if role not in ('head', 'branch_head'): continue\n"
            f"            depth = 0\n"
            f"            cur = bone\n"
            f"            while cur is not None:\n"
            f"                depth += 1\n"
            f"                cont = [\n"
            f"                    c for c in cur.children\n"
            f"                    if arm_obj.pose.bones.get(c.name) is not None and\n"
            f"                       arm_obj.pose.bones[c.name].get('chain_role', '')\n"
            f"                       not in ('head', 'branch_head')\n"
            f"                ]\n"
            f"                cur = cont[0] if cont else None\n"
            f"            parent_name = bone.parent.name if bone.parent else ''\n"
            f"            chains.append({{'name': bone.name, 'role': role,\n"
            f"                           'depth': depth, 'parent': parent_name}})\n"
            f"    except Exception as _e:\n"
            f"        chain_err = traceback.format_exc(limit=3)\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    if chain_err:\n"
            f"        print('CHAIN_ERR:' + chain_err.replace('\\n', '|'))\n"
            f"    elif refresh_err:\n"
            f"        print('REFRESH_ERR:' + refresh_err.replace('\\n', '|'))\n"
            f"    else:\n"
            f"        print('CHAINS:' + json.dumps(chains))\n"
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
        if lines[0].startswith("CHAIN_ERR:"):
            detail = lines[0][len("CHAIN_ERR:"):].replace("|", "\n")
            return (
                PhaseError(
                    category="unexpected",
                    operator="chain topology walk",
                    message="Failed to walk physics bone chain topology.",
                    raw=detail,
                ),
                {},
            )
        if lines[0].startswith("REFRESH_ERR:"):
            # Color refresh failed but topology is still available: degrade gracefully.
            # Return empty chains — the error surfaces as a warning in state_diff.
            detail = lines[0][len("REFRESH_ERR:"):].replace("|", "\n")
            return (
                PhaseError(
                    category="operator_failed",
                    operator=_OP_REFRESH_COLORS,
                    message="refresh_physics_bone_colors raised an exception.",
                    raw=detail,
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
      Step 2 — Verify armature exists in Blender.
      Step 3 — Merge '合并到父级' bones into parents (if any).
      Step 4 — Discover / create RE Chain collection; set toolpanel.chainCollection
               (PointerProperty); call mhws.auto_create_chains; identify new objects.
      Step 5 — Apply physics params from JSON to each new chain settings object.

    Phase advancement:
      prepare_only=True  → advances_phase returns False (cleanup check only, not done yet).
      prepare_only=False → advances_phase returns True  (full creation completes Phase 4B).
    """

    def __init__(self) -> None:
        # Tracks whether the LAST run() call used prepare_only so advances_phase
        # can return the correct value.  Safe because AgentLoop executes tool calls
        # sequentially (each await completes before the next tool call starts).
        self._last_was_prepare_only: bool = False

    @property
    def name(self) -> str:
        return "physics_chains"

    @property
    def advances_phase(self) -> bool:
        """
        False for prepare_only calls (cleanup check — Phase 4B is not done yet).
        True for full chain creation calls.
        """
        return not self._last_was_prepare_only

    @property
    def phase_slot(self) -> str | None:
        return "phase_4b"

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
                        "description": (
                            "Optional hint: Blender Collection name for the RE Chain collection. "
                            "Must have '.chain' or '.clsp' in its name and be a valid "
                            "RE_CHAIN_COLLECTION (created via RE Chain Editor). "
                            "If omitted, the phase auto-discovers the first matching collection "
                            "in the scene."
                        ),
                    },
                    "bones_to_clear": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Bone names whose chain_role marks should be cleared. "
                            "Use for native game bones accidentally marked by "
                            "refresh_physics_bone_colors (e.g. Cage, Cage_L). "
                            "The bones remain in the armature — only chain_role is removed. "
                            "Runs AFTER bones_to_merge (merge auto-refreshes colors, which "
                            "would re-mark cleared bones if clear ran first)."
                        ),
                    },
                    "bones_to_merge": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Bone names classified as '合并到父级'. "
                            "Each bone's vertex weights are merged into its direct parent "
                            "via modder.merge_into_parent; the bone is then deleted and its "
                            "children reconnect to the grandparent. "
                            "Runs BEFORE bones_to_clear and chain creation."
                        ),
                    },
                    "inferred_types": {
                        "type": "object",
                        "description": (
                            "Map of chain_head_bone_name → inferred_type for bones "
                            "classified as '启用物理'. "
                            f"Valid types: {list_inferred_types()}"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "settings_mode": {
                        "type": "string",
                        "enum": ["SEPARATE", "SHARED"],
                        "description": (
                            "SEPARATE (default): one chain settings object per chain "
                            "(correct for scenes with different inferred_types). "
                            "SHARED: all chains share one settings object (experimental)."
                        ),
                    },
                    "prepare_only": {
                        "type": "boolean",
                        "description": (
                            "If true, clear all chain_role marks and bone colors on the "
                            "armature, then re-detect physics bones via "
                            "refresh_physics_bone_colors. Returns immediately — call "
                            "physics_chains again (without prepare_only) after the user "
                            "has confirmed bone colors look correct in Blender."
                        ),
                    },
                },
                "required": [
                    "target_armature",
                ],
                "description": (
                    "When prepare_only=true: only target_armature is needed. "
                    "When prepare_only is omitted/false: inferred_types is also required."
                ),
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
        bones_to_clear: list[str] = params.get("bones_to_clear") or []
        bones_to_merge: list[str] = params.get("bones_to_merge") or []
        inferred_types: dict[str, str] = params.get("inferred_types", {})
        settings_mode = params.get("settings_mode", "SEPARATE")
        prepare_only: bool = bool(params.get("prepare_only", False))

        # Record whether this call is prepare_only so advances_phase can return
        # the correct value.  Must be set before any early return so that a failed
        # prepare_only call also does not accidentally advance the phase.
        self._last_was_prepare_only = prepare_only

        # ── param validation ───────────────────────────────────────────────
        if not target_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'target_armature' is required.",
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

        # ── prepare_only branch: cleanup only ────────────────────────────────
        # NOTE: strict mark verification ("every chain_role head must have an
        # _End descendant") used to run here, but it false-positived on bones
        # like Eye_L/R, Ribbon Root, Hat Root — these legitimately lack _End
        # descendants because they are scheduled for the bones_to_merge step
        # in the chain-creation branch below. At prepare_only time the merge
        # has not happened yet, so the verifier saw a dirty intermediate state
        # and surfaced the noise to the user.
        #
        # Validation is now deferred to AFTER Step 2b (post-merge, post-clear)
        # in the chain-creation branch, where the state is the ground truth
        # and any remaining anomaly is a true anomaly worth flagging.
        if prepare_only:
            state_before = cache.refresh()
            try:
                err = self._validate_scene(client, target_arm)
                if err is not None:
                    return PhaseResult.fail(err)

                err = self._clear_and_refresh_chain_roles(client, target_arm)
                if err is not None:
                    return PhaseResult.fail(err)

            except BlenderError as exc:
                return PhaseResult.fail(
                    PhaseError(
                        category="unexpected",
                        operator=_OP_REFRESH_COLORS,
                        message="Blender error during chain_role cleanup.",
                        raw=str(exc),
                    )
                )
            except OSError as exc:
                return PhaseResult.fail(
                    PhaseError(
                        category="timeout",
                        operator=_OP_REFRESH_COLORS,
                        message="Lost connection to Blender during chain_role cleanup.",
                        raw=str(exc),
                    )
                )

            state_after = cache.refresh()
            diff = state_before.diff(state_after)
            diff["message"] = (
                "Chain role marks refreshed. Strict verification (no body bones "
                "marked, every chain head has _End descendants) is deferred to "
                "the chain-creation step where bones_to_merge will have been "
                "applied. Proceed by classifying chains in the widget; "
                "physics_chains will re-run validation against the post-merge "
                "state and surface any real anomalies in its result."
            )
            return PhaseResult.ok(diff)

        # ── chain creation branch ─────────────────────────────────────────
        # chain_col is optional; _create_chains auto-discovers if empty
        if not inferred_types:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'inferred_types' must be a non-empty dict of bone → type pairs.",
                    suggestion="Run physics_classification first to get chain heads.",
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
            # Step 1 — validate armature; chain collection discovered in step 3
            err = self._validate_scene(client, target_arm)
            if err is not None:
                return PhaseResult.fail(err)

            # Step 2a — merge '合并到父级' bones FIRST.
            # merge_into_parent auto-refreshes chain_role bone colors; running it
            # after clear would re-mark the just-cleared native bones as physics.
            if bones_to_merge:
                # Cascade: anything ending in `_End` that's a child of a bone
                # being merged is itself a Phase-3.5 placeholder leaf with no
                # physics meaning — the LLM frequently overlooks pairing them
                # (e.g. selects `Twist wrist_L` but not `Twist wrist_L_End`).
                # Expand the merge list deterministically so the leftover
                # `_End` leaves don't get spurious physics chains.
                original_merge_len = len(bones_to_merge)
                bones_to_merge = self._expand_end_children(
                    client, target_arm, bones_to_merge
                )
                added = bones_to_merge[original_merge_len:]
                # Surface the cascade outcome to the SSE log so the user can
                # verify the auto-extension actually ran and what it caught.
                # Visible only in debug mode (debug-bubble class).
                cascade_summary = (
                    f"_End cascade added {len(added)} bone(s): "
                    f"{added[:10]}{'...' if len(added) > 10 else ''}"
                )
                # Diagnostics: write to state_diff via an attribute the caller
                # can later attach.  For now just log via stdout so the dev
                # console shows it; future improvement: emit a debug SSE event.
                # noqa: this is a deliberate print — Blender pipeline runs in
                # background thread without our usual emit handle.
                print(f"[physics_chains] {cascade_summary}")
                err = self._merge_into_parents(client, target_arm, bones_to_merge)
                if err is not None:
                    return PhaseResult.fail(err)

            # Step 2b — clear chain_role on native game bones AFTER merge, so that
            # the merge's color-refresh cannot re-mark the excluded bones.
            if bones_to_clear:
                err = self._clear_specific_bone_roles(client, target_arm, bones_to_clear)
                if err is not None:
                    return PhaseResult.fail(err)

            # Step 2c — strict mark verification, deferred here from prepare_only.
            # At this point the merge has consumed the false-positive sources
            # (Eye_L/R, Ribbon Root, etc.) and bones_to_clear has knocked off
            # native game bones the user excluded. Anything still carrying
            # chain_role='head'/'branch_head' without an _End descendant is a
            # genuine anomaly. Non-fatal: surface in state_diff and continue,
            # because aborting here would discard the user's classification
            # widget answers — surfacing the warning lets them decide whether
            # to inspect manually after chain creation finishes.
            verify_err, marks_clean = self._verify_chain_marks(client, target_arm)
            mark_warning: str | None = None
            if verify_err is not None:
                mark_warning = (
                    "Chain mark verification failed to run "
                    f"({verify_err.message}). Chain creation will proceed; "
                    "manual inspection may be needed afterwards."
                )
            elif not marks_clean:
                mark_warning = (
                    "Post-merge verification detected chain_role marks on "
                    "bones that lack _End descendants. Chain creation will "
                    "still proceed; inspect bone colors in Blender after the "
                    "phase completes and remove any spurious marks manually."
                )

            # Step 3 — create chains; settings_mode controls CS count per chain
            err, new_cs_names, chain_col_name = self._create_chains(
                client, target_arm, chain_col, settings_mode
            )
            if err is not None:
                return PhaseResult.fail(err)

            # Step 4 — apply physics params to chain settings objects.
            # If new_cs_names is empty the operator returned no NEW objects — this
            # happens when a prior run timed out on the Python side but Blender
            # actually finished and the CS objects already exist.  Fall back to
            # scanning the chain collection for existing CS objects.
            if not new_cs_names and chain_col_name:
                new_cs_names = self._find_existing_cs_in_collection(
                    client, chain_col_name
                )
            if new_cs_names:
                if settings_mode == "SEPARATE":
                    # One CS per chain — apply params in alphabetical bone order
                    # (consistent with operator iteration order)
                    skipped = self._apply_params_to_chain_settings(
                        client, new_cs_names, inferred_types, resolved, "SEPARATE"
                    )
                    final_cs = new_cs_names
                else:
                    # SHARED: one CS was created; split into per-type CS via consolidation
                    _, cs_to_type = self._consolidate_chain_settings(
                        client, new_cs_names[0], chain_col_name, inferred_types
                    )
                    skipped = self._apply_params_by_type(
                        client, cs_to_type, inferred_types, resolved
                    )
                    final_cs = list(cs_to_type.keys())
            else:
                skipped = []
                final_cs = []

            # Step 5 — angle limit ramp on all new chain groups (default 60°, 4 iter)
            self._apply_angle_limit_ramp(client, chain_col_name)

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
            # Connection dropped mid-run (Blender crash, timeout, or socket reset).
            # Blender may have actually finished creating chains before the drop.
            # Try to reconnect and apply params to whatever was created.
            recovery = self._recover_after_drop(
                client, target_arm, inferred_types, resolved
            )
            if recovery is not None:
                state_after = cache.refresh()
                diff = state_before.diff(state_after)
                diff.update(recovery)
                return PhaseResult.ok(diff)
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
        diff["chain_settings_created"] = final_cs
        if skipped:
            diff["skipped_params"] = skipped
        if mark_warning is not None:
            diff["mark_warning"] = mark_warning
        return PhaseResult.ok(diff)

    # ── private helpers ────────────────────────────────────────────────────

    def _clear_and_refresh_chain_roles(
        self,
        client: BlenderClient,
        target_arm: str,
    ) -> "PhaseError | None":
        """
        Clear stale chain_role marks and bone colors, then re-detect physics bones.

        Sequence:
          1. Enter POSE mode; select all bones.
          2. Call modder.clear_chain_role() — removes the chain_role custom property
             from every selected bone. Does NOT remove bone colors.
          3. Reset bone color palette to DEFAULT for all pose bones (color removal).
          4. Call modder.refresh_physics_bone_colors() — re-detects physics bones
             using the active X preset and marks correct chain_roles + colors.

        This prevents stale chain_role marks from prior sessions causing spurious
        chain groups during auto_create_chains (e.g. body bones incorrectly getting
        chain_role='head' → one CHAINGROUP per body bone).
        """
        _OP_CLEAR_ROLE = "modder.clear_chain_role"
        code = (
            f"import bpy\n"
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            f"if arm_obj is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:not_found:{target_arm}')\n"
            f"else:\n"
            # Step 1: enter POSE mode and select all bones
            f"    bpy.context.view_layer.objects.active = arm_obj\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    arm_obj.select_set(True)\n"
            f"    bpy.ops.object.mode_set(mode='POSE')\n"
            f"    bpy.ops.pose.select_all(action='SELECT')\n"
            # Step 2: clear chain_role custom properties from all selected bones
            f"    clear_err = ''\n"
            f"    try:\n"
            f"        bpy.ops.{_OP_CLEAR_ROLE}()\n"
            f"    except Exception as _e:\n"
            f"        clear_err = str(_e)[:120]\n"
            # Step 3: clear bone colors (palette reset — clear_chain_role does NOT do this)
            f"    for pb in arm_obj.pose.bones:\n"
            f"        try: pb.color.palette = 'DEFAULT'\n"
            f"        except Exception: pass\n"
            # Step 4: re-detect physics bones and mark correct chain_roles + colors
            f"    refresh_err = ''\n"
            f"    try:\n"
            f"        bpy.ops.{_OP_REFRESH_COLORS}()\n"
            f"    except Exception as _e:\n"
            f"        refresh_err = str(_e)[:120]\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    if clear_err:\n"
            f"        print('CLEAR_ERR:' + clear_err)\n"
            f"    elif refresh_err:\n"
            f"        print('REFRESH_ERR:' + refresh_err)\n"
            f"    else:\n"
            f"        print('OK')\n"
        )
        lines = client.execute_and_extract(code)
        if not lines:
            return PhaseError(
                category="operator_failed",
                operator=_OP_CLEAR_ROLE,
                message="clear_chain_role returned no output from Blender.",
            )
        if lines[0].startswith("PRECONDITION:"):
            return PhaseError(
                category="precondition",
                operator=_OP_CLEAR_ROLE,
                message=f"Armature {target_arm!r} not found.",
                suggestion="Ensure physics_transplant has been run successfully.",
            )
        if lines[0].startswith("CLEAR_ERR:"):
            detail = lines[0][len("CLEAR_ERR:"):]
            return PhaseError(
                category="operator_failed",
                operator=_OP_CLEAR_ROLE,
                message="clear_chain_role raised an exception.",
                raw=detail,
            )
        if lines[0].startswith("REFRESH_ERR:"):
            detail = lines[0][len("REFRESH_ERR:"):]
            return PhaseError(
                category="operator_failed",
                operator=_OP_REFRESH_COLORS,
                message="refresh_physics_bone_colors raised an exception after clearing roles.",
                raw=detail,
            )
        return None  # 'OK'

    def _verify_chain_marks(
        self,
        client: BlenderClient,
        target_arm: str,
    ) -> tuple["PhaseError | None", bool]:
        """
        Verify chain_role marks are clean after cleanup.

        Logic: every bone with chain_role='head' or 'branch_head' must have at
        least one _End bone descendant. Phase 3.5 auto-generates _End bones at
        the tip of each transplanted physics chain. A body bone that was
        accidentally marked as a chain head has no _End descendants.

        Uses iterative BFS (no recursion) to avoid Python stack limits.

        Returns:
          (None, True)   — all marked heads have _End descendants; clean.
          (None, False)  — suspicious bones found (chain heads without _End).
          (error, False) — Blender call failed.
        """
        code = (
            f"import bpy, json\n"
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            f"if arm_obj is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:not_found')\n"
            f"else:\n"
            f"    def has_end_descendant(start_bone):\n"
            f"        stack = list(start_bone.children)\n"
            f"        while stack:\n"
            f"            b = stack.pop()\n"
            f"            if b.name.endswith('_End'): return True\n"
            f"            stack.extend(b.children)\n"
            f"        return False\n"
            f"    arm = arm_obj.data\n"
            f"    suspicious = []\n"
            f"    for bone in arm.bones:\n"
            f"        pb = arm_obj.pose.bones.get(bone.name)\n"
            f"        if pb is None: continue\n"
            f"        role = pb.get('chain_role', '')\n"
            f"        if role in ('head', 'branch_head'):\n"
            f"            if not has_end_descendant(bone):\n"
            f"                suspicious.append(bone.name)\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('VERIFY:' + json.dumps({{'clean': len(suspicious) == 0, 'suspicious': suspicious}}))\n"
        )
        lines = client.execute_and_extract(code)
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator=_OP_REFRESH_COLORS,
                    message="Chain mark verification returned no output from Blender.",
                ),
                False,
            )
        if lines[0].startswith("PRECONDITION:"):
            return (
                PhaseError(
                    category="precondition",
                    operator=_OP_REFRESH_COLORS,
                    message=f"Armature {target_arm!r} not found during mark verification.",
                ),
                False,
            )
        if lines[0].startswith("VERIFY:"):
            try:
                payload = json.loads(lines[0][len("VERIFY:"):])
                return None, bool(payload.get("clean", False))
            except json.JSONDecodeError:
                pass
        return (
            PhaseError(
                category="unexpected",
                operator=_OP_REFRESH_COLORS,
                message=f"Unexpected verification output: {lines[0]!r}",
            ),
            False,
        )

    def _validate_scene(
        self,
        client: BlenderClient,
        target_arm: str,
    ) -> PhaseError | None:
        """Validate only the armature; chain collection is discovered in _create_chains."""
        code = (
            f"import bpy\n"
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"if arm_obj is None:\n"
            f"    print('PRECONDITION:armature_not_found:{target_arm}')\n"
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
            return PhaseError(
                category="precondition",
                operator="",
                message=f"MHWilds armature {target_arm!r} not found in scene.",
                suggestion="Ensure physics_transplant has been run successfully.",
            )
        return None

    def _clear_specific_bone_roles(
        self,
        client: BlenderClient,
        target_arm: str,
        bone_names: list[str],
    ) -> "PhaseError | None":
        """
        Clear chain_role marks on specific named bones without affecting the rest.

        Use for native game bones that were accidentally picked up by
        refresh_physics_bone_colors and should NOT participate in physics
        (e.g. Cage, Cage_L). The bones remain in the armature unchanged —
        only their chain_role custom property is removed via modder.clear_chain_role.

        Runs BEFORE bones_to_merge and chain creation.
        """
        _OP_CLEAR_ROLE = "modder.clear_chain_role"
        names_json = json.dumps(bone_names, ensure_ascii=False)
        code = (
            f"import bpy\n"
            f"bone_names = {names_json}\n"
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            f"if arm_obj is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:armature_not_found')\n"
            f"else:\n"
            f"    bpy.context.view_layer.objects.active = arm_obj\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    arm_obj.select_set(True)\n"
            f"    bpy.ops.object.mode_set(mode='POSE')\n"
            f"    bpy.ops.pose.select_all(action='DESELECT')\n"
            f"    missing = []\n"
            f"    for name in bone_names:\n"
            f"        pb = arm_obj.pose.bones.get(name)\n"
            f"        if pb: pb.bone.select = True\n"
            f"        else: missing.append(name)\n"
            f"    ret = bpy.ops.{_OP_CLEAR_ROLE}()\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    if missing:\n"
            f"        print('WARN:not_found:' + ','.join(missing))\n"
            f"    print(str(ret))\n"
        )
        lines = client.execute_and_extract(code)
        if not lines:
            return PhaseError(
                category="operator_failed",
                operator=_OP_CLEAR_ROLE,
                message="clear_chain_role (specific bones) returned no output from Blender.",
            )
        if lines[0].startswith("PRECONDITION:"):
            return PhaseError(
                category="precondition",
                operator=_OP_CLEAR_ROLE,
                message=f"Armature {target_arm!r} not found — cannot clear bone marks.",
            )
        # WARN:not_found is non-fatal: some bones may have been renamed; log and continue
        result_line = lines[-1]
        return require_finished([result_line], _OP_CLEAR_ROLE)

    def _expand_end_children(
        self,
        client: BlenderClient,
        target_arm: str,
        bones_to_merge: list[str],
    ) -> list[str]:
        """Append any `*_End` direct children of bones in `bones_to_merge`.

        Phase 3.5 `smart_graft` writes a trailing `_End` placeholder under
        every transplanted physics bone to mark the chain tail.  When the user
        decides to merge an auxiliary bone (e.g. `Twist wrist_L`) into its
        parent, the `_End` leaf left behind has no physics meaning either —
        but the LLM commonly forgets to add it to `bones_to_merge`, so
        physics_chains then builds a degenerate single-bone chain on the leaf.

        NB: we do NOT filter by `chain_role` — by Phase 3.5 convention every
        `_End` is a placeholder leaf, regardless of whether its chain_role
        marker is currently set or got cleared earlier in the pipeline
        (prepare_only's cleanup can strip marks before we reach here).

        Returns a de-duplicated list preserving original order.  On any
        Blender error the input is returned unchanged (non-fatal).
        """
        if not bones_to_merge:
            return list(bones_to_merge)
        names_json = json.dumps(bones_to_merge, ensure_ascii=False)
        code = (
            f"import bpy, json\n"
            f"arm = bpy.data.objects.get({target_arm!r})\n"
            f"extras = []\n"
            f"if arm and arm.type == 'ARMATURE':\n"
            f"    pose_bones = arm.pose.bones\n"
            f"    for name in {names_json}:\n"
            f"        pb = pose_bones.get(name)\n"
            f"        if pb is None:\n"
            f"            continue\n"
            f"        for child in pb.children:\n"
            f"            if not child.name.endswith('_End'):\n"
            f"                continue\n"
            f"            extras.append(child.name)\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"print('EXTRAS:' + json.dumps(extras, ensure_ascii=False))\n"
        )
        try:
            lines = client.execute_and_extract(code)
        except Exception:
            return list(bones_to_merge)
        if not lines or not lines[0].startswith("EXTRAS:"):
            return list(bones_to_merge)
        try:
            extras: list[str] = json.loads(lines[0][len("EXTRAS:"):])
        except Exception:
            return list(bones_to_merge)
        # De-dup while preserving order: original first, then extras.
        seen: set[str] = set()
        out: list[str] = []
        for name in [*bones_to_merge, *extras]:
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
        return out

    def _merge_into_parents(
        self,
        client: BlenderClient,
        target_arm: str,
        bone_names: list[str],
    ) -> PhaseError | None:
        """
        Select the given bones and call modder.merge_into_parent in POSE mode.

        Each bone's vertex weights are merged into its direct parent; the bone is
        deleted and its children reconnect to the grandparent automatically.
        The operator also auto-refreshes chain_role bone colors.
        """
        names_json = json.dumps(bone_names, ensure_ascii=False)
        op = "modder.merge_into_parent"
        code = (
            f"import bpy\n"
            f"bone_names = {names_json}\n"
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            f"if arm_obj is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:armature_not_found')\n"
            f"else:\n"
            f"    bpy.context.view_layer.objects.active = arm_obj\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    arm_obj.select_set(True)\n"
            f"    bpy.ops.object.mode_set(mode='POSE')\n"
            f"    bpy.ops.pose.select_all(action='DESELECT')\n"
            f"    for name in bone_names:\n"
            f"        pb = arm_obj.pose.bones.get(name)\n"
            f"        if pb: pb.bone.select = True\n"
            f"    ret = bpy.ops.{op}()\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(str(ret))\n"
        )
        lines = client.execute_and_extract(code)
        if not lines:
            return PhaseError(
                category="operator_failed",
                operator=op,
                message="merge_into_parent returned no output from Blender.",
            )
        if lines[0].startswith("PRECONDITION:"):
            return PhaseError(
                category="precondition",
                operator=op,
                message=f"Armature {target_arm!r} not found — cannot merge bones.",
            )
        return require_finished(lines, op)

    def _create_chains(
        self,
        client: BlenderClient,
        target_arm: str,
        chain_col: str,
        settings_mode: str = "SEPARATE",
    ) -> tuple[PhaseError | None, list[str], str]:
        """
        Discover the RE Chain collection, snapshot existing CHAINSETTINGS objects,
        set re_chain_toolpanel.chainCollection (PointerProperty — NOT enum kwarg),
        call mhws.auto_create_chains with the given settings_mode, and return the
        list of newly created CHAINSETTINGS object names plus the collection name.

        settings_mode='SEPARATE' (default): creates one CS per chain head.
        settings_mode='SHARED': creates one CS shared by all chains (used by the
        SHARED consolidation path).

        Uses a 1200-second socket timeout because auto_create_chains can be slow
        for scenes with many chain heads (60+ chains ≈ 60-600 seconds on Windows).

        chain_collection discovery rules (in order):
          1. Use chain_col hint if provided and the collection has .chain/.clsp suffix.
          2. Auto-discover: prefer a collection with ~TYPE=RE_CHAIN_COLLECTION;
             fall back to any .chain/.clsp collection in the scene.
          3. Auto-create via re_chain.create_chain_header if still none found.

        Calling convention (post-toolkit patch):
          toolpanel.chainCollection = chain_col  ← PointerProperty assignment
          bpy.ops.mhws.auto_create_chains(settings_mode=...)  ← no chain_collection kwarg
        """
        code = (
            f"import bpy, json\n"
            f"def _is_cs(obj): return obj.get('TYPE') == 'RE_CHAIN_CHAINSETTINGS'\n"
            f"existing_cs = set(obj.name for obj in bpy.data.objects if _is_cs(obj))\n"
            # Look up arm_obj early so it can be used as active object before mode_set calls
            f"arm_obj = bpy.data.objects.get({target_arm!r})\n"
            # Step 1: discover chain collection
            f"chain_col = None\n"
            f"hint = {chain_col!r}\n"
            f"if hint:\n"
            f"    _c = bpy.data.collections.get(hint)\n"
            f"    if _c and ('.chain' in _c.name or '.clsp' in _c.name):\n"
            f"        chain_col = _c\n"
            # Auto-discover: prefer ~TYPE-tagged collection, fall back to name match
            f"if chain_col is None:\n"
            f"    for _c in bpy.data.collections:\n"
            f"        if ('.chain' in _c.name or '.clsp' in _c.name):\n"
            f"            if _c.get('~TYPE') == 'RE_CHAIN_COLLECTION':\n"
            f"                chain_col = _c\n"
            f"                break\n"
            f"            elif chain_col is None:\n"
            f"                chain_col = _c\n"
            # Step 2: auto-create if still not found
            f"if chain_col is None:\n"
            f"    if arm_obj: bpy.context.view_layer.objects.active = arm_obj\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.re_chain.create_chain_header(\n"
            f"        collectionName='MHWilds_Female',\n"
            f"        chainFormat='.chain2',\n"
            f"    )\n"
            f"    chain_col = bpy.data.collections.get('MHWilds_Female.chain2')\n"
            f"if chain_col is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:chain_collection_create_failed')\n"
            f"else:\n"
            # Step 3: set PointerProperty (avoids dynamic enum callback issues),
            # select armature in POSE mode, call auto_create_chains without kwarg
            f"    bpy.context.scene.re_chain_toolpanel.chainCollection = chain_col\n"
            f"    settings = bpy.context.scene.mhw_suite_settings\n"
            f"    settings.import_preset_enum = '怪猎荒野.json'\n"
            f"    if arm_obj: bpy.context.view_layer.objects.active = arm_obj\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    if arm_obj: arm_obj.select_set(True)\n"
            f"    bpy.ops.object.mode_set(mode='POSE')\n"
            f"    ret = bpy.ops.{_OP_AUTO_CHAINS}(settings_mode={settings_mode!r})\n"
            f"    new_cs = [obj.name for obj in bpy.data.objects\n"
            f"              if _is_cs(obj) and obj.name not in existing_cs]\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    if 'FINISHED' not in str(ret):\n"
            f"        print('CANCELLED:' + str(ret))\n"
            f"    else:\n"
            f"        print('NEW_CS:' + json.dumps({{'new_cs': new_cs, 'col': chain_col.name}}))\n"
        )
        lines = client.execute_and_extract(code, timeout=1200)
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator=_OP_AUTO_CHAINS,
                    message="auto_create_chains returned no output.",
                ),
                [],
                "",
            )
        if lines[0].startswith("PRECONDITION:chain_collection_create_failed"):
            return (
                PhaseError(
                    category="precondition",
                    operator=_OP_AUTO_CHAINS,
                    message=(
                        "No RE Chain collection found and auto-create failed. "
                        "A collection with '.chain' or '.clsp' in its name is required."
                    ),
                    suggestion=(
                        "In RE Chain Editor, create a chain collection "
                        "(e.g. 'MHWilds_Female.chain2') before running this phase."
                    ),
                ),
                [],
                "",
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
                "",
            )
        if lines[0].startswith("NEW_CS:"):
            try:
                payload = json.loads(lines[0][len("NEW_CS:"):])
                new_cs_names: list[str] = payload.get("new_cs", [])
                col_name: str = payload.get("col", "")
                return None, new_cs_names, col_name
            except json.JSONDecodeError:
                return (
                    PhaseError(
                        category="unexpected",
                        operator=_OP_AUTO_CHAINS,
                        message="Could not parse new chain settings names from Blender.",
                    ),
                    [],
                    "",
                )
        return (
            PhaseError(
                category="unexpected",
                operator=_OP_AUTO_CHAINS,
                message=f"Unexpected output: {lines[0]!r}",
            ),
            [],
            "",
        )

    def _recover_after_drop(
        self,
        client: BlenderClient,
        target_arm: str,
        inferred_types: dict[str, str],
        resolved_params: dict[str, dict],
    ) -> dict | None:
        """Reconnect after a dropped connection and apply params to any CS that exist.

        Returns a partial diff dict on success, None if recovery is impossible.
        Called from the OSError handler in run() — Blender may have finished
        auto_create_chains before the socket dropped.
        """
        try:
            client.reconnect()
        except Exception:
            return None  # Blender is gone; nothing to recover

        # Scan all chain collections for CHAINSETTINGS objects
        code = (
            f"import bpy, json\n"
            f"cs = [o.name for o in bpy.data.objects\n"
            f"      if o.get('TYPE') == 'RE_CHAIN_CHAINSETTINGS']\n"
            f"cols = [c.name for c in bpy.data.collections\n"
            f"        if '.chain' in c.name or '.clsp' in c.name]\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"print('CS:' + json.dumps(cs))\n"
            f"print('COLS:' + json.dumps(cols))\n"
        )
        try:
            lines = client.execute_and_extract(code)
        except Exception:
            return None

        all_cs: list[str] = []
        col_name: str = ""
        for line in lines:
            if line.startswith("CS:"):
                try:
                    all_cs = json.loads(line[len("CS:"):])
                except Exception:
                    pass
            elif line.startswith("COLS:"):
                try:
                    cols = json.loads(line[len("COLS:"):])
                    col_name = cols[0] if cols else ""
                except Exception:
                    pass

        if not all_cs:
            return None  # nothing was created; must retry from scratch

        # Best-effort: apply params to whatever CS exists
        try:
            skipped = self._apply_params_to_chain_settings(
                client, all_cs, inferred_types, resolved_params, "SEPARATE"
            )
        except Exception as exc:
            skipped = [str(exc)]

        # Best-effort: angle limit ramp
        if col_name:
            self._apply_angle_limit_ramp(client, col_name)

        return {
            "chain_settings_created": all_cs,
            "recovered_after_disconnect": True,
            "skipped_params": skipped,
        }

    def _find_existing_cs_in_collection(
        self, client: BlenderClient, chain_col_name: str
    ) -> list[str]:
        """Return CHAINSETTINGS object names inside chain_col_name (fallback for re-runs)."""
        code = (
            f"import bpy, json\n"
            f"col = bpy.data.collections.get({chain_col_name!r})\n"
            f"cs = [o.name for o in (col.all_objects if col else [])\n"
            f"      if o.get('TYPE') == 'RE_CHAIN_CHAINSETTINGS']\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"print('CS:' + json.dumps(cs))\n"
        )
        try:
            lines = client.execute_and_extract(code)
            if lines and lines[0].startswith("CS:"):
                return json.loads(lines[0][len("CS:"):])
        except Exception:
            pass
        return []

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

        NOTE: Parameter assignment uses Blender's RNA PropertyGroup interface (setattr).
        RE Chain Editor stores chain settings fields as PropertyGroup attributes.
        If a field cannot be set (read-only / wrong type), it is silently skipped and
        logged in the return list.
        """
        if not cs_names:
            return []

        # Build ordered list of params — one per cs_name
        ordered_bone_names = sorted(inferred_types.keys())
        params_list: list[dict] = []

        if settings_mode == "SHARED":
            first_bone = ordered_bone_names[0] if ordered_bone_names else ""
            shared_params = resolved_params.get(first_bone, {})
            params_list = [shared_params] * len(cs_names)
        else:
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
        lines = client.execute_and_extract(code, timeout=120)
        if lines and lines[0].startswith("APPLIED:"):
            try:
                return json.loads(lines[0][len("APPLIED:"):])
            except json.JSONDecodeError:
                pass
        return []

    def _consolidate_chain_settings(
        self,
        client: BlenderClient,
        canonical_cs_name: str,
        col_name: str,
        inferred_types: dict[str, str],
    ) -> tuple[PhaseError | None, dict[str, str]]:
        """
        Split the single shared CHAINSETTINGS into one CS per unique inferred_type.

        Strategy (SHARED + create per type):
          1. Discover CHAINGROUP → bone_name by reading the first child CHAINNODE's
             parent_bone property (re_chain_chainnode or re_chain_chainnodesettings PG).
          2. Group CHAINGROUPs by inferred_type.
          3. The alphabetically-first type gets the existing canonical CS.
          4. Each additional type: call re_chain.create_chain_settings() to create a new
             CS object inside the chain collection; detect by diffing object names.
          5. Reassign CHAINGROUPs of non-first types to their new CS.

        Returns (None, cs_to_type) — always non-fatal. Fallback: canonical CS → first type.
        """
        unique_types = sorted(set(inferred_types.values()))
        _fallback: dict[str, str] = {canonical_cs_name: unique_types[0] if unique_types else ""}

        inferred_json = json.dumps(inferred_types, ensure_ascii=False)
        code = (
            f"import bpy, json\n"
            f"canonical_cs_name = {canonical_cs_name!r}\n"
            f"col_name = {col_name!r}\n"
            f"inferred_types = {inferred_json}\n"
            f"col = bpy.data.collections.get(col_name)\n"
            # Step 1: discover CHAINGROUP→bone via first child CHAINNODE's parent_bone
            f"cg_to_bone = {{}}\n"
            f"col_obj_names = {{o.name for o in col.all_objects}} if col else None\n"
            f"for obj in bpy.data.objects:\n"
            f"    if obj.get('TYPE') != 'RE_CHAIN_CHAINGROUP': continue\n"
            f"    if col_obj_names is not None and obj.name not in col_obj_names: continue\n"
            f"    for child in obj.children:\n"
            f"        if child.get('TYPE') != 'RE_CHAIN_CHAINNODE': continue\n"
            f"        for pname in ('re_chain_chainnode', 're_chain_chainnodesettings'):\n"
            f"            pg = getattr(child, pname, None)\n"
            f"            if pg is not None and hasattr(pg, 'parent_bone') and pg.parent_bone:\n"
            f"                cg_to_bone[obj.name] = pg.parent_bone\n"
            f"                break\n"
            f"        if obj.name in cg_to_bone: break\n"
            # Step 2: group CHAINGROUPs by inferred_type
            f"type_to_cgs = {{}}\n"
            f"unmapped = []\n"
            f"for cg_name, bone_name in cg_to_bone.items():\n"
            f"    itype = inferred_types.get(bone_name)\n"
            f"    if itype: type_to_cgs.setdefault(itype, []).append(cg_name)\n"
            f"    else: unmapped.append(cg_name + ':' + bone_name)\n"
            f"unique_types = sorted(type_to_cgs.keys())\n"
            # Step 3: assign canonical CS to first type; create new CS for remaining types
            f"cs_to_type = {{}}\n"
            f"type_to_cs = {{}}\n"
            f"errors = []\n"
            f"if unique_types:\n"
            f"    type_to_cs[unique_types[0]] = canonical_cs_name\n"
            f"    cs_to_type[canonical_cs_name] = unique_types[0]\n"
            f"    if len(unique_types) > 1:\n"
            f"        _cs0 = bpy.data.objects.get(canonical_cs_name)\n"
            f"        if _cs0: bpy.context.view_layer.objects.active = _cs0\n"
            f"        bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"        if col: bpy.context.scene.re_chain_toolpanel.chainCollection = col\n"
            f"        for itype in unique_types[1:]:\n"
            f"            before = {{o.name for o in bpy.data.objects if o.get('TYPE') == 'RE_CHAIN_CHAINSETTINGS'}}\n"
            f"            try:\n"
            f"                bpy.ops.re_chain.create_chain_settings()\n"
            f"            except Exception as _e:\n"
            f"                errors.append('create_cs:' + itype + ':' + str(_e)[:80])\n"
            f"                type_to_cs[itype] = canonical_cs_name\n"
            f"                continue\n"
            f"            new_cands = [o.name for o in bpy.data.objects\n"
            f"                         if o.get('TYPE') == 'RE_CHAIN_CHAINSETTINGS' and o.name not in before]\n"
            f"            if new_cands:\n"
            f"                type_to_cs[itype] = new_cands[0]\n"
            f"                cs_to_type[new_cands[0]] = itype\n"
            f"            else:\n"
            f"                errors.append('create_cs:' + itype + ':no_new_object')\n"
            f"                type_to_cs[itype] = canonical_cs_name\n"
            # Step 4: reassign CHAINGROUPs of non-first types to their new CS
            f"    for itype, cg_names in type_to_cgs.items():\n"
            f"        target_cs_name = type_to_cs.get(itype, canonical_cs_name)\n"
            f"        if target_cs_name == canonical_cs_name: continue\n"
            f"        target_cs_obj = bpy.data.objects.get(target_cs_name)\n"
            f"        if not target_cs_obj: continue\n"
            f"        for cg_name in cg_names:\n"
            f"            cg = bpy.data.objects.get(cg_name)\n"
            f"            if not cg: continue\n"
            f"            for pname in ('re_chain_chaingroup', 're_chain_chaingroupsettings'):\n"
            f"                pg = getattr(cg, pname, None)\n"
            f"                if pg is not None and hasattr(pg, 'chainSetting'):\n"
            f"                    try: pg.chainSetting = target_cs_obj\n"
            f"                    except Exception as _e: errors.append('reassign:' + cg_name + ':' + str(_e)[:60])\n"
            f"                    break\n"
            f"else:\n"
            f"    first_type = sorted(set(inferred_types.values()))[0] if inferred_types else ''\n"
            f"    cs_to_type[canonical_cs_name] = first_type\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"print('CONSOLIDATED:' + json.dumps({{\n"
            f"    'cs_to_type': cs_to_type, 'type_to_cs': type_to_cs,\n"
            f"    'errors': errors, 'unmapped': unmapped,\n"
            f"}}))\n"
        )

        try:
            lines = client.execute_and_extract(code)
        except Exception:
            return None, _fallback

        if not lines:
            return None, _fallback
        if lines[0].startswith("CONSOLIDATED:"):
            try:
                payload = json.loads(lines[0][len("CONSOLIDATED:"):])
                cs_to_type: dict[str, str] = payload.get("cs_to_type", {})
                return None, cs_to_type if cs_to_type else _fallback
            except json.JSONDecodeError:
                pass
        return None, _fallback

    def _apply_params_by_type(
        self,
        client: BlenderClient,
        cs_to_type: dict[str, str],
        inferred_types: dict[str, str],
        resolved_params: dict[str, dict],
    ) -> list[str]:
        """
        Apply physics params to each chain settings object after consolidation.

        cs_to_type maps remaining CS object names to their inferred_type.
        Builds type → params from the first bone found for each type.
        Functionally identical to _apply_params_to_chain_settings but keyed by
        inferred_type rather than alphabetical bone position.
        """
        if not cs_to_type:
            return []

        # Build type → params (all bones of same type share identical params)
        type_to_params: dict[str, dict] = {}
        for bone_name, itype in inferred_types.items():
            if itype not in type_to_params:
                type_to_params[itype] = dict(resolved_params.get(bone_name, {}))

        collider_default = (
            _load_presets()
            .get("_usage_guide", {})
            .get("colliderFilterInfoPath_default", _DEFAULT_COLLIDER_PATH)
        )
        for p in type_to_params.values():
            p.setdefault("colliderFilterInfoPath", collider_default)

        cs_names = list(cs_to_type.keys())
        params_list = [type_to_params.get(cs_to_type[cs], {}) for cs in cs_names]
        params_json = json.dumps(params_list, ensure_ascii=False)
        cs_names_json = json.dumps(cs_names, ensure_ascii=False)
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

    def _apply_angle_limit_ramp(
        self,
        client: BlenderClient,
        chain_col_name: str,
    ) -> None:
        """
        Select all RE_CHAIN_CHAINGROUP objects in the chain collection and call
        re_chain.apply_angle_limit_ramp with default values (60° / 4 iterations).

        This step is non-fatal: failures are silently ignored. The ramp smoothly
        increases angle limits from the chain root to the tip (node 0 gets step*1,
        node 1 gets step*2, ..., nodes >= maxIteration get maxAngleLimit).
        """
        if not chain_col_name:
            return
        code = (
            f"import bpy\n"
            f"col = bpy.data.collections.get({chain_col_name!r})\n"
            f"if col is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('SKIP:collection_not_found')\n"
            f"else:\n"
            f"    _first_cg = next((o for o in col.all_objects if o.get('TYPE') == 'RE_CHAIN_CHAINGROUP'), None)\n"
            f"    if _first_cg: bpy.context.view_layer.objects.active = _first_cg\n"
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    last_cg = None\n"
            f"    for obj in col.all_objects:\n"
            f"        if obj.get('TYPE') == 'RE_CHAIN_CHAINGROUP':\n"
            f"            obj.select_set(True)\n"
            f"            last_cg = obj\n"
            f"    if last_cg is None:\n"
            f"        print({BLENDER_SENTINEL!r})\n"
            f"        print('SKIP:no_chain_groups')\n"
            f"    else:\n"
            f"        bpy.context.view_layer.objects.active = last_cg\n"
            f"        ret = bpy.ops.re_chain.apply_angle_limit_ramp(\n"
            f"            maxAngleLimit=1.047198,\n"
            f"            maxIteration=4,\n"
            f"        )\n"
            f"        print({BLENDER_SENTINEL!r})\n"
            f"        print('RAMP:' + str(ret))\n"
        )
        try:
            client.execute_and_extract(code, timeout=120)
        except Exception:
            pass  # non-fatal


# ── PhysicsAdjust ─────────────────────────────────────────────────────────────


class PhysicsAdjust(PhaseTool):
    """
    Adjust physics parameters on one or more RE_CHAIN_CHAINSETTINGS objects
    without re-creating chains.  Does NOT advance the phase.

    Typical use: fine-tune gravity, damping, spring force, wind coefficients
    after Phase 4B chain creation.

    Property value types:
      - Scalar fields (damping, springForce, etc.): number
      - Vector fields (gravity): [x, y, z]
      - Enum fields (windDelayType): string representation of int, e.g. "0"
    """

    @property
    def name(self) -> str:
        return "physics_adjust"

    @property
    def advances_phase(self) -> bool:
        return False

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "physics_adjust",
            "description": (
                "Adjust physics parameters on one or more RE_CHAIN_CHAINSETTINGS "
                "objects without re-creating chains. Use for post-creation fine-tuning: "
                "gravity, damping, reduceSelfDistanceRate, springForce, windEffectCoef, etc. "
                "Does not advance the phase — safe to call multiple times. "
                "Gravity example: [0, -9.8, 0] = full down, [0, 3, 0] = light up."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Names of RE_CHAIN_CHAINSETTINGS Blender objects to modify "
                            "(e.g. ['CHAIN_SETTINGS_04', 'CHAIN_SETTINGS_44'])."
                        ),
                    },
                    "properties": {
                        "type": "object",
                        "description": (
                            "Map of property name → new value. "
                            "Scalar: number. Vector: [x, y, z]. Enum: string int e.g. '0'. "
                            "Valid keys: damping, minDamping, reduceSelfDistanceRate, gravity, "
                            "springForce, shockAbsorptionRate, windEffectCoef, "
                            "envWindEffectCoef, motionForce."
                        ),
                    },
                },
                "required": ["targets", "properties"],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        targets: list[str] = params.get("targets", [])
        properties: dict = params.get("properties", {})

        if not targets:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'targets' must be a non-empty list of CHAIN_SETTINGS object names.",
                )
            )
        if not properties:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'properties' must be a non-empty dict of property_name → value.",
                )
            )

        try:
            err, result = self._apply_adjustments(client, targets, properties)
        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator="re_chain_chainsettings",
                    message="Blender error while adjusting physics parameters.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator="re_chain_chainsettings",
                    message="Lost connection to Blender during physics parameter adjustment.",
                    raw=str(exc),
                )
            )

        if err is not None:
            return PhaseResult.fail(err)
        return PhaseResult.ok(result)

    def _apply_adjustments(
        self,
        client: BlenderClient,
        targets: list[str],
        properties: dict,
    ) -> tuple[PhaseError | None, dict]:
        import json as _json

        props_json = _json.dumps(properties)
        targets_json = _json.dumps(targets)

        code = (
            f"import bpy, json\n"
            f"_sentinel = {BLENDER_SENTINEL!r}\n"
            f"_targets = {targets_json}\n"
            f"_props = {props_json}\n"
            f"_results = []\n"
            f"for _name in _targets:\n"
            f"    _obj = bpy.data.objects.get(_name)\n"
            f"    if _obj is None:\n"
            f"        _results.append({{'target': _name, 'status': 'not_found', 'errors': []}})\n"
            f"        continue\n"
            f"    _s = getattr(_obj, 're_chain_chainsettings', None)\n"
            f"    if _s is None:\n"
            f"        _results.append({{'target': _name, 'status': 'no_settings', 'errors': []}})\n"
            f"        continue\n"
            f"    _errs = []\n"
            f"    for _k, _v in _props.items():\n"
            f"        try:\n"
            f"            _cur = getattr(_s, _k, None)\n"
            f"            if _cur is not None and hasattr(_cur, '__len__') and isinstance(_v, list):\n"
            f"                for _i, _val in enumerate(_v):\n"
            f"                    _cur[_i] = _val\n"
            f"            else:\n"
            f"                setattr(_s, _k, _v)\n"
            f"        except Exception as _e:\n"
            f"            _errs.append(_k + ': ' + str(_e))\n"
            f"    _results.append({{'target': _name, 'status': 'ok', 'errors': _errs}})\n"
            f"print(_sentinel)\n"
            f"print(json.dumps({{'adjusted': _results}}, ensure_ascii=False))\n"
        )

        lines = client.execute_and_extract(code)
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator="re_chain_chainsettings",
                    message="Physics adjust returned no output from Blender.",
                ),
                {},
            )

        try:
            data = json.loads(lines[0])
        except json.JSONDecodeError:
            return (
                PhaseError(
                    category="unexpected",
                    operator="re_chain_chainsettings",
                    message=f"Could not parse physics adjust result: {lines[0]!r}",
                ),
                {},
            )

        not_found = [r["target"] for r in data["adjusted"] if r["status"] == "not_found"]
        if not_found:
            return (
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"CHAIN_SETTINGS objects not found: {not_found}",
                    suggestion=(
                        "Use get_object_props or list_objects to verify the exact "
                        "object names in the scene."
                    ),
                ),
                {},
            )

        prop_errors = {
            r["target"]: r["errors"]
            for r in data["adjusted"]
            if r.get("errors")
        }
        result: dict = {"adjusted_targets": [r["target"] for r in data["adjusted"] if r["status"] == "ok"]}
        if prop_errors:
            result["property_errors"] = prop_errors
        return None, result
