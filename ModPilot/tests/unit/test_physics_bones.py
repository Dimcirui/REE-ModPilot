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
        # Responses: validate=OK, create=NEW_CS, apply=APPLIED
        client = MagicMock()
        client.execute_and_extract.side_effect = [
            ["OK"],
            [f"NEW_CS:{json.dumps(new_cs)}"],
            [f"APPLIED:{json.dumps([])}"],
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
        assert result.state_diff["chain_settings_created"] == new_cs

    def test_skipped_params_recorded_in_state_diff(self):
        skipped = ["RE_CHAIN_CHAINSETTINGS_0.motionForce"]
        client = MagicMock()
        client.execute_and_extract.side_effect = [
            ["OK"],
            ["NEW_CS:[\"RE_CHAIN_CHAINSETTINGS_0\"]"],
            [f"APPLIED:{json.dumps(skipped)}"],
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

    def test_apply_params_separate_mode_ordering(self):
        """SEPARATE mode maps cs_names[i] to alphabetically sorted bone names[i]."""
        tool = PhysicsChains()
        # Two bone heads, two chain settings objects
        inferred_types = {"zz_bone": "hair_long_straight", "aa_bone": "hair_short"}
        resolved = {
            "aa_bone": get_physics_params("hair_short"),
            "zz_bone": get_physics_params("hair_long_straight"),
        }
        # Expect: aa_bone (index 0) → cs_0, zz_bone (index 1) → cs_1
        # The code injected into Blender should have params for hair_short first
        client = _make_client(["APPLIED:[]"])
        tool._apply_params_to_chain_settings(
            client,
            ["cs_0", "cs_1"],
            inferred_types,
            resolved,
            "SEPARATE",
        )
        call_args = client.execute_and_extract.call_args[0][0]
        # params_list[0] should have damping from hair_short (0.185)
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
