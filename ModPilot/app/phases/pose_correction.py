"""
Phase 1 — Pose Correction (plan.md video 1).

A three-step deterministic pipeline applied before skeleton alignment (Phase 2):

  Step 1 — Pose reset
    Clear all pose transforms on the source armature so any residual bone
    rotations/locations from prior Blender operations do not pollute the result.
    Uses the built-in operator: bpy.ops.pose.transforms_clear()

  Step 2 — Arm-bone scale alignment  (skipped when skip_scale_align=True)
    Scale the source armature uniformly so its arm bones match the target's
    arm-bone vertical height. We average the world-space head Z of the six
    arm-segment bones (upperarm/forearm/hand × L/R) for each side, then
    derive ratio = mean(target_z) / mean(source_z).

    Why arms, not mesh bbox: mesh-bbox-Z is biased by anything that sticks
    out above the head (hats, props, hair, weapons), which trips up scale on
    a meaningful fraction of MMD / VRChat avatars. Arm-bone Z is stable
    because it's a structural shoulder-height signal that's invariant to
    surface geometry. Source bone names per slot come from the active
    X-preset's `main` candidate list; target bone names ARE the slot keys
    (MHWilds canonical naming). At least 2 bones per side must resolve.

    Feet are assumed at Z≈0 for both models (Modding-Toolkit's import
    operator does foot-align by default — see issue #13).

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

import json
from typing import Any

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.preset_catalog import discover_preset_dir, enumerate_x_presets
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

# Issue #13: the 6 arm-segment slots used for scale-align. Order is stable so
# any debug output is reproducible.
_ARM_SLOTS: tuple[str, ...] = (
    "upperarm_L", "forearm_L", "hand_L",
    "upperarm_R", "forearm_R", "hand_R",
)

# Cache of {x_preset_name: {slot: [main_candidate_names]}}, populated lazily on
# first call. Discovery requires Blender, so the cache is keyed by nothing and
# rebuilt only when set to None (test-injectable).
_ARM_CANDIDATE_CACHE: dict[str, dict[str, list[str]]] | None = None


def _resolve_arm_candidates(
    client: BlenderClient,
    x_preset: str,
) -> dict[str, list[str]]:
    """Return {slot_key: [bone-name candidates]} for the 6 arm slots of the
    given X-preset (issue #13).

    Source-side bone names vary per preset (e.g. MMD's `upperarm_L` matches
    "Left arm" / "腕.L"); we pull the `main` candidate list from the preset's
    mappings so the scale-align code blob can try each in order. Slots not
    present in the preset fall back to the slot key itself — covers minimal
    custom presets or 怪猎荒野 (canonical-naming) rigs.

    Catalog lookup is cached for app lifetime; tests can monkeypatch this
    function directly or clear `_ARM_CANDIDATE_CACHE`.
    """
    global _ARM_CANDIDATE_CACHE
    if _ARM_CANDIDATE_CACHE is None:
        try:
            preset_dir = discover_preset_dir(client)
            catalog = enumerate_x_presets(preset_dir)
        except Exception:
            catalog = {}
        _ARM_CANDIDATE_CACHE = {
            name: {
                slot: list(meta.mappings.get(slot, {}).get("main", [])) or [slot]
                for slot in _ARM_SLOTS
            }
            for name, meta in catalog.items()
        }
    return _ARM_CANDIDATE_CACHE.get(
        x_preset,
        {slot: [slot] for slot in _ARM_SLOTS},
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
                    "y_preset": {
                        "type": "string",
                        "description": (
                            "Target game preset for arm-bone name lookup. "
                            "Default: 怪猎荒野 (MHWs). Change only for non-MHWs targets."
                        ),
                    },
                    "skip_scale_align": {
                        "type": "boolean",
                        "description": (
                            "Skip the scale-align step (issue #13: based on arm-bone "
                            "average height) if models are already correctly sized. "
                            "Default: false."
                        ),
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
        y_preset = params.get("y_preset", DEFAULT_Y_PRESET)
        if y_preset not in Y_PRESETS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Unknown Y preset {y_preset!r}. Valid: {sorted(Y_PRESETS)}",
                    suggestion="Use 怪猎荒野 for MHWs.",
                )
            )

        # ── entry spot-check ───────────────────────────────────────────────
        state_before = cache.refresh()

        # ── pipeline ───────────────────────────────────────────────────────
        try:
            # Step 1 — pose reset
            err = self._pose_reset(client, source_arm)
            if err is not None:
                return PhaseResult.fail(err)

            # Step 2 — arm-bone average-height scale alignment (issue #13)
            if not skip_scale:
                err = self._scale_align(client, source_arm, target_arm, x_preset, y_preset)
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
        x_preset: str,
        y_preset: str = DEFAULT_Y_PRESET,
    ) -> PhaseError | None:
        """
        Issue #13 — Arm-bone average-height scale alignment.

        For each of the 6 arm-segment slots (upperarm/forearm/hand × L/R) we
        sample world-space head Z on both armatures, then compute
        `ratio = mean(target_z) / mean(source_z)` and apply it as a uniform
        scale on the source armature (with transform_apply to bake).

        Bone names for both sides come from the preset system:
          source ← X-preset (MMD / VRChat / 终末地)
          target ← Y-preset (怪猎荒野 for MHWs)
        Each preset maps slot keys to the actual bone names in its rig.
        """
        src_candidates = _resolve_arm_candidates(client, x_preset)
        tgt_candidates = _resolve_arm_candidates(client, y_preset)
        src_candidates_json = json.dumps(src_candidates, ensure_ascii=False)
        tgt_candidates_json = json.dumps(tgt_candidates, ensure_ascii=False)
        slot_keys_json = json.dumps(list(_ARM_SLOTS))

        code = (
            f"import bpy, json\n"
            f"src_arm = bpy.data.objects.get({source_arm!r})\n"
            f"tgt_arm = bpy.data.objects.get({target_arm!r})\n"
            f"missing = []\n"
            f"if src_arm is None: missing.append({source_arm!r})\n"
            f"if tgt_arm is None: missing.append({target_arm!r})\n"
            f"if missing:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:objects_not_found:' + ','.join(missing))\n"
            f"else:\n"
            # Bake any unapplied scale on the source armature + mesh children
            # so matrix_world reflects the true visual size before sampling.
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    src_arm.select_set(True)\n"
            f"    for _ch in src_arm.children:\n"
            f"        if _ch.type == 'MESH': _ch.select_set(True)\n"
            f"    bpy.context.view_layer.objects.active = src_arm\n"
            f"    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)\n"
            f"    bpy.context.view_layer.update()\n"
            # ── arm-bone sampling ───────────────────────────────────────────
            f"    _src_candidates = json.loads({src_candidates_json!r})\n"
            f"    _tgt_candidates = json.loads({tgt_candidates_json!r})\n"
            f"    _slot_keys = json.loads({slot_keys_json!r})\n"
            # Both sides: try each candidate per slot; first match wins.
            f"    src_zs = []\n"
            f"    src_matched = []\n"
            f"    tgt_zs = []\n"
            f"    tgt_matched = []\n"
            f"    for slot in _slot_keys:\n"
            f"        for name in _src_candidates.get(slot, [slot]):\n"
            f"            pb = src_arm.pose.bones.get(name)\n"
            f"            if pb is not None:\n"
            f"                src_zs.append((src_arm.matrix_world @ pb.head).z)\n"
            f"                src_matched.append(slot)\n"
            f"                break\n"
            f"        for name in _tgt_candidates.get(slot, [slot]):\n"
            f"            pb = tgt_arm.pose.bones.get(name)\n"
            f"            if pb is not None:\n"
            f"                tgt_zs.append((tgt_arm.matrix_world @ pb.head).z)\n"
            f"                tgt_matched.append(slot)\n"
            f"                break\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    if len(src_zs) < 2:\n"
            f"        print('PRECONDITION:source_arm_bones_unresolved:' + ','.join(src_matched))\n"
            f"    elif len(tgt_zs) < 2:\n"
            f"        print('PRECONDITION:target_arm_bones_unresolved:' + ','.join(tgt_matched))\n"
            f"    else:\n"
            f"        src_h = sum(src_zs) / len(src_zs)\n"
            f"        tgt_h = sum(tgt_zs) / len(tgt_zs)\n"
            f"        if src_h <= 0:\n"
            f"            print('PRECONDITION:source_arm_height_zero')\n"
            f"        elif tgt_h <= 0:\n"
            f"            print('PRECONDITION:target_arm_height_zero')\n"
            f"        else:\n"
            f"            ratio = tgt_h / src_h\n"
            f"            src_arm.scale = (ratio, ratio, ratio)\n"
            f"            bpy.context.view_layer.objects.active = src_arm\n"
            f"            bpy.ops.object.select_all(action='DESELECT')\n"
            f"            src_arm.select_set(True)\n"
            f"            bpy.ops.object.transform_apply(\n"
            f"                location=False, rotation=False, scale=True)\n"
            f"            print(f'SCALE_OK:ratio={{ratio:.4f}} src_mean_z={{src_h:.3f}} tgt_mean_z={{tgt_h:.3f}} src_matched={{src_matched}} tgt_matched={{tgt_matched}}')\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("PRECONDITION:"):
            detail = lines[0][len("PRECONDITION:"):]
            return PhaseError(
                category="precondition",
                operator=_OP_APPLY_SCALE,
                message=f"Arm-bone scale alignment failed: {detail}",
                suggestion=(
                    "Issue #13 uses upperarm/forearm/hand bone heights for scale. "
                    "Ensure both armatures expose at least 2 of those slots — "
                    "candidate bone names come from the X-preset (source) and "
                    "Y-preset (target). If the models are already correctly sized, "
                    "pass skip_scale_align=true."
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
