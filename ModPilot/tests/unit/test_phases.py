"""
Unit tests for Stage 2 phase tools.

All tests mock BlenderClient — no real Blender required.
Run with: uv run pytest -m unit tests/unit/test_phases.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.blender.client import BLENDER_SENTINEL, BlenderError
from app.blender.state import SceneCache, SceneState
from app.phases.base import (
    DEFAULT_Y_PRESET,
    PhaseError,
    PhaseResult,
    require_finished,
    wrap_with_sentinel,
)
from app.phases.pose_correction import PoseCorrection
from app.phases.skeleton_align import SkeletonAlign
from app.phases.vertex_groups import VertexGroups


# ── fixtures ───────────────────────────────────────────────────────────────


def make_client(extract_output: list[str] | None = None, raises: Exception | None = None):
    """
    Return a mock BlenderClient whose execute_and_extract() either returns
    the given lines, or raises the given exception.
    Also stubs get_scene_info() for SceneCache.refresh().
    """
    client = MagicMock()

    if raises is not None:
        client.execute_and_extract.side_effect = raises
    else:
        client.execute_and_extract.return_value = extract_output or [f"{{'FINISHED'}}"]

    client.get_scene_info.return_value = {
        "name": "Scene",
        "object_count": 2,
        "objects": [
            {"name": "Armature", "type": "ARMATURE"},
            {"name": "Body", "type": "MESH"},
        ],
        "materials_count": 0,
    }
    return client


def make_cache(client) -> SceneCache:
    return SceneCache(client)


# ── base helpers ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestBaseHelpers:
    def test_require_finished_ok(self):
        assert require_finished([f"{{'FINISHED'}}"], "modder.foo") is None

    def test_require_finished_cancelled(self):
        err = require_finished([f"{{'CANCELLED'}}"], "modder.foo")
        assert err is not None
        assert err.category == "operator_failed"
        assert "CANCELLED" in err.message

    def test_require_finished_empty(self):
        err = require_finished([], "modder.foo")
        assert err is not None
        assert err.category == "operator_failed"

    def test_phase_result_ok(self):
        r = PhaseResult.ok({"objects_added": ["NewMesh"]})
        assert r.success
        assert r.error is None
        assert "objects_added" in r.state_diff

    def test_phase_result_fail(self):
        e = PhaseError(category="precondition", operator="", message="bad")
        r = PhaseResult.fail(e)
        assert not r.success
        assert r.error is e
        assert r.state_diff == {}


# ── PoseCorrection ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPoseCorrection:
    """
    Tests for the redesigned 3-step PoseCorrection pipeline:
      Step 1 — pose_reset  (pose.transforms_clear)
      Step 2 — scale_align (mesh-bbox uniform scale)
      Step 3 — pose_convert (deterministic by x_preset)

    Default mock returns [{'FINISHED'}] for every execute_and_extract call.
    For scale_align, non-PRECONDITION output is treated as success, so the
    default mock value passes through cleanly.
    """

    def _phase(self):
        return PoseCorrection()

    def _base_params(self, x_preset="MMD"):
        return {
            "x_preset": x_preset,
            "source_armature": "SourceArm",
            "target_armature": "GameArm",
        }

    # ── validation ─────────────────────────────────────────────────────────

    def test_name(self):
        assert self._phase().name == "pose_correction"

    def test_invalid_x_preset(self):
        client = make_client()
        cache = make_cache(client)
        params = self._base_params()
        params["x_preset"] = "Unknown"
        result = self._phase().run(client, cache, params)
        assert not result.success
        assert result.error.category == "precondition"
        assert "Unknown X preset" in result.error.message

    def test_missing_source_armature(self):
        client = make_client()
        cache = make_cache(client)
        params = self._base_params()
        params["source_armature"] = ""
        result = self._phase().run(client, cache, params)
        assert not result.success
        assert "source_armature" in result.error.message

    def test_missing_target_armature(self):
        client = make_client()
        cache = make_cache(client)
        params = self._base_params()
        params["target_armature"] = ""
        result = self._phase().run(client, cache, params)
        assert not result.success
        assert "target_armature" in result.error.message

    # ── step 1: pose reset ─────────────────────────────────────────────────

    def test_pose_reset_is_first_call(self):
        """First execute_and_extract call must contain pose.transforms_clear."""
        client = make_client()
        cache = make_cache(client)
        self._phase().run(client, cache, self._base_params())
        first_code = client.execute_and_extract.call_args_list[0].args[0]
        assert "transforms_clear" in first_code
        assert "SourceArm" in first_code

    def test_pose_reset_precondition_stops_pipeline(self):
        """If pose reset returns PRECONDITION, scale_align must NOT be called."""
        client = make_client(["PRECONDITION:armature_not_found"])
        cache = make_cache(client)
        result = self._phase().run(client, cache, self._base_params())
        assert not result.success
        assert result.error.category == "precondition"
        assert client.execute_and_extract.call_count == 1

    # ── step 2: scale align ────────────────────────────────────────────────

    def test_scale_align_is_second_call(self):
        """Second execute_and_extract call must reference both armature names."""
        client = make_client()
        cache = make_cache(client)
        self._phase().run(client, cache, self._base_params())
        second_code = client.execute_and_extract.call_args_list[1].args[0]
        assert "SourceArm" in second_code
        assert "GameArm" in second_code
        assert "bound_box" in second_code

    def test_scale_align_precondition_stops_pipeline(self):
        """If scale_align returns PRECONDITION, pose_convert must NOT be called."""
        client = make_client()
        client.execute_and_extract.side_effect = [
            [f"{{'FINISHED'}}"],          # pose_reset ok
            ["PRECONDITION:no_source_meshes"],  # scale_align fails
        ]
        cache = make_cache(client)
        result = self._phase().run(client, cache, self._base_params())
        assert not result.success
        assert result.error.category == "precondition"
        assert client.execute_and_extract.call_count == 2

    def test_skip_scale_align_skips_step2(self):
        """With skip_scale_align=True, only 2 calls: reset + pose_convert (MMD)."""
        client = make_client()
        cache = make_cache(client)
        params = self._base_params("MMD")
        params["skip_scale_align"] = True
        result = self._phase().run(client, cache, params)
        assert result.success
        assert client.execute_and_extract.call_count == 2
        # Second call should be pose_convert, not scale_align
        second_code = client.execute_and_extract.call_args_list[1].args[0]
        assert "tpose_direction" in second_code

    # ── step 3: pose convert ───────────────────────────────────────────────

    def test_mmd_calls_tpose_direction(self):
        """MMD x_preset: third call must invoke modder.tpose_direction."""
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, self._base_params("MMD"))
        assert result.success
        assert client.execute_and_extract.call_count == 3
        third_code = client.execute_and_extract.call_args_list[2].args[0]
        assert "tpose_direction" in third_code
        assert "MMD" in third_code

    def test_vrchat_skips_pose_convert(self):
        """VRChat x_preset: only 2 calls (reset + scale_align); no pose op."""
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, self._base_params("VRChat"))
        assert result.success
        assert client.execute_and_extract.call_count == 2

    def test_endfield_calls_apply_transform_forward(self):
        """终末地 x_preset: third call must invoke modder.apply_transform_forward."""
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, self._base_params("终末地"))
        assert result.success
        assert client.execute_and_extract.call_count == 3
        third_code = client.execute_and_extract.call_args_list[2].args[0]
        assert "apply_transform_forward" in third_code
        assert "终末地" in third_code  # both preset and transform_name

    def test_endfield_sets_pose_preset_enum(self):
        """终末地 conversion must set pose_preset_enum to '终末地' (the transform file)."""
        client = make_client()
        cache = make_cache(client)
        self._phase().run(client, cache, self._base_params("终末地"))
        third_code = client.execute_and_extract.call_args_list[2].args[0]
        assert "pose_preset_enum" in third_code

    # ── error propagation ──────────────────────────────────────────────────

    def test_blender_error_returns_unexpected(self):
        client = make_client(raises=BlenderError("SyntaxError"))
        cache = make_cache(client)
        result = self._phase().run(client, cache, self._base_params())
        assert not result.success
        assert result.error.category == "unexpected"

    def test_oserror_returns_timeout(self):
        client = make_client(raises=OSError("connection reset"))
        cache = make_cache(client)
        result = self._phase().run(client, cache, self._base_params())
        assert not result.success
        assert result.error.category == "timeout"


# ── SkeletonAlign ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSkeletonAlign:
    def _phase(self):
        return SkeletonAlign()

    def test_name(self):
        assert self._phase().name == "skeleton_align"

    def test_invalid_x_preset(self):
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "x_preset": "Unknown", "source_armature": "Src", "target_armature": "Tgt"
        })
        assert not result.success
        assert "Unknown X preset" in result.error.message

    def test_missing_armatures(self):
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "x_preset": "MMD", "source_armature": "", "target_armature": ""
        })
        assert not result.success

    def test_align_success(self):
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "x_preset": "MMD",
            "source_armature": "SourceArm",
            "target_armature": "GameArm",
        })
        assert result.success
        code = client.execute_and_extract.call_args.args[0]
        assert "universal_snap" in code
        assert "MMD" in code
        assert "怪猎荒野" in code  # default Y preset
        assert "SourceArm" in code
        assert "GameArm" in code

    def test_default_y_preset_is_mhws(self):
        """Y preset defaults to 怪猎荒野 when not specified."""
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "x_preset": "VRChat",
            "source_armature": "Src",
            "target_armature": "Tgt",
        })
        assert result.success
        code = client.execute_and_extract.call_args.args[0]
        assert "怪猎荒野" in code

    def test_target_active_in_code(self):
        """Target armature must be set as active_object (operator precondition)."""
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        self._phase().run(client, cache, {
            "x_preset": "MMD",
            "source_armature": "Src",
            "target_armature": "GameSkeleton",
        })
        code = client.execute_and_extract.call_args.args[0]
        # Target must be the last selected and set as active
        assert "objects.active = tgt" in code

    def test_objects_not_found_precondition(self):
        client = make_client(["PRECONDITION:objects_not_found:GameArm"])
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "x_preset": "MMD",
            "source_armature": "Src",
            "target_armature": "GameArm",
        })
        assert not result.success
        assert result.error.category == "precondition"

    def test_oserror_returns_timeout(self):
        client = make_client(raises=OSError("connection reset"))
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "x_preset": "MMD",
            "source_armature": "Src",
            "target_armature": "Tgt",
        })
        assert not result.success
        assert result.error.category == "timeout"


# ── VertexGroups ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestVertexGroups:
    def _phase(self):
        return VertexGroups()

    def test_name(self):
        assert self._phase().name == "vertex_groups"

    def test_empty_mesh_objects(self):
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "x_preset": "MMD",
            "mesh_objects": [],
            "source_armature": "Arm",
        })
        assert not result.success
        assert "mesh_objects" in result.error.message

    def test_success_calls_both_operators(self):
        """Both direct_convert and rename_bones_to_target must be called."""
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "x_preset": "MMD",
            "mesh_objects": ["Body", "Hair"],
            "source_armature": "Armature",
        })
        assert result.success
        assert client.execute_and_extract.call_count == 2

        first_code = client.execute_and_extract.call_args_list[0].args[0]
        second_code = client.execute_and_extract.call_args_list[1].args[0]
        assert "direct_convert" in first_code
        assert "rename_bones_to_target" in second_code

    def test_mesh_names_in_first_call(self):
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        self._phase().run(client, cache, {
            "x_preset": "VRChat",
            "mesh_objects": ["Body", "Hair", "Outfit"],
            "source_armature": "Armature",
        })
        first_code = client.execute_and_extract.call_args_list[0].args[0]
        assert "Body" in first_code
        assert "Hair" in first_code
        assert "Outfit" in first_code

    def test_chinese_presets_in_code(self):
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        self._phase().run(client, cache, {
            "x_preset": "终末地",
            "mesh_objects": ["Body"],
            "source_armature": "Arm",
        })
        for call in client.execute_and_extract.call_args_list:
            code = call.args[0]
            assert "终末地" in code
            assert "怪猎荒野" in code

    def test_first_op_precondition_stops_second(self):
        """If direct_convert fails, rename_bones_to_target must NOT be called."""
        client = make_client()
        client.execute_and_extract.return_value = ["PRECONDITION:not_found:Body"]
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "x_preset": "MMD",
            "mesh_objects": ["Body"],
            "source_armature": "Arm",
        })
        assert not result.success
        assert client.execute_and_extract.call_count == 1

    def test_second_op_armature_not_found(self):
        """direct_convert succeeds but rename fails — should return failure."""
        client = make_client()
        client.execute_and_extract.side_effect = [
            [f"{{'FINISHED'}}"],                          # direct_convert ok
            ["PRECONDITION:armature_not_found"],           # rename fails
        ]
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "x_preset": "MMD",
            "mesh_objects": ["Body"],
            "source_armature": "BadArm",
        })
        assert not result.success
        assert result.error.category == "precondition"
