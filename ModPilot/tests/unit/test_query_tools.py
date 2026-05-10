"""
Unit tests for app/phases/query_tools.py.

Covers:
  - QueryTool base class contract (name, tool_schema shape)
  - Each tool's run() output parsing with mocked BlenderClient
  - AgentLoop integration: query tools registered, don't advance phase,
    available in _build_query_tool_list

Run with: uv run pytest -m unit tests/unit/test_query_tools.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.phases.query_tools import (
    GetBoneInfo,
    ListCollections,
    ListObjects,
    QueryTool,
    SceneInfo,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_client(lines: list[str]) -> MagicMock:
    client = MagicMock()
    client.execute_and_extract.return_value = lines
    return client


def _make_scene_client(info: dict) -> MagicMock:
    client = MagicMock()
    client.get_scene_info.return_value = info
    return client


# ── QueryTool base contract ───────────────────────────────────────────────────


@pytest.mark.unit
class TestQueryToolContract:
    tools = [SceneInfo(), ListObjects(), GetBoneInfo(), ListCollections()]

    def test_all_tools_are_query_tool_instances(self):
        for t in self.tools:
            assert isinstance(t, QueryTool)

    def test_all_tools_have_non_empty_name(self):
        for t in self.tools:
            assert t.name

    def test_all_tool_schemas_have_required_keys(self):
        for t in self.tools:
            schema = t.tool_schema()
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["name"] == t.name

    def test_expected_tool_names(self):
        names = {t.name for t in self.tools}
        assert names == {"scene_info", "list_objects", "get_bone_info", "list_collections"}


# ── SceneInfo ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSceneInfo:
    tool = SceneInfo()

    def test_returns_json_from_get_scene_info(self):
        info = {"name": "Scene", "object_count": 5}
        result = self.tool.run(_make_scene_client(info), {})
        assert json.loads(result) == info

    def test_returns_error_json_on_exception(self):
        client = MagicMock()
        client.get_scene_info.side_effect = RuntimeError("connection lost")
        result = json.loads(self.tool.run(client, {}))
        assert "error" in result

    def test_schema_has_no_required_params(self):
        schema = self.tool.tool_schema()
        assert schema["input_schema"]["required"] == []


# ── ListObjects ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestListObjects:
    tool = ListObjects()

    def test_returns_blender_output_line(self):
        objs = [{"name": "Arm", "type": "ARMATURE", "visible": True}]
        client = _make_client([json.dumps(objs)])
        result = json.loads(self.tool.run(client, {}))
        assert result == objs

    def test_type_filter_passed_into_code(self):
        client = _make_client([json.dumps([])])
        self.tool.run(client, {"type_filter": "ARMATURE"})
        code = client.execute_and_extract.call_args[0][0]
        assert "ARMATURE" in code

    def test_empty_output_returns_empty_list(self):
        client = _make_client([])
        result = json.loads(self.tool.run(client, {}))
        assert result == []

    def test_exception_returns_error_json(self):
        client = MagicMock()
        client.execute_and_extract.side_effect = RuntimeError("socket error")
        result = json.loads(self.tool.run(client, {}))
        assert "error" in result


# ── GetBoneInfo ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestGetBoneInfo:
    tool = GetBoneInfo()

    def test_requires_armature_name_in_schema(self):
        schema = self.tool.tool_schema()
        assert "armature_name" in schema["input_schema"]["required"]

    def test_returns_bone_data(self):
        payload = {
            "armature": "MHWs",
            "total_bones": 2,
            "returned_bones": 1,
            "bones": [{"name": "hair_001", "parent": None, "custom_props": {"chain_role": "head"}}],
        }
        client = _make_client([json.dumps(payload)])
        result = json.loads(self.tool.run(client, {"armature_name": "MHWs"}))
        assert result["armature"] == "MHWs"
        assert len(result["bones"]) == 1

    def test_filter_custom_prop_appears_in_code(self):
        client = _make_client([json.dumps({"armature": "MHWs", "total_bones": 0, "returned_bones": 0, "bones": []})])
        self.tool.run(client, {"armature_name": "MHWs", "filter_custom_prop": "chain_role"})
        code = client.execute_and_extract.call_args[0][0]
        assert "chain_role" in code

    def test_exception_returns_error_json(self):
        client = MagicMock()
        client.execute_and_extract.side_effect = RuntimeError("socket error")
        result = json.loads(self.tool.run(client, {"armature_name": "MHWs"}))
        assert "error" in result


# ── ListCollections ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestListCollections:
    tool = ListCollections()

    def test_returns_collection_list(self):
        cols = [{"name": "MHWilds_Female.chain2", "object_count": 3,
                 "children": [], "custom_props": {"~TYPE": "RE_CHAIN_COLLECTION"}}]
        client = _make_client([json.dumps(cols)])
        result = json.loads(self.tool.run(client, {}))
        assert result[0]["name"] == "MHWilds_Female.chain2"

    def test_chain_only_flag_appears_in_code(self):
        client = _make_client([json.dumps([])])
        self.tool.run(client, {"chain_only": True})
        code = client.execute_and_extract.call_args[0][0]
        assert "true" in code  # json.dumps(True) → 'true'

    def test_exception_returns_error_json(self):
        client = MagicMock()
        client.execute_and_extract.side_effect = RuntimeError("socket error")
        result = json.loads(self.tool.run(client, {}))
        assert "error" in result


# ── AgentLoop integration ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestQueryToolsInLoop:
    def _make_loop(self):
        from app.agent.loop import AgentLoop
        llm = MagicMock()
        llm.chat.return_value = MagicMock(
            content="ok", has_tool_calls=False, tool_calls=[]
        )
        blender = MagicMock()
        blender.get_scene_info.return_value = {"name": "Scene", "object_count": 0}
        return AgentLoop(llm=llm, blender=blender)

    def test_query_tools_are_registered(self):
        loop = self._make_loop()
        names = set(loop._phase_tools.keys())
        assert {"scene_info", "list_objects", "get_bone_info", "list_collections"}.issubset(names)

    def test_build_query_tool_list_returns_only_query_tools(self):
        loop = self._make_loop()
        query = loop._build_query_tool_list()
        names = {t["name"] for t in query}
        assert names == {"scene_info", "list_objects", "get_bone_info", "list_collections"}
        # Phase tools must NOT appear
        assert "pose_correction" not in names

    @pytest.mark.asyncio
    async def test_query_tool_call_does_not_advance_phase(self):
        loop = self._make_loop()
        initial_idx = loop._phase_idx

        blender = loop._blender
        blender.get_scene_info.return_value = {"name": "Scene", "object_count": 1}

        result, error = await loop._execute_tool_call(
            {"id": "q1", "name": "scene_info", "input": {}}
        )
        assert error is None
        assert loop._phase_idx == initial_idx  # phase did NOT advance
        assert "Scene" in result or "object_count" in result

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_string(self):
        loop = self._make_loop()
        result, error = await loop._execute_tool_call(
            {"id": "x1", "name": "nonexistent_tool", "input": {}}
        )
        assert error is None
        assert "not available" in result
