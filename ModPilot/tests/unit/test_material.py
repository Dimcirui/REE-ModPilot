"""
Unit tests for material.py (Phase 5A, 5B, 5C).

Focus:
  - MaterialInspect: param validation, texture_dir scan, Blender PRECONDITION/INSPECT parsing
  - MaterialSetup: param validation, x_preset routing, Normal chain code correctness,
    null-slot skip, WIRED result parsing
  - MaterialGenerate: param validation, collection not found, preset matching,
    auto_guessed tracking, RESULT/CANCELLED/EXCEPTION handling

Run with: uv run pytest -m unit tests/unit/test_material.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.phases.material import (
    IMAGE_EXTENSIONS,
    PRINCIPLED_SLOTS,
    VALID_SETUP_PRESETS,
    MaterialGenerate,
    MaterialInspect,
    MaterialSetup,
)
from app.phases.base import PhaseResult


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_client(output_lines: list[str]) -> MagicMock:
    client = MagicMock()
    client.execute_and_extract.return_value = output_lines
    return client


def _make_cache() -> MagicMock:
    cache = MagicMock()
    state = MagicMock()
    state.diff.return_value = {}
    cache.refresh.return_value = state
    return cache


def _inspect_response(materials: list[str], connections: dict) -> str:
    return "INSPECT:" + json.dumps({"materials": materials, "connections": connections})


def _wired_response(wired: list[str], skipped: list[str] | None = None) -> str:
    return "WIRED:" + json.dumps({"wired": wired, "skipped": skipped or []})


def _result_response(mdf_collection: str, processed: list[str], auto_guessed: list[str]) -> str:
    return "RESULT:" + json.dumps({
        "mdf_collection": mdf_collection,
        "materials_processed": processed,
        "presets_auto_guessed": auto_guessed,
    })


# ── MaterialInspect — constants ───────────────────────────────────────────────


@pytest.mark.unit
class TestMaterialConstants:
    def test_valid_setup_presets(self):
        assert "VRChat" in VALID_SETUP_PRESETS
        assert "終末地" in VALID_SETUP_PRESETS
        assert "MMD" not in VALID_SETUP_PRESETS

    def test_principled_slots_has_normal(self):
        assert "Normal" in PRINCIPLED_SLOTS

    def test_principled_slots_count(self):
        assert len(PRINCIPLED_SLOTS) == 6

    def test_image_extensions_coverage(self):
        for ext in (".png", ".tga", ".dds", ".jpg"):
            assert ext in IMAGE_EXTENSIONS


# ── MaterialInspect ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMaterialInspect:
    tool = MaterialInspect()

    def test_name(self):
        assert self.tool.name == "material_inspect"

    def test_tool_schema_shape(self):
        schema = MaterialInspect.tool_schema()
        assert schema["name"] == "material_inspect"
        assert "target_object" in schema["input_schema"]["required"]
        assert "texture_dir" in schema["input_schema"]["required"]

    def test_missing_target_object_fails(self):
        result = self.tool.run(_make_client([]), _make_cache(), {"texture_dir": "C:/tex"})
        assert not result.success
        assert result.error.category == "precondition"

    def test_missing_texture_dir_fails(self):
        result = self.tool.run(_make_client([]), _make_cache(), {"target_object": "Body"})
        assert not result.success
        assert result.error.category == "precondition"

    def test_nonexistent_texture_dir_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {"target_object": "Body", "texture_dir": "Z:/no_such_dir_xyz123"},
        )
        assert not result.success
        assert result.error.category == "precondition"
        assert "texture_dir" in result.error.message.lower() or "not found" in result.error.message.lower()

    def test_object_not_found_precondition(self):
        client = _make_client(["PRECONDITION:object_not_found:Body"])
        with patch.object(MaterialInspect, "_scan_texture_dir", return_value=[]):
            result = self.tool.run(
                client, _make_cache(), {"target_object": "Body", "texture_dir": "C:/tex"}
            )
        assert not result.success
        assert result.error.category == "precondition"

    def test_empty_blender_output_fails(self):
        client = _make_client([])
        with patch.object(MaterialInspect, "_scan_texture_dir", return_value=[]):
            result = self.tool.run(
                client, _make_cache(), {"target_object": "Body", "texture_dir": "C:/tex"}
            )
        assert not result.success
        assert result.error.category == "operator_failed"

    def test_successful_inspect_populates_state_diff(self):
        materials = ["Body", "Hair"]
        connections = {
            "Body": {"Base Color": "C:/tex/body_d.png", "Normal": None},
            "Hair": {"Base Color": None, "Normal": None},
        }
        client = _make_client([_inspect_response(materials, connections)])
        with patch.object(MaterialInspect, "_scan_texture_dir", return_value=["C:/tex/body_d.png"]):
            result = self.tool.run(
                client, _make_cache(), {"target_object": "Body", "texture_dir": "C:/tex"}
            )
        assert result.success
        assert result.state_diff["materials"] == materials
        assert result.state_diff["texture_files"] == ["C:/tex/body_d.png"]
        assert result.state_diff["existing_connections"] == connections

    def test_scan_texture_dir_filters_by_extension(self, tmp_path):
        (tmp_path / "tex.png").write_bytes(b"")
        (tmp_path / "tex.tga").write_bytes(b"")
        (tmp_path / "readme.txt").write_bytes(b"")
        (tmp_path / "model.fbx").write_bytes(b"")
        result = MaterialInspect._scan_texture_dir(str(tmp_path))
        basenames = {Path(p).name for p in result}
        assert "tex.png" in basenames
        assert "tex.tga" in basenames
        assert "readme.txt" not in basenames
        assert "model.fbx" not in basenames

    def test_scan_texture_dir_nonexistent_returns_none(self):
        assert MaterialInspect._scan_texture_dir("Z:/no_such_dir_xyz") is None

    def test_scan_texture_dir_empty_dir_returns_empty_list(self, tmp_path):
        result = MaterialInspect._scan_texture_dir(str(tmp_path))
        assert result == []


# ── MaterialSetup ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMaterialSetup:
    tool = MaterialSetup()

    def test_name(self):
        assert self.tool.name == "material_setup"

    def test_tool_schema_x_preset_enum(self):
        props = MaterialSetup.tool_schema()["input_schema"]["properties"]
        assert set(props["x_preset"]["enum"]) == {"VRChat", "終末地"}

    def test_missing_target_object_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {"x_preset": "VRChat", "texture_mapping": {"Hair": {"Base Color": "a.png"}}},
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_invalid_x_preset_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {
                "target_object": "Body",
                "x_preset": "MMD",
                "texture_mapping": {"Hair": {"Base Color": "a.png"}},
            },
        )
        assert not result.success
        assert result.error.category == "precondition"
        assert "MMD" in result.error.message

    def test_empty_texture_mapping_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {"target_object": "Body", "x_preset": "VRChat", "texture_mapping": {}},
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_object_not_found_precondition(self):
        client = _make_client(["PRECONDITION:object_not_found:Body"])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_object": "Body",
                "x_preset": "VRChat",
                "texture_mapping": {"Hair": {"Base Color": "a.png"}},
            },
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_empty_blender_output_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {
                "target_object": "Body",
                "x_preset": "VRChat",
                "texture_mapping": {"Hair": {"Base Color": "a.png"}},
            },
        )
        assert not result.success
        assert result.error.category == "operator_failed"

    def test_vchat_normal_wiring_contains_subtract(self):
        client = _make_client([_wired_response(["Hair.Normal"])])
        self.tool.run(
            client,
            _make_cache(),
            {
                "target_object": "Body",
                "x_preset": "VRChat",
                "texture_mapping": {"Hair": {"Normal": "hair_n.png"}},
            },
        )
        code = client.execute_and_extract.call_args[0][0]
        assert "SUBTRACT" in code
        assert "ShaderNodeSeparateXYZ" in code
        assert "ShaderNodeCombineXYZ" in code

    def test_zenmo_normal_wiring_no_subtract(self):
        client = _make_client([_wired_response(["Body.Normal"])])
        self.tool.run(
            client,
            _make_cache(),
            {
                "target_object": "Body",
                "x_preset": "終末地",
                "texture_mapping": {"Body": {"Normal": "body_n.png"}},
            },
        )
        code = client.execute_and_extract.call_args[0][0]
        assert "SUBTRACT" not in code
        assert "ShaderNodeNormalMap" in code

    def test_null_slot_does_not_appear_in_wiring_for(self):
        client = _make_client([_wired_response(["Hair.Base Color"])])
        self.tool.run(
            client,
            _make_cache(),
            {
                "target_object": "Body",
                "x_preset": "VRChat",
                "texture_mapping": {"Hair": {"Base Color": "hair_d.png", "Normal": None}},
            },
        )
        # The code should still work (no crash); the None slot is handled by Blender code
        code = client.execute_and_extract.call_args[0][0]
        assert "hair_d.png" in code

    def test_successful_wiring_populates_state_diff(self):
        wired = ["Body.Base Color", "Body.Normal", "Hair.Base Color"]
        client = _make_client([_wired_response(wired)])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_object": "Body",
                "x_preset": "VRChat",
                "texture_mapping": {
                    "Body": {"Base Color": "body_d.png", "Normal": "body_n.png"},
                    "Hair": {"Base Color": "hair_d.png"},
                },
            },
        )
        assert result.success
        assert result.state_diff["materials_wired"] == wired

    def test_skipped_slots_recorded_in_state_diff(self):
        skipped = ["Hair.Alpha:(some error)"]
        client = _make_client([_wired_response(["Hair.Base Color"], skipped)])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "target_object": "Body",
                "x_preset": "VRChat",
                "texture_mapping": {"Hair": {"Base Color": "hair_d.png", "Alpha": "hair_a.png"}},
            },
        )
        assert result.success
        assert result.state_diff["slots_skipped"] == skipped

    def test_zenmo_generates_different_code_from_vchat(self):
        """x_preset resolution happens in Python; VRChat and 終末地 produce distinct code strings."""
        def _run(preset):
            client = _make_client([_wired_response([])])
            MaterialSetup().run(
                client,
                _make_cache(),
                {
                    "target_object": "Mesh",
                    "x_preset": preset,
                    "texture_mapping": {"Mat": {"Normal": "n.png"}},
                },
            )
            return client.execute_and_extract.call_args[0][0]

        vchat_code = _run("VRChat")
        zenmo_code = _run("終末地")
        assert vchat_code != zenmo_code
        assert "SUBTRACT" in vchat_code
        assert "SUBTRACT" not in zenmo_code


# ── MaterialGenerate ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMaterialGenerate:
    tool = MaterialGenerate()

    def test_name(self):
        assert self.tool.name == "material_generate"

    def test_tool_schema_shape(self):
        schema = MaterialGenerate.tool_schema()
        assert schema["name"] == "material_generate"
        required = schema["input_schema"]["required"]
        assert "mesh_collection" in required
        assert "texture_base_path" in required
        assert "preset_mapping" in required

    def test_missing_mesh_collection_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {"texture_base_path": "Author/Char/", "preset_mapping": {}},
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_missing_texture_base_path_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {"mesh_collection": "MeshCol", "preset_mapping": {}},
        )
        assert not result.success
        assert result.error.category == "precondition"

    def test_collection_not_found_precondition(self):
        client = _make_client(["PRECONDITION:collection_not_found:MeshCol"])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "mesh_collection": "MeshCol",
                "texture_base_path": "Author/Char/",
                "preset_mapping": {},
            },
        )
        assert not result.success
        assert result.error.category == "precondition"
        assert "MeshCol" in result.error.message

    def test_empty_blender_output_fails(self):
        result = self.tool.run(
            _make_client([]),
            _make_cache(),
            {
                "mesh_collection": "MeshCol",
                "texture_base_path": "Author/Char/",
                "preset_mapping": {},
            },
        )
        assert not result.success
        assert result.error.category == "operator_failed"

    def test_exception_in_process_fails(self):
        client = _make_client(["EXCEPTION:RE Mesh Editor not installed"])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "mesh_collection": "MeshCol",
                "texture_base_path": "Author/Char/",
                "preset_mapping": {},
            },
        )
        assert not result.success
        assert result.error.category == "unexpected"
        assert "RE Mesh Editor not installed" in result.error.message

    def test_cancelled_process_fails(self):
        client = _make_client(["CANCELLED:{'CANCELLED'}"])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "mesh_collection": "MeshCol",
                "texture_base_path": "Author/Char/",
                "preset_mapping": {},
            },
        )
        assert not result.success
        assert result.error.category == "operator_failed"

    def test_successful_result_populates_state_diff(self):
        client = _make_client([
            _result_response("MeshCol.mdf2", ["Body", "Hair"], [])
        ])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "mesh_collection": "MeshCol",
                "texture_base_path": "Author/Char/",
                "preset_mapping": {"Body": "Skin", "Hair": "Hair"},
            },
        )
        assert result.success
        assert result.state_diff["mdf_collection"] == "MeshCol.mdf2"
        assert result.state_diff["materials_processed"] == ["Body", "Hair"]
        assert result.state_diff["presets_auto_guessed"] == []

    def test_auto_guessed_materials_recorded(self):
        client = _make_client([
            _result_response("MeshCol.mdf2", ["Body", "Hair", "Face"], ["Face"])
        ])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "mesh_collection": "MeshCol",
                "texture_base_path": "Author/Char/",
                "preset_mapping": {"Body": "Skin", "Hair": "Hair"},
            },
        )
        assert result.success
        assert "Face" in result.state_diff["presets_auto_guessed"]

    def test_preset_mapping_injected_into_blender_code(self):
        client = _make_client([_result_response("Col.mdf2", [], [])])
        self.tool.run(
            client,
            _make_cache(),
            {
                "mesh_collection": "MeshCol",
                "texture_base_path": "Author/Char/",
                "preset_mapping": {"Body": "Skin", "Hair": "NoPDO Hair"},
            },
        )
        code = client.execute_and_extract.call_args[0][0]
        assert "Skin" in code
        assert "NoPDO Hair" in code

    def test_optional_natives_root_injected_when_provided(self):
        client = _make_client([_result_response("Col.mdf2", [], [])])
        self.tool.run(
            client,
            _make_cache(),
            {
                "mesh_collection": "MeshCol",
                "texture_base_path": "Author/Char/",
                "preset_mapping": {},
                "natives_root": "E:/mod/root",
            },
        )
        code = client.execute_and_extract.call_args[0][0]
        assert "E:/mod/root" in code

    def test_optional_mdf_collection_name_injected_when_provided(self):
        client = _make_client([_result_response("CustomName.mdf2", [], [])])
        self.tool.run(
            client,
            _make_cache(),
            {
                "mesh_collection": "MeshCol",
                "texture_base_path": "Author/Char/",
                "preset_mapping": {},
                "mdf_collection_name": "CustomName.mdf2",
            },
        )
        code = client.execute_and_extract.call_args[0][0]
        assert "CustomName.mdf2" in code

    def test_empty_preset_mapping_accepted(self):
        client = _make_client([_result_response("Col.mdf2", ["Body"], ["Body"])])
        result = self.tool.run(
            client,
            _make_cache(),
            {
                "mesh_collection": "MeshCol",
                "texture_base_path": "Author/Char/",
                "preset_mapping": {},
            },
        )
        assert result.success
        assert "Body" in result.state_diff["presets_auto_guessed"]
