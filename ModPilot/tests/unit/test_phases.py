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
    def _phase(self):
        return PoseCorrection()

    def test_name(self):
        assert self._phase().name == "pose_correction"

    def test_invalid_tool(self):
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, {"tool": "unknown", "x_preset": "MMD", "source_armature": "Arm"})
        assert not result.success
        assert result.error.category == "precondition"
        assert "Unknown pose tool" in result.error.message

    def test_invalid_x_preset(self):
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, {"tool": "direction", "x_preset": "Unknown", "source_armature": "Arm"})
        assert not result.success
        assert "Unknown X preset" in result.error.message

    def test_missing_source_armature(self):
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, {"tool": "direction", "x_preset": "MMD", "source_armature": ""})
        assert not result.success
        assert "source_armature" in result.error.message

    def test_direction_success(self):
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "tool": "direction",
            "x_preset": "MMD",
            "source_armature": "Armature",
        })
        assert result.success
        client.execute_and_extract.assert_called_once()
        code = client.execute_and_extract.call_args.args[0]
        assert "tpose_direction" in code
        assert "MMD" in code
        assert "Armature" in code

    def test_matrix_zero_uses_correct_op(self):
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        self._phase().run(client, cache, {
            "tool": "matrix_zero",
            "x_preset": "VRChat",
            "source_armature": "Armature",
        })
        code = client.execute_and_extract.call_args.args[0]
        assert "tpose_matrix_zero" in code

    def test_chinese_preset_in_code(self):
        """Chinese preset names must appear in the generated Blender code."""
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        self._phase().run(client, cache, {
            "tool": "direction",
            "x_preset": "终末地",
            "source_armature": "Armature",
        })
        code = client.execute_and_extract.call_args.args[0]
        assert "终末地" in code

    def test_object_not_found_returns_precondition_error(self):
        client = make_client(["PRECONDITION:object_not_found"])
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "tool": "direction",
            "x_preset": "MMD",
            "source_armature": "NonExistent",
        })
        assert not result.success
        assert result.error.category == "precondition"

    def test_blender_error_returns_unexpected(self):
        client = make_client(raises=BlenderError("SyntaxError"))
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "tool": "direction",
            "x_preset": "MMD",
            "source_armature": "Armature",
        })
        assert not result.success
        assert result.error.category == "unexpected"

    def test_record_requires_target_and_name(self):
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "tool": "record",
            "x_preset": "MMD",
            "source_armature": "ArmA",
            # missing target_armature and transform_name
        })
        assert not result.success

    def test_record_success(self):
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "tool": "record",
            "x_preset": "MMD",
            "source_armature": "ArmA",
            "target_armature": "ArmB",
            "transform_name": "mmd_to_tpose",
        })
        assert result.success
        code = client.execute_and_extract.call_args.args[0]
        assert "record_transform" in code
        assert "mmd_to_tpose" in code

    def test_apply_forward_requires_transform_name(self):
        client = make_client()
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "tool": "apply_forward",
            "x_preset": "MMD",
            "source_armature": "Arm",
            # missing transform_name
        })
        assert not result.success

    def test_apply_forward_success(self):
        client = make_client([f"{{'FINISHED'}}"])
        cache = make_cache(client)
        result = self._phase().run(client, cache, {
            "tool": "apply_forward",
            "x_preset": "MMD",
            "source_armature": "Arm",
            "transform_name": "mmd_to_tpose",
        })
        assert result.success
        code = client.execute_and_extract.call_args.args[0]
        assert "apply_transform_forward" in code


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
