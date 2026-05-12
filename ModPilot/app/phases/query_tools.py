"""
Read-only Blender scene query tools.

These tools let the agent (and ASK_MODE) inspect scene state without
advancing any phase.  Available in RUNNING_PHASE and ASK_MODE.

Tools:
  scene_info       — basic scene metadata (object count, active object, mode)
  list_objects     — enumerate objects, optionally filtered by Blender type
  get_bone_info    — pose bone list + custom props for a named armature
  list_collections — collections + their custom props (~TYPE, etc.)
  get_mesh_info    — vertex groups, UV layers, vertex count, parent armature,
                     material slot names for a MESH object
  get_material_info — material slots with shader type and texture node paths
                      for a MESH object; detects broken/missing image links
  get_object_props — custom properties on any named object (covers RE Chain
                     objects: TYPE, chain_role, re_chain_* PropertyGroups)
"""

from __future__ import annotations

import json
import textwrap
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
            f"    custom = {{}}\n"
            f"    for k, v in col.items():\n"
            f"        try:\n"
            f"            custom[k] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)\n"
            f"        except Exception:\n"
            f"            custom[k] = '<unserializable>'\n"
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


# ── get_mesh_info ─────────────────────────────────────────────────────────────


class GetMeshInfo(QueryTool):
    @property
    def name(self) -> str:
        return "get_mesh_info"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "get_mesh_info",
            "description": (
                "Return mesh inspection data for a MESH object: "
                "vertex group names (confirms Phase 3 rename completion), "
                "UV layer names, vertex count, parent armature name, "
                "and material slot names. "
                "Use max_vgroups to limit output for large rigs."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "mesh_name": {
                        "type": "string",
                        "description": "Name of the MESH object in the Blender scene.",
                    },
                    "max_vgroups": {
                        "type": "integer",
                        "description": (
                            "Maximum number of vertex group names to return. "
                            "Default 50. Full count is always returned separately."
                        ),
                    },
                },
                "required": ["mesh_name"],
            },
        }

    def run(self, client: BlenderClient, params: dict) -> str:
        mesh_name = params.get("mesh_name", "")
        max_vgroups = int(params.get("max_vgroups", 50))
        code = (
            f"import bpy, json\n"
            f"mesh_obj = bpy.data.objects.get({mesh_name!r})\n"
            f"max_vg = {max_vgroups}\n"
            f"if mesh_obj is None or mesh_obj.type != 'MESH':\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(json.dumps({{'error': 'Mesh not found or not MESH type: ' + {mesh_name!r}}}))\n"
            f"else:\n"
            f"    vgroups = [vg.name for vg in mesh_obj.vertex_groups]\n"
            f"    mat_slots = [\n"
            f"        slot.material.name if slot.material else None\n"
            f"        for slot in mesh_obj.material_slots\n"
            f"    ]\n"
            f"    uv_layers = [uv.name for uv in mesh_obj.data.uv_layers]\n"
            f"    parent_arm = (\n"
            f"        mesh_obj.parent.name\n"
            f"        if mesh_obj.parent and mesh_obj.parent.type == 'ARMATURE'\n"
            f"        else None\n"
            f"    )\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(json.dumps({{\n"
            f"        'mesh': {mesh_name!r},\n"
            f"        'parent_armature': parent_arm,\n"
            f"        'vertex_count': len(mesh_obj.data.vertices),\n"
            f"        'vertex_group_count': len(vgroups),\n"
            f"        'vertex_groups': vgroups[:max_vg],\n"
            f"        'material_slots': mat_slots,\n"
            f"        'uv_layers': uv_layers,\n"
            f"    }}, ensure_ascii=False))\n"
        )
        try:
            lines = client.execute_and_extract(code)
            return lines[0] if lines else json.dumps({"error": "no output"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ── get_material_info ─────────────────────────────────────────────────────────


class GetMaterialInfo(QueryTool):
    @property
    def name(self) -> str:
        return "get_material_info"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "get_material_info",
            "description": (
                "Inspect material slots on a MESH object. "
                "For each slot returns: material name, whether nodes are enabled, "
                "the shader type connected to the Material Output (e.g. 'Principled BSDF', "
                "'Group'), and all Image Texture nodes with their image name, "
                "relative filepath, is_packed flag, and has_data flag "
                "(False = image not loaded → likely broken path). "
                "Use before Phase 5 to assess material state without modifying anything."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "mesh_name": {
                        "type": "string",
                        "description": "Name of the MESH object in the Blender scene.",
                    },
                },
                "required": ["mesh_name"],
            },
        }

    def run(self, client: BlenderClient, params: dict) -> str:
        mesh_name = params.get("mesh_name", "")
        code = (
            f"import bpy, json\n"
            f"mesh_obj = bpy.data.objects.get({mesh_name!r})\n"
            f"if mesh_obj is None or mesh_obj.type != 'MESH':\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(json.dumps({{'error': 'Mesh not found or not MESH type: ' + {mesh_name!r}}}))\n"
            f"else:\n"
            f"    slots = []\n"
            f"    for i, slot in enumerate(mesh_obj.material_slots):\n"
            f"        mat = slot.material\n"
            f"        if mat is None:\n"
            f"            slots.append({{'slot_index': i, 'material_name': None,\n"
            f"                           'use_nodes': False, 'shader_type': None,\n"
            f"                           'textures': []}})\n"
            f"            continue\n"
            f"        shader_type = None\n"
            f"        textures = []\n"
            f"        if mat.use_nodes and mat.node_tree:\n"
            # Identify the shader connected to Material Output surface socket
            f"            for node in mat.node_tree.nodes:\n"
            f"                if node.type == 'OUTPUT_MATERIAL':\n"
            f"                    surf = node.inputs.get('Surface')\n"
            f"                    if surf and surf.links:\n"
            f"                        shader_type = surf.links[0].from_node.bl_label\n"
            f"                    break\n"
            # Collect all Image Texture nodes (regardless of connection state)
            f"            for node in mat.node_tree.nodes:\n"
            f"                if node.type == 'TEX_IMAGE':\n"
            f"                    img = node.image\n"
            f"                    textures.append({{\n"
            f"                        'node': node.name,\n"
            f"                        'image': img.name if img else None,\n"
            f"                        'filepath': img.filepath if img else None,\n"
            f"                        'is_packed': bool(img.packed_file) if img else False,\n"
            # has_data=False → image reference exists but pixels not loaded (broken path)
            f"                        'has_data': bool(img.has_data) if img else False,\n"
            f"                    }})\n"
            f"        slots.append({{\n"
            f"            'slot_index': i,\n"
            f"            'material_name': mat.name,\n"
            f"            'use_nodes': mat.use_nodes,\n"
            f"            'shader_type': shader_type,\n"
            f"            'textures': textures,\n"
            f"        }})\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(json.dumps({{\n"
            f"        'mesh': {mesh_name!r},\n"
            f"        'material_slots': slots,\n"
            f"    }}, ensure_ascii=False))\n"
        )
        try:
            lines = client.execute_and_extract(code)
            return lines[0] if lines else json.dumps({"error": "no output"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ── get_object_props ──────────────────────────────────────────────────────────


class GetObjectProps(QueryTool):
    @property
    def name(self) -> str:
        return "get_object_props"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "get_object_props",
            "description": (
                "Return the custom properties of any named Blender object, "
                "plus its object type. "
                "Useful for inspecting RE Chain Editor objects "
                "(TYPE, chain_role, re_chain_chainsettings fields) "
                "or any other object with custom data. "
                "Keys starting with '_' (internal Blender props) are excluded."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "object_name": {
                        "type": "string",
                        "description": "Name of the Blender object to inspect.",
                    },
                },
                "required": ["object_name"],
            },
        }

    def run(self, client: BlenderClient, params: dict) -> str:
        obj_name = params.get("object_name", "")
        code = (
            f"import bpy, json\n"
            f"obj = bpy.data.objects.get({obj_name!r})\n"
            f"if obj is None:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(json.dumps({{'error': 'Object not found: ' + {obj_name!r}}}))\n"
            f"else:\n"
            f"    custom = {{}}\n"
            f"    for k, v in obj.items():\n"
            f"        if k.startswith('_'): continue\n"
            f"        try: custom[k] = v if isinstance(v, (str, int, float, bool)) else str(v)\n"
            f"        except Exception: custom[k] = '<unserializable>'\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print(json.dumps({{\n"
            f"        'object': obj.name,\n"
            f"        'type': obj.type,\n"
            f"        'custom_props': custom,\n"
            f"    }}, ensure_ascii=False))\n"
        )
        try:
            lines = client.execute_and_extract(code)
            return lines[0] if lines else json.dumps({"error": "no output"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


class InspectMaterialNodes(QueryTool):
    """
    Full node-tree dump of a single material.

    Use case: material_inspect's per-material summary only shows Principled BSDF
    slot connectivity. When the LLM needs to understand the real shader path
    (e.g. Emission → Material Output as in VRChat / MMD), or detect orphan
    image-texture nodes left over from a previous import, this tool returns the
    complete nodes + links graph plus convenience fields:

      - output_shader: which shader node is actually feeding Material Output.Surface
      - orphan_nodes:  nodes that participate in NO link — these include leftover
                       Image Texture data-blocks the LLM would otherwise miss

    Image data-blocks include the same {path, exists} schema as material_inspect.
    """

    @property
    def name(self) -> str:
        return "inspect_material_nodes"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "inspect_material_nodes",
            "description": (
                "Return the complete node tree of a single Blender material: every "
                "shader node, every link, the actual shader driving Material Output, "
                "and any orphan nodes (including stray Image Texture nodes not linked "
                "to anything). "
                "INPUT: material name (string). "
                "OUTPUT: {nodes[], links[], output_shader, orphan_nodes[]}. "
                "Each TEX_IMAGE node carries its image as {path, exists} (or null). "
                "\n"
                "Use this when material_inspect's PBS-slot summary is insufficient — "
                "e.g. when materials use Emission/MMDShader/MixShader instead of PBS, "
                "or when checking whether texture assets exist as orphan nodes outside "
                "the PBS chain. `exists` is the only authoritative path-validity signal "
                "(do not use any lazy-pixel-load flag for that judgment)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "material_name": {
                        "type": "string",
                        "description": "Name of the bpy.data.materials entry to dump.",
                    },
                },
                "required": ["material_name"],
            },
        }

    def run(self, client: BlenderClient, params: dict) -> str:
        material_name = params.get("material_name", "")
        if not material_name:
            return json.dumps({"error": "material_name is required"})

        header = (
            f"import bpy, json, os\n"
            f"_target = {material_name!r}\n"
            f"_sentinel = {BLENDER_SENTINEL!r}\n"
        )
        body = textwrap.dedent("""\
            def _resolve_image(img):
                if img is None:
                    return None
                fp = bpy.path.abspath(img.filepath) if img.filepath else ""
                if not fp:
                    return None
                return {"path": fp, "exists": os.path.exists(fp)}

            mat = bpy.data.materials.get(_target)
            if mat is None:
                print(_sentinel)
                print(json.dumps({"error": "material_not_found: " + _target}))
            elif mat.node_tree is None:
                print(_sentinel)
                print(json.dumps({
                    "material": _target,
                    "node_count": 0,
                    "nodes": [],
                    "links": [],
                    "output_shader": None,
                    "orphan_nodes": [],
                    "note": "material has no node_tree (use_nodes is False)",
                }))
            else:
                nodes_list = list(mat.node_tree.nodes)
                links_list = list(mat.node_tree.links)
                # Use node.name as key — Blender Python API returns a new wrapper
                # object each access, so id() is unreliable for identity comparison.
                # Node names are unique within a material node tree.
                idx_of = {n.name: i for i, n in enumerate(nodes_list)}

                node_entries = []
                for i, n in enumerate(nodes_list):
                    entry = {"id": i, "name": n.name, "type": n.type}
                    if n.type == "TEX_IMAGE":
                        entry["image"] = _resolve_image(n.image)
                    node_entries.append(entry)

                link_entries = []
                linked_node_ids = set()
                for lk in links_list:
                    fn_id = idx_of.get(lk.from_node.name)
                    tn_id = idx_of.get(lk.to_node.name)
                    if fn_id is None or tn_id is None:
                        continue
                    link_entries.append({
                        "from": str(fn_id) + "." + lk.from_socket.name,
                        "to":   str(tn_id) + "." + lk.to_socket.name,
                    })
                    linked_node_ids.add(fn_id)
                    linked_node_ids.add(tn_id)

                output_shader = None
                for i, n in enumerate(nodes_list):
                    if n.type != "OUTPUT_MATERIAL":
                        continue
                    surf = n.inputs.get("Surface")
                    if surf is not None and surf.is_linked:
                        upstream = surf.links[0].from_node
                        up_id = idx_of.get(upstream.name)
                        output_shader = {
                            "node_id": up_id,
                            "type": upstream.type,
                            "name": upstream.name,
                        }
                    break

                orphan_entries = [
                    node_entries[i] for i in range(len(nodes_list))
                    if i not in linked_node_ids
                ]

                print(_sentinel)
                print(json.dumps({
                    "material": _target,
                    "node_count": len(node_entries),
                    "nodes": node_entries,
                    "links": link_entries,
                    "output_shader": output_shader,
                    "orphan_nodes": orphan_entries,
                }, ensure_ascii=False))
        """)
        code = header + body
        try:
            lines = client.execute_and_extract(code)
            return lines[0] if lines else json.dumps({"error": "no output"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


class PhysicsRead(QueryTool):
    """
    Read current re_chain_chainsettings property values for one or more
    RE_CHAIN_CHAINSETTINGS objects.

    Returns each object's current physics parameter values as a JSON object.
    Use before physics_adjust to inspect current values, or to compare settings
    between two chain groups.

    If targets is an empty list, returns values for ALL CHAIN_SETTINGS objects
    in the scene.  If properties is empty, returns all known physics parameters.
    """

    _KNOWN_PROPS: tuple[str, ...] = (
        "damping",
        "minDamping",
        "reduceSelfDistanceRate",
        "gravity",
        "springForce",
        "shockAbsorptionRate",
        "windEffectCoef",
        "envWindEffectCoef",
        "motionForce",
        "colliderFilterInfoPath",
    )

    @property
    def name(self) -> str:
        return "physics_read"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "physics_read",
            "description": (
                "Read current re_chain_chainsettings property values for one or more "
                "RE_CHAIN_CHAINSETTINGS objects. Use before physics_adjust to check "
                "current values. Pass empty targets list to read all CHAIN_SETTINGS "
                "in the scene. Pass empty properties list to read all known parameters."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Names of RE_CHAIN_CHAINSETTINGS objects to read "
                            "(e.g. ['CHAIN_SETTINGS_04']). "
                            "Pass [] to read ALL CHAIN_SETTINGS objects in the scene."
                        ),
                    },
                    "properties": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Property names to read. Pass [] for all known physics params. "
                            "Valid keys: damping, minDamping, reduceSelfDistanceRate, gravity, "
                            "springForce, shockAbsorptionRate, windEffectCoef, "
                            "envWindEffectCoef, motionForce, colliderFilterInfoPath."
                        ),
                    },
                },
                "required": [],
            },
        }

    def run(self, client: BlenderClient, params: dict) -> str:
        targets: list[str] = params.get("targets") or []
        props: list[str] = params.get("properties") or list(self._KNOWN_PROPS)

        targets_json = json.dumps(targets)
        props_json = json.dumps(props)

        code = (
            f"import bpy, json\n"
            f"_targets = {targets_json}\n"
            f"_props = {props_json}\n"
            f"if _targets:\n"
            f"    _objs = [bpy.data.objects.get(n) for n in _targets]\n"
            f"    # Preserve original name even when object not found\n"
            f"    _names = _targets\n"
            f"else:\n"
            f"    _objs = [o for o in bpy.data.objects if o.get('TYPE') == 'RE_CHAIN_CHAINSETTINGS']\n"
            f"    _names = [o.name for o in _objs]\n"
            f"_results = []\n"
            f"for _name, _obj in zip(_names, _objs):\n"
            f"    if _obj is None:\n"
            f"        _results.append({{'name': _name, 'values': {{'error': 'not found'}}}})\n"
            f"        continue\n"
            f"    _s = getattr(_obj, 're_chain_chainsettings', None)\n"
            f"    if _s is None:\n"
            f"        _results.append({{'name': _name, 'values': {{'error': 'no re_chain_chainsettings'}}}})\n"
            f"        continue\n"
            f"    _vals = {{}}\n"
            f"    for _p in _props:\n"
            f"        _v = getattr(_s, _p, None)\n"
            f"        if _v is None:\n"
            f"            continue\n"
            f"        try:\n"
            f"            _vals[_p] = list(_v) if hasattr(_v, '__len__') and not isinstance(_v, str) else _v\n"
            f"        except Exception:\n"
            f"            _vals[_p] = str(_v)\n"
            f"    _results.append({{'name': _name, 'values': _vals}})\n"
            f"print({BLENDER_SENTINEL!r})\n"
            f"print(json.dumps({{'chain_settings': _results}}, ensure_ascii=False))\n"
        )

        try:
            lines = client.execute_and_extract(code)
            return lines[0] if lines else json.dumps({"error": "no output"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})


class ListMdfPresets(QueryTool):
    """
    List available MHWs MDF2 preset names from RE Mesh Editor's Presets/MHWILDS/ directory.

    Calls load_preset_enum_items("MHWILDS") via sys.modules scan (same pattern as
    MaterialGenerate's internal preset resolution). Returns display names (filenames
    without .json extension) — these are the exact strings expected by preset_mapping
    in material_generate.

    Returns an empty list if the RE Mesh Editor addon is not loaded in Blender.
    """

    @property
    def name(self) -> str:
        return "list_mdf_presets"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "list_mdf_presets",
            "description": (
                "List all available MHWs material preset names from RE Mesh Editor's "
                "Presets/MHWILDS/ directory. Each name is the .json filename without "
                "the extension — the exact string to use as a value in preset_mapping "
                "when calling material_generate. "
                "Call this before asking the user to pick a preset so you can present "
                "the actual available options rather than guessing. "
                "Returns an empty list if the RE Mesh Editor addon is not loaded in Blender."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        }

    def run(self, client: BlenderClient, params: dict) -> str:
        header = f"import sys, json\n_sentinel = {BLENDER_SENTINEL!r}\n"
        body = textwrap.dedent("""\
            _load_fn = None
            for _mod in sys.modules.values():
                try:
                    attr = getattr(_mod, "load_preset_enum_items", None)
                    if attr is not None and callable(attr):
                        _load_fn = attr
                        break
                except Exception:
                    continue
            if _load_fn is None:
                print(_sentinel)
                print(json.dumps({"error": "RE Mesh Editor addon not loaded", "presets": []}))
            else:
                try:
                    items = _load_fn("MHWILDS")
                    # items: [(path, display_name, path), ...]
                    names = [item[1] for item in items]
                    print(_sentinel)
                    print(json.dumps({"presets": names}))
                except Exception as exc:
                    print(_sentinel)
                    print(json.dumps({"error": str(exc), "presets": []}))
        """)
        code = header + body
        try:
            lines = client.execute_and_extract(code)
            return lines[0] if lines else json.dumps({"error": "no output", "presets": []})
        except Exception as exc:
            return json.dumps({"error": str(exc), "presets": []})
