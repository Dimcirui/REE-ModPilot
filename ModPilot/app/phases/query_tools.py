"""
Read-only Blender scene query tools.

These tools let the agent (and ASK_MODE) inspect scene state without
advancing any phase.  Available in RUNNING_PHASE and ASK_MODE.

Tools:
  scene_info       — basic scene metadata (object count, active object, mode)
  list_objects     — enumerate objects, optionally filtered by Blender type
  get_bone_info    — pose bone list + custom props for a named armature
  list_collections — collections + their custom props (~TYPE, etc.)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from app.blender.client import BLENDER_SENTINEL, BlenderClient


class QueryTool(ABC):
    """
    Base class for read-only Blender scene inspection tools.

    run() returns a JSON string directly — no PhaseResult, no phase advancement.
    AgentLoop._execute_tool_call() checks isinstance(tool, QueryTool) to route
    these separately from phase tools.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @classmethod
    @abstractmethod
    def tool_schema(cls) -> dict[str, Any]: ...

    @abstractmethod
    def run(self, client: BlenderClient, params: dict) -> str: ...


# ── scene_info ────────────────────────────────────────────────────────────────


class SceneInfo(QueryTool):
    @property
    def name(self) -> str:
        return "scene_info"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "scene_info",
            "description": (
                "Return basic metadata about the current Blender scene: "
                "active object name and type, current mode, total object count, "
                "and top-level collection names. Use to verify the scene is ready "
                "before or after a phase."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }

    def run(self, client: BlenderClient, params: dict) -> str:
        try:
            info = client.get_scene_info()
            return json.dumps(info, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ── list_objects ──────────────────────────────────────────────────────────────


class ListObjects(QueryTool):
    @property
    def name(self) -> str:
        return "list_objects"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "list_objects",
            "description": (
                "List objects in the Blender scene, optionally filtered by "
                "Blender object type (ARMATURE, MESH, EMPTY, etc.). "
                "Returns name, type, and viewport visibility for each object."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "type_filter": {
                        "type": "string",
                        "description": (
                            "Blender object type to filter by "
                            "(e.g. 'ARMATURE', 'MESH', 'EMPTY'). "
                            "Omit to list all objects."
                        ),
                    },
                },
                "required": [],
            },
        }

    def run(self, client: BlenderClient, params: dict) -> str:
        type_filter = params.get("type_filter", "")
        code = (
            f"import bpy, json\n"
            f"type_filter = {type_filter!r}\n"
            f"objs = list(bpy.data.objects)\n"
            f"if type_filter:\n"
            f"    objs = [o for o in objs if o.type == type_filter.upper()]\n"
            f"result = [\n"
            f"    {{'name': o.name, 'type': o.type, 'visible': not o.hide_viewport}}\n"
            f"    for o in objs\n"
            f"]\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"print(json.dumps(result, ensure_ascii=False))\n"
        )
        try:
            lines = client.execute_and_extract(code)
            return lines[0] if lines else json.dumps([])
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ── get_bone_info ─────────────────────────────────────────────────────────────


class GetBoneInfo(QueryTool):
    @property
    def name(self) -> str:
        return "get_bone_info"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "get_bone_info",
            "description": (
                "Return pose bone information for an ARMATURE object. "
                "Lists bones with their parent name and custom properties "
                "(chain_role, etc.). "
                "Use filter_custom_prop='chain_role' to see only physics bones."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "armature_name": {
                        "type": "string",
                        "description": "Name of the ARMATURE object in the Blender scene.",
                    },
                    "filter_custom_prop": {
                        "type": "string",
                        "description": (
                            "If set, only return bones that have this custom property key. "
                            "E.g. 'chain_role' to see only physics-annotated bones."
                        ),
                    },
                },
                "required": ["armature_name"],
            },
        }

    def run(self, client: BlenderClient, params: dict) -> str:
        arm_name = params.get("armature_name", "")
        filter_prop = params.get("filter_custom_prop", "")
        code = (
            f"import bpy, json\n"
            f"arm_obj = bpy.data.objects.get({arm_name!r})\n"
            f"filter_prop = {filter_prop!r}\n"
            f"if arm_obj is None or arm_obj.type != 'ARMATURE':\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(json.dumps({{'error': 'Armature not found: ' + {arm_name!r}}}))\n"
            f"else:\n"
            f"    bones = []\n"
            f"    for pb in arm_obj.pose.bones:\n"
            f"        custom = {{k: v for k, v in pb.items() if not k.startswith('_')}}\n"
            f"        if filter_prop and filter_prop not in custom:\n"
            f"            continue\n"
            f"        bones.append({{\n"
            f"            'name': pb.name,\n"
            f"            'parent': pb.parent.name if pb.parent else None,\n"
            f"            'custom_props': custom,\n"
            f"        }})\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(json.dumps({{\n"
            f"        'armature': {arm_name!r},\n"
            f"        'total_bones': len(arm_obj.pose.bones),\n"
            f"        'returned_bones': len(bones),\n"
            f"        'bones': bones,\n"
            f"    }}, ensure_ascii=False))\n"
        )
        try:
            lines = client.execute_and_extract(code)
            return lines[0] if lines else json.dumps({"error": "no output"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ── list_collections ──────────────────────────────────────────────────────────


class ListCollections(QueryTool):
    @property
    def name(self) -> str:
        return "list_collections"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "list_collections",
            "description": (
                "List Blender collections and their custom properties. "
                "Use chain_only=true to find RE Chain collections "
                "(those with '.chain' or '.clsp' in the name). "
                "Useful for diagnosing why chain collection discovery failed."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "chain_only": {
                        "type": "boolean",
                        "description": (
                            "If true, only return collections whose name contains "
                            "'.chain' or '.clsp'."
                        ),
                    },
                },
                "required": [],
            },
        }

    def run(self, client: BlenderClient, params: dict) -> str:
        chain_only = params.get("chain_only", False)
        code = (
            f"import bpy, json\n"
            f"chain_only = {json.dumps(chain_only)}\n"
            f"cols = []\n"
            f"for col in bpy.data.collections:\n"
            f"    if chain_only and '.chain' not in col.name and '.clsp' not in col.name:\n"
            f"        continue\n"
            f"    custom = {{k: v for k, v in col.items()}}\n"
            f"    cols.append({{\n"
            f"        'name': col.name,\n"
            f"        'object_count': len(col.objects),\n"
            f"        'children': [c.name for c in col.children],\n"
            f"        'custom_props': custom,\n"
            f"    }})\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"print(json.dumps(cols, ensure_ascii=False))\n"
        )
        try:
            lines = client.execute_and_extract(code)
            return lines[0] if lines else json.dumps([])
        except Exception as exc:
            return json.dumps({"error": str(exc)})
