"""
Unit tests for SetupImportSource — the FBX source-rig import phase tool.

Mocks BlenderClient; no real Blender required.

Run with: uv run pytest -m unit tests/unit/test_setup_import_source.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.blender.client import BlenderError
from app.blender.state import SceneCache
from app.phases.setup import SetupImportSource


def _make_client(extract_output: list[str] | None = None, raises: Exception | None = None):
    client = MagicMock()
    if raises is not None:
        client.execute_and_extract.side_effect = raises
    else:
        client.execute_and_extract.return_value = extract_output or []
    client.get_scene_info.return_value = {
        "name": "Scene", "object_count": 0, "objects": [], "materials_count": 0,
    }
    return client


def _cache(client) -> SceneCache:
    return SceneCache(client)


@pytest.mark.unit
class TestSetupImportSource:
    def test_schema_requires_file_path(self):
        schema = SetupImportSource.tool_schema()
        assert schema["name"] == "setup_import_source"
        assert "file_path" in schema["input_schema"]["properties"]
        assert "file_path" in schema["input_schema"]["required"]

    def test_already_imported_returns_ok_without_running_op(self):
        """If the scene already has a source armature (user did File→Import
        themselves before talking to the agent), skip the operator entirely
        and report 'already_imported' so the bubble auto-advances."""
        tool = SetupImportSource()
        client = _make_client(extract_output=[json.dumps({
            "status": "already_imported",
            "source_armature": "Armature.001",
        })])
        result = tool.run(client, _cache(client), {"file_path": "C:/x/source.fbx"})
        assert result.success
        assert result.state_diff["import_status"] == "already_imported"
        assert result.state_diff["source_armature"] == "Armature.001"

    def test_successful_import_returns_new_armature(self):
        tool = SetupImportSource()
        client = _make_client(extract_output=[json.dumps({
            "status": "imported",
            "source_armature": "Source",
            "imported_objects": ["Source", "Body", "Hair"],
        })])
        result = tool.run(
            client, _cache(client),
            {"file_path": "C:/path/to/source.fbx"},
        )
        assert result.success
        assert result.state_diff["import_status"] == "imported"
        assert result.state_diff["source_armature"] == "Source"

    def test_missing_file_path_param_fails_precondition(self):
        """LLM omitting file_path is a hard precondition failure — don't
        send empty path to bpy.ops, give the agent a clear error."""
        tool = SetupImportSource()
        client = _make_client()
        result = tool.run(client, _cache(client), {})
        assert not result.success
        assert result.error.category == "precondition"
        assert "file_path" in result.error.message.lower()

    def test_file_not_found_returns_precondition_error(self):
        """The Blender-side code reports file_not_found when os.path.isfile
        is False. Surface that as a precondition error with a clear suggestion
        so the LLM can re-ask the user for the correct path."""
        tool = SetupImportSource()
        client = _make_client(extract_output=[json.dumps({
            "status": "file_not_found",
            "file_path": "C:/does/not/exist.fbx",
        })])
        result = tool.run(
            client, _cache(client),
            {"file_path": "C:/does/not/exist.fbx"},
        )
        assert not result.success
        assert result.error.category == "precondition"
        assert "not found" in result.error.message.lower() or "does/not/exist" in result.error.message

    def test_operator_cancelled_returns_operator_failed(self):
        tool = SetupImportSource()
        client = _make_client(extract_output=[json.dumps({
            "status": "cancelled",
            "operator_result": "{'CANCELLED'}",
        })])
        result = tool.run(
            client, _cache(client),
            {"file_path": "C:/x/source.fbx"},
        )
        assert not result.success
        assert result.error.category == "operator_failed"

    def test_blender_disconnected_returns_timeout_error(self):
        tool = SetupImportSource()
        client = _make_client(raises=OSError("connection reset"))
        result = tool.run(
            client, _cache(client),
            {"file_path": "C:/x/source.fbx"},
        )
        assert not result.success
        assert result.error.category == "timeout"

    def test_blender_error_returns_unexpected(self):
        tool = SetupImportSource()
        client = _make_client(raises=BlenderError("internal blender error"))
        result = tool.run(
            client, _cache(client),
            {"file_path": "C:/x/source.fbx"},
        )
        assert not result.success
        assert result.error.category == "unexpected"

    def test_non_fbx_extension_rejected_at_precondition(self):
        """FBX-only scope (per design). Reject .obj/.glb/.blend so an LLM
        confusion can't run the wrong importer."""
        tool = SetupImportSource()
        client = _make_client()
        for path in ("C:/x/source.obj", "C:/x/source.glb", "C:/x/source.blend"):
            result = tool.run(client, _cache(client), {"file_path": path})
            assert not result.success, f"expected reject for {path}"
            assert result.error.category == "precondition"
            assert "fbx" in result.error.message.lower()
