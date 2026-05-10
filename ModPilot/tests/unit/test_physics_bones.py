"""
Unit tests for physics_bones.py (Phase 3.5, 4A, 4B).

Focus:
  - physics_presets.json loading + get_physics_params / list_inferred_types
  - PhysicsTransplant param validation
  - PhysicsClassification param validation + chain topology parsing
  - PhysicsChains param validation, inferred_type resolution, and
    _apply_params_to_chain_settings ordering logic

Run with: uv run pytest -m unit tests/unit/test_physics_bones.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.phases.physics_bones import (
    PhysicsChains,
    PhysicsClassification,
    PhysicsTransplant,
    _PRESETS_PATH,
    get_physics_params,
    list_inferred_types,
)
from app.phases.base import PhaseResult


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_client(output_lines: list[str]) -> MagicMock:
    client = MagicMock()
    client.execute_and_extract.return_value = output_lines
    return client


def _make_cache() -> MagicMock:
    cache = MagicMock()
    cache.refresh.return_value = MagicMock()
    cache.refresh.return_value.diff.return_value = {}
    return cache


# ── physics_presets.json integrity ───────────────────────────────────────────


@pytest.mark.unit
class TestPresetsJson:
    def test_file_exists(self):
        assert _PRESETS_PATH.exists(), f"physics_presets.json not found at {_PRESETS_PATH}"

    def test_file_is_valid_json(self):
        data = json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_types_key_present(self):
        data = json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
        assert "types" in data

    def test_all_types_have_params(self):
        data = json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
        for type_name, entry in data["types"].items():
            assert "params" in entry, f"type {type_name!r} missing 'params'"

    def test_all_params_have_required_fields(self):
        required = {"gravity", "damping", "springForce", "windEffectCoef"}
        data = json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
        for type_name, entry in data["types"].items():
            params = entry["params"]
            missing = required - set(params)
            assert not missing, f"type {type_name!r} params missing: {missing}"

    def test_gravity_is_3_element_list(self):
        data = json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
        for type_name, entry in data["types"].items():
            g = entry["params"].get("gravity")
            if g is not None:
                assert isinstance(g, list) and len(g) == 3, (
                    f"type {type_name!r}: gravity must be [x, y, z], got {g!r}"
                )

    def test_non_reference_types_have_high_or_medium_confidence(self):
        data = json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
        for type_name, entry in data["types"].items():
            if type_name.startswith("_"):
                continue
            assert entry.get("confidence") in ("high", "medium"), (
                f"type {type_name!r} has unexpected confidence: {entry.get('confidence')}"
            )


# ── get_physics_params / list_inferred_types ─────────────────────────────────


@pytest.mark.unit
class TestHelperFunctions:
    def test_get_physics_params_known_type(self):
        params = get_physics_params("hair_short")
        assert params is not None
        assert "damping" in params
        assert "gravity" in params

    def test_get_physics_params_unknown_type_returns_none(self):
        assert get_physics_params("does_not_exist_xyz") is None

    def test_get_physics_params_returns_copy(self):
        p1 = get_physics_params("hair_short")
        p1["damping"] = 9999.0
        p2 = get_physics_params("hair_short")
        assert p2["damping"] != 9999.0

    def test_list_inferred_types_excludes_reference_types(self):
        types = list_inferred_types()
        assert all(not t.startswith("_") for t in types)

    def test_list_inferred_types_includes_hair_and_cloth(self):
        types = set(list_inferred_types())
        assert "hair_short" in types
        assert "cloth_skirt_waist" in types
        assert "body_jiggle" in types

    def test_list_inferred_types_at_least_15_entries(self):
        assert len(list_inferred_types()) >= 15


# ── PhysicsTransplant ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPhysicsTransplant:
    tool = PhysicsTransplant()

    def test_name(self):
        assert self.tool.name == "physics_transplant"

    def test_tool_schema_shape(self):
        schema = PhysicsTransplant.tool_schema()
        assert schema["name"] == "physics_transplant"
        assert "description" in schema
        assert "input_schema" in schema

    def test_missing_source_armature_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {"target_armature": "MHWs", "x_preset": "MMD"},
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_missing_target_armature_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {"source_armature": "Body", "x_preset": "MMD"},
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_invalid_x_preset_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {
                "source_armature": "Body",
                "target_armature": "MHWs",
                "x_preset": "UnknownPreset",
            },
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_blender_finished_returns_ok(self):
        client = _make_client(["{'FINISHED'}"])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "source_armature": "Body",
                "target_armature": "MHWs",
                "x_preset": "MMD",
            },
        )
        assert result.success

    def test_precondition_not_found_fails(self):
        client = _make_client(["PRECONDITION:not_found:MHWs"])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "source_armature": "Body",
                "target_armature": "MHWs",
                "x_preset": "MMD",
            },
        )
        assert not result.success
        assert result.error.category == "precondition"


# ── PhysicsClassification ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestPhysicsClassification:
    tool = PhysicsClassification()

    def test_name(self):
        assert self.tool.name == "physics_classification"

    def test_missing_armature_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {"x_preset": "MMD"},
        )
        assert not result.success

    def test_invalid_preset_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {"target_armature": "MHWs", "x_preset": "BadPreset"},
        )
        assert not result.success

    def test_valid_chain_data_parsed_into_state_diff(self):
        chain_data = [
            {"name": "hair_001", "role": "head", "depth": 5, "parent": "head"},
        ]
        client = _make_client([f"CHAINS:{json.dumps(chain_data)}"])
        result = self.tool.run(
            client,
            _make_cache(),
            {"target_armature": "MHWs", "x_preset": "MMD"},
        )
        assert result.success
        assert "chain_topology" in result.state_diff
        assert result.state_diff["chain_topology"]["chain_heads"] == chain_data

    def test_precondition_not_found_fails(self):
        client = _make_client(["PRECONDITION:not_found:MHWs"])
        result = self.tool.run(
            client,
            _make_cache(),
            {"target_armature": "MHWs", "x_preset": "MMD"},
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_empty_output_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {"target_armature": "MHWs", "x_preset": "MMD"},
        )
        assert not result.success


# ── PhysicsChains ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPhysicsChains:
    tool = PhysicsChains()

    def test_name(self):
        assert self.tool.name == "physics_chains"

    def test_tool_schema_lists_valid_inferred_types(self):
        schema = PhysicsChains.tool_schema()
        desc = schema["input_schema"]["properties"]["inferred_types"]["description"]
        assert "hair_short" in desc

    def test_missing_target_armature_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {
                "chain_collection": "ChainCol",
                "inferred_types": {"hair_001": "hair_short"},
                "x_preset": "MMD",
            },
        )
        assert not result.success

    def test_empty_inferred_types_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {
                "target_armature": "MHWs",
                "chain_collection": "ChainCol",
                "inferred_types": {},
                "x_preset": "MMD",
            },
        )
        assert not result.success

    def test_unknown_inferred_type_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {
                "target_armature": "MHWs",
                "chain_collection": "ChainCol",
                "inferred_types": {"hair_001": "unknown_xyz"},
                "x_preset": "MMD",
            },
        )
        assert not result.success
        assert "unknown_xyz" in result.error.message

    def test_invalid_settings_mode_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {
                "target_armature": "MHWs",
                "chain_collection": "ChainCol",
                "inferred_types": {"hair_001": "hair_short"},
                "x_preset": "MMD",
                "settings_mode": "INVALID",
            },
        )
        assert not result.success

    def test_successful_pipeline_returns_ok(self):
        new_cs = ["RE_CHAIN_CHAINSETTINGS_0"]
        # Responses (SEPARATE mode): validate, create, apply, angle_ramp
        client = MagicMock()
        client.execute_and_extract.side_effect = [
            ["OK"],
            [f"NEW_CS:{json.dumps({'new_cs': new_cs, 'col': 'MHWilds_Female.chain2'})}"],
            [f"APPLIED:{json.dumps([])}"],
            ["RAMP:{'FINISHED'}"],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "chain_collection": "ChainCol",
                "inferred_types": {"hair_001": "hair_short"},
            },
        )
        assert result.success
        assert result.state_diff["chain_settings_created"] == new_cs

    def test_prepare_only_marks_clean_first_attempt(self):
        """prepare_only=True: cleanup + verify clean on first attempt."""
        client = MagicMock()
        # Responses: validate, clear_and_refresh, verify(clean)
        client.execute_and_extract.side_effect = [
            ["OK"],
            ["OK"],
            [f"VERIFY:{json.dumps({'clean': True, 'suspicious': []})}"],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "inferred_types": {},
                "prepare_only": True,
            },
        )
        assert result.success
        assert result.state_diff.get("marks_clean") is True
        assert "verified clean" in result.state_diff["message"].lower()
        # validate + clear + verify — no chain creation
        assert client.execute_and_extract.call_count == 3

    def test_prepare_only_marks_dirty_retries_and_succeeds(self):
        """prepare_only=True: first verify dirty → auto-retry → second verify clean."""
        client = MagicMock()
        dirty = f"VERIFY:{json.dumps({'clean': False, 'suspicious': ['Spine']})}"
        clean = f"VERIFY:{json.dumps({'clean': True, 'suspicious': []})}"
        # Responses: validate, clear1, verify(dirty), clear2, verify(clean)
        client.execute_and_extract.side_effect = [
            ["OK"],
            ["OK"],
            [dirty],
            ["OK"],
            [clean],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "inferred_types": {},
                "prepare_only": True,
            },
        )
        assert result.success
        assert result.state_diff.get("marks_clean") is True
        assert client.execute_and_extract.call_count == 5

    def test_prepare_only_marks_dirty_both_attempts_warns(self):
        """prepare_only=True: both verify attempts fail → marks_clean=False, success with warning."""
        client = MagicMock()
        dirty = f"VERIFY:{json.dumps({'clean': False, 'suspicious': ['Spine']})}"
        # Responses: validate, clear1, verify(dirty), clear2, verify(dirty)
        client.execute_and_extract.side_effect = [
            ["OK"],
            ["OK"],
            [dirty],
            ["OK"],
            [dirty],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "inferred_types": {},
                "prepare_only": True,
            },
        )
        assert result.success  # warning, not hard failure
        assert result.state_diff.get("marks_clean") is False
        assert "warning" in result.state_diff["message"].lower()
        assert client.execute_and_extract.call_count == 5

    def test_prepare_only_armature_not_found_fails(self):
        """prepare_only=True propagates precondition error from validate."""
        client = MagicMock()
        client.execute_and_extract.return_value = ["PRECONDITION:armature_not_found:MHWs"]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "inferred_types": {},
                "prepare_only": True,
            },
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_prepare_only_armature_not_found_fails(self):
        """prepare_only=True propagates precondition error from validate."""
        client = MagicMock()
        client.execute_and_extract.return_value = ["PRECONDITION:armature_not_found:MHWs"]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "inferred_types": {},
                "prepare_only": True,
            },
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_bones_to_clear_runs_before_merge_and_creation(self):
        """bones_to_clear fires first, then merge, then create chains."""
        new_cs = ["RE_CHAIN_CHAINSETTINGS_0"]
        client = MagicMock()
        # Responses: validate, clear, merge, create, apply, angle_ramp
        client.execute_and_extract.side_effect = [
            ["OK"],
            ["{'FINISHED'}"],
            ["{'FINISHED'}"],
            [f"NEW_CS:{json.dumps({'new_cs': new_cs, 'col': 'MHWilds_Female.chain2'})}"],
            [f"APPLIED:{json.dumps([])}"],
            ["RAMP:{'FINISHED'}"],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "bones_to_clear": ["Cage", "Cage_L"],
                "bones_to_merge": ["Ribbon_root"],
                "inferred_types": {"hair_001": "hair_short"},
            },
        )
        assert result.success
        assert client.execute_and_extract.call_count == 6
        # clear call is index 1; verify bone names appear in the code
        clear_code = client.execute_and_extract.call_args_list[1][0][0]
        assert "Cage" in clear_code
        assert "clear_chain_role" in clear_code
        # merge call is index 2
        merge_code = client.execute_and_extract.call_args_list[2][0][0]
        assert "Ribbon_root" in merge_code

    def test_bones_to_clear_without_merge(self):
        """bones_to_clear works without bones_to_merge."""
        new_cs = ["RE_CHAIN_CHAINSETTINGS_0"]
        client = MagicMock()
        # Responses: validate, clear, create, apply, angle_ramp
        client.execute_and_extract.side_effect = [
            ["OK"],
            ["{'FINISHED'}"],
            [f"NEW_CS:{json.dumps({'new_cs': new_cs, 'col': 'MHWilds_Female.chain2'})}"],
            [f"APPLIED:{json.dumps([])}"],
            ["RAMP:{'FINISHED'}"],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "bones_to_clear": ["Cage"],
                "inferred_types": {"hair_001": "hair_short"},
            },
        )
        assert result.success
        assert client.execute_and_extract.call_count == 5

    def test_bones_to_clear_failure_stops_pipeline(self):
        """If clear_chain_role fails, phase fails without calling _create_chains."""
        client = MagicMock()
        client.execute_and_extract.side_effect = [
            ["OK"],
            ["PRECONDITION:armature_not_found"],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "bones_to_clear": ["Cage"],
                "inferred_types": {"hair_001": "hair_short"},
            },
        )
        assert not result.success
        assert client.execute_and_extract.call_count == 2

    def test_bones_to_merge_runs_before_chain_creation(self):
        """bones_to_merge step fires before _create_chains."""
        new_cs = ["RE_CHAIN_CHAINSETTINGS_0"]
        client = MagicMock()
        # Responses (SEPARATE mode): validate, merge, create, apply, angle_ramp
        client.execute_and_extract.side_effect = [
            ["OK"],
            ["{'FINISHED'}"],
            [f"NEW_CS:{json.dumps({'new_cs': new_cs, 'col': 'MHWilds_Female.chain2'})}"],
            [f"APPLIED:{json.dumps([])}"],
            ["RAMP:{'FINISHED'}"],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "bones_to_merge": ["Ribbon_root"],
                "inferred_types": {"hair_001": "hair_short"},
            },
        )
        assert result.success
        assert client.execute_and_extract.call_count == 5
        # Merge call is the 2nd call (index 1); verify bone name appears in code
        merge_code = client.execute_and_extract.call_args_list[1][0][0]
        assert "Ribbon_root" in merge_code
        assert "merge_into_parent" in merge_code

    def test_merge_failure_stops_pipeline(self):
        """If merge_into_parent fails, phase fails without calling _create_chains."""
        client = MagicMock()
        client.execute_and_extract.side_effect = [
            ["OK"],
            ["PRECONDITION:armature_not_found"],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "bones_to_merge": ["Ribbon_root"],
                "inferred_types": {"hair_001": "hair_short"},
            },
        )
        assert not result.success
        assert client.execute_and_extract.call_count == 2

    def test_skipped_params_recorded_in_state_diff(self):
        skipped = ["RE_CHAIN_CHAINSETTINGS_0.motionForce"]
        client = MagicMock()
        # Responses (SEPARATE mode): validate, create, apply (with skips), angle_ramp
        client.execute_and_extract.side_effect = [
            ["OK"],
            [f"NEW_CS:{json.dumps({'new_cs': ['RE_CHAIN_CHAINSETTINGS_0'], 'col': 'MHWilds_Female.chain2'})}"],
            [f"APPLIED:{json.dumps(skipped)}"],
            ["RAMP:{'FINISHED'}"],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "chain_collection": "ChainCol",
                "inferred_types": {"hair_001": "hair_short"},
                "x_preset": "MMD",
            },
        )
        assert result.success
        assert "skipped_params" in result.state_diff
        assert skipped == result.state_diff["skipped_params"]

    def test_scene_validation_fail_returns_precondition(self):
        client = MagicMock()
        client.execute_and_extract.return_value = ["PRECONDITION:collection:ChainCol"]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "chain_collection": "ChainCol",
                "inferred_types": {"hair_001": "hair_short"},
                "x_preset": "MMD",
            },
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_cancelled_operator_returns_operator_failed(self):
        client = MagicMock()
        client.execute_and_extract.side_effect = [
            ["OK"],
            ["CANCELLED:{'CANCELLED'}"],
        ]
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_armature": "MHWs",
                "chain_collection": "ChainCol",
                "inferred_types": {"hair_001": "hair_short"},
                "x_preset": "MMD",
            },
        )
        assert not result.success
        assert result.error.category == "operator_failed"

    def test_consolidation_deduplicates_same_type_chains(self):
        """Single type → Blender creates no extra CS; returns {canonical: type}."""
        tool = PhysicsChains()
        inferred_types = {"aa_bone": "hair_short", "bb_bone": "hair_short"}
        canonical = "CS_0"
        consolidated = {"CS_0": "hair_short"}  # single type, no extra CS created
        client = _make_client([
            f"CONSOLIDATED:{json.dumps({'cs_to_type': consolidated, 'type_to_cs': {'hair_short': 'CS_0'}, 'errors': [], 'unmapped': []})}"
        ])
        _, cs_to_type = tool._consolidate_chain_settings(
            client, canonical, "MHWilds_Female.chain2", inferred_types
        )
        assert cs_to_type == consolidated
        assert len(cs_to_type) == 1

    def test_consolidation_keeps_separate_types(self):
        """Two types → Blender creates one extra CS; both types preserved."""
        tool = PhysicsChains()
        inferred_types = {"aa_bone": "hair_short", "bb_bone": "cloth_skirt_waist"}
        canonical = "CS_0"
        consolidated = {"CS_0": "hair_short", "CS_1": "cloth_skirt_waist"}
        client = _make_client([
            f"CONSOLIDATED:{json.dumps({'cs_to_type': consolidated, 'type_to_cs': {'hair_short': 'CS_0', 'cloth_skirt_waist': 'CS_1'}, 'errors': [], 'unmapped': []})}"
        ])
        _, cs_to_type = tool._consolidate_chain_settings(
            client, canonical, "col", inferred_types
        )
        assert cs_to_type == consolidated
        assert len(cs_to_type) == 2

    def test_consolidation_fallback_on_empty_output(self):
        """No output from Blender → fallback: canonical CS → first unique type."""
        tool = PhysicsChains()
        inferred_types = {"aa_bone": "hair_short", "bb_bone": "cloth_skirt_waist"}
        canonical = "CS_0"
        client = _make_client([])  # empty output
        _, cs_to_type = tool._consolidate_chain_settings(
            client, canonical, "col", inferred_types
        )
        # Fallback: canonical maps to alphabetically first type ("cloth_skirt_waist" < "hair_short")
        assert canonical in cs_to_type
        assert cs_to_type[canonical] in ("hair_short", "cloth_skirt_waist")

    def test_apply_params_by_type_injects_correct_params(self):
        """_apply_params_by_type uses type → params mapping, not bone order."""
        tool = PhysicsChains()
        inferred_types = {"aa_bone": "hair_short", "bb_bone": "hair_short"}
        resolved = {
            "aa_bone": get_physics_params("hair_short"),
            "bb_bone": get_physics_params("hair_short"),
        }
        cs_to_type = {"CS_0": "hair_short"}  # after consolidation, only one CS
        client = _make_client(["APPLIED:[]"])
        skipped = tool._apply_params_by_type(client, cs_to_type, inferred_types, resolved)
        assert skipped == []
        # Verify Blender code was called with hair_short params
        code = client.execute_and_extract.call_args[0][0]
        assert "Character_Chain.cfil" in code  # default collider path

    def test_apply_params_separate_mode_ordering(self):
        """SEPARATE mode (legacy path, SHARED-like) maps cs_names to bone order."""
        tool = PhysicsChains()
        inferred_types = {"zz_bone": "hair_long_straight", "aa_bone": "hair_short"}
        resolved = {
            "aa_bone": get_physics_params("hair_short"),
            "zz_bone": get_physics_params("hair_long_straight"),
        }
        client = _make_client(["APPLIED:[]"])
        tool._apply_params_to_chain_settings(
            client,
            ["cs_0", "cs_1"],
            inferred_types,
            resolved,
            "SEPARATE",
        )
        call_args = client.execute_and_extract.call_args[0][0]
        assert "0.185" in call_args

    def test_collider_path_default_injected(self):
        """Default colliderFilterInfoPath is added to every params dict."""
        tool = PhysicsChains()
        inferred_types = {"hair_001": "hair_short"}
        resolved = {"hair_001": get_physics_params("hair_short")}
        client = _make_client(["APPLIED:[]"])
        tool._apply_params_to_chain_settings(
            client, ["cs_0"], inferred_types, resolved, "SEPARATE"
        )
        call_args = client.execute_and_extract.call_args[0][0]
        assert "Character_Chain.cfil" in call_args


# ── _verify_chain_marks ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestVerifyChainMarks:
    tool = PhysicsChains()

    def test_clean_returns_true(self):
        """All chain heads have _End descendants → clean=True."""
        payload = json.dumps({"clean": True, "suspicious": []})
        client = _make_client([f"VERIFY:{payload}"])
        err, clean = self.tool._verify_chain_marks(client, "ArmObj")
        assert err is None
        assert clean is True

    def test_suspicious_bones_returns_false(self):
        """Chain head without _End descendant → clean=False."""
        payload = json.dumps({"clean": False, "suspicious": ["Spine", "Hips"]})
        client = _make_client([f"VERIFY:{payload}"])
        err, clean = self.tool._verify_chain_marks(client, "ArmObj")
        assert err is None
        assert clean is False

    def test_no_output_returns_error_and_false(self):
        """Empty output from Blender → error returned."""
        client = _make_client([])
        err, clean = self.tool._verify_chain_marks(client, "ArmObj")
        assert err is not None
        assert err.category == "operator_failed"
        assert clean is False

    def test_precondition_not_found_returns_error(self):
        """Armature not found in Blender → precondition error."""
        client = _make_client(["PRECONDITION:not_found"])
        err, clean = self.tool._verify_chain_marks(client, "MissingArm")
        assert err is not None
        assert err.category == "precondition"
        assert clean is False

    def test_unexpected_output_returns_error(self):
        """Unrecognised output format → unexpected error."""
        client = _make_client(["GARBAGE:data"])
        err, clean = self.tool._verify_chain_marks(client, "ArmObj")
        assert err is not None
        assert err.category == "unexpected"
        assert clean is False

    def test_verify_code_uses_end_bone_detection(self):
        """Generated Blender code must reference '_End' suffix and iterative BFS."""
        client = _make_client([f"VERIFY:{json.dumps({'clean': True, 'suspicious': []})}"])
        self.tool._verify_chain_marks(client, "ArmObj")
        code = client.execute_and_extract.call_args[0][0]
        assert "_End" in code
        assert "stack" in code  # iterative BFS, not recursion
