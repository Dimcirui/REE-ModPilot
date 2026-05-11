"""
Phase 5A — MaterialInspect  (scene + filesystem reader; no LLM calls)
Phase 5B — MaterialSetup    (Principled BSDF node wiring)
Phase 5C — MaterialGenerate (MDF2 generator pipeline)

Data flow:
  MaterialInspect  → returns materials list + texture_files + existing_connections
                    (agent loop runs LLM classification + user confirmation between tools)
  MaterialSetup    → receives confirmed {mat_name: {slot: filepath}} mapping, wires nodes
  MaterialGenerate → receives confirmed {mat_name: preset_display_name} mapping,
                     runs mdf_gen_refresh + mdf_gen_process

MMD models skip MaterialSetup — their textures are already wired by the MMD importer.
They enter Phase 5 at MaterialGenerate directly.

Normal slot handling is x_preset-dependent:
  VRChat  — OpenGL normals: ImageTexture → SepXYZ → Math(1-Y) → CombXYZ → NormalMap → Normal
  終末地  — DirectX normals (no inversion): ImageTexture → NormalMap → Normal

Generator preset paths are dynamic (RE Mesh Editor Presets/MHWILDS/*.json).
load_preset_enum_items("MHWILDS") is resolved at runtime by scanning sys.modules
for the RE Mesh Editor addon function; falls back to empty lookup on import failure.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.state import SceneCache
from app.phases.base import PhaseError, PhaseResult, PhaseTool

# ── constants ─────────────────────────────────────────────────────────────────

#: X presets that require MaterialSetup node wiring; MMD skips this tool
VALID_SETUP_PRESETS: frozenset[str] = frozenset({"VRChat", "終末地"})

IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".tga", ".dds", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"
})

PRINCIPLED_SLOTS: tuple[str, ...] = (
    "Base Color", "Alpha", "Roughness", "Metallic", "Emission", "Normal"
)

_OP_MDF_REFRESH = "mhws.mdf_gen_refresh"
_OP_MDF_PROCESS = "mhws.mdf_gen_process"

# Normal chain code fragments injected at Python time (20-space indent = inside if slot==Normal).
# Generated per-x_preset so the Blender code string itself is preset-specific and testable.
_I20 = " " * 20
_VCHAT_NORMAL_CHAIN: str = (
    f"{_I20}sep = nodes.new('ShaderNodeSeparateXYZ')\n"
    f"{_I20}sep.location = (-700, y)\n"
    f"{_I20}mth = nodes.new('ShaderNodeMath')\n"
    f"{_I20}mth.operation = 'SUBTRACT'\n"
    f"{_I20}mth.inputs[0].default_value = 1.0\n"
    f"{_I20}mth.location = (-500, y - 120)\n"
    f"{_I20}cmb = nodes.new('ShaderNodeCombineXYZ')\n"
    f"{_I20}cmb.location = (-350, y)\n"
    f"{_I20}links.new(tex.outputs['Color'], sep.inputs['Vector'])\n"
    f"{_I20}links.new(sep.outputs['X'], cmb.inputs['X'])\n"
    f"{_I20}links.new(sep.outputs['Y'], mth.inputs[1])\n"
    f"{_I20}links.new(mth.outputs[0], cmb.inputs['Y'])\n"
    f"{_I20}links.new(sep.outputs['Z'], cmb.inputs['Z'])\n"
    f"{_I20}links.new(cmb.outputs[0], nm.inputs['Color'])\n"
)
_ZENMO_NORMAL_CHAIN: str = (
    f"{_I20}links.new(tex.outputs['Color'], nm.inputs['Color'])\n"
)


# ── Phase 5pre ────────────────────────────────────────────────────────────────


class MaterialConsolidate(PhaseTool):
    """
    Phase 5pre: Deduplicate materials that share the same texture set.

    Many source models (especially VRChat community bases) split one logical
    material into many Blender materials (face.001/.002, body_a/body_b...) that
    all reference the same image files. Without consolidation, every downstream
    step (texture classification, node wiring, MDF2 generation) repeats the same
    work for each duplicate.

    Two modes:
      dry_run=True  (default): scan mesh, group materials by the set of image
                                filepaths referenced anywhere in their node tree,
                                return a proposal with keeper + to_remove for each
                                group. Does NOT mutate the scene.
      dry_run=False           : accept a confirmed `groups` list, reassign polygon
                                material_index, remove redundant slots.

    "Texture set" = sorted tuple of normalized absolute filepaths from every
    TEX_IMAGE node in a material's node tree (regardless of whether the node is
    connected to the Principled BSDF). Materials with no textures form their own
    singleton group (no merge).
    """

    @property
    def name(self) -> str:
        return "material_consolidate"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "material_consolidate",
            "description": (
                "Phase 5pre: Deduplicates Blender materials that share an identical "
                "texture set on a single mesh. "
                "INPUT: mesh with possibly many materials referencing the same image files. "
                "OUTPUT (dry_run=True): grouping proposal — list of {keeper, to_remove[]} "
                "per shared texture set; no scene mutation. "
                "OUTPUT (dry_run=False): scene mutated — for each group, polygons with "
                "material_index pointing to to_remove[] are reassigned to keeper, then "
                "redundant material slots are removed. "
                "Call this BEFORE material_inspect to shrink the material count the LLM "
                "must classify. VRChat community bases typically collapse from ~30 to ~6 materials."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_object": {
                        "type": "string",
                        "description": "Name of the MESH object in the Blender scene.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": (
                            "True: scan and return grouping proposal without mutating scene. "
                            "False: execute merges from the supplied `groups` list."
                        ),
                        "default": True,
                    },
                    "groups": {
                        "type": "array",
                        "description": (
                            "Required when dry_run=False. List of confirmed merges: "
                            "[{keeper: <material_name>, to_remove: [<material_name>, ...]}, ...]. "
                            "Each to_remove material's faces are reassigned to keeper, "
                            "then the slot is removed from the mesh."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "keeper": {"type": "string"},
                                "to_remove": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["keeper", "to_remove"],
                        },
                    },
                },
                "required": ["target_object"],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        target_object = params.get("target_object", "")
        dry_run = params.get("dry_run", True)
        groups: list = params.get("groups", [])

        if not target_object:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'target_object' is required.",
                )
            )
        if not dry_run and not groups:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'groups' is required when dry_run=False.",
                    suggestion="Run with dry_run=True first to obtain the grouping proposal.",
                )
            )

        state_before = cache.refresh()

        try:
            if dry_run:
                err, payload = self._scan_groups(client, target_object)
            else:
                err, payload = self._execute_merges(client, target_object, groups)
            if err is not None:
                return PhaseResult.fail(err)
        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator="",
                    message="Blender error during material consolidation.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator="",
                    message="Lost connection to Blender during material consolidation.",
                    raw=str(exc),
                )
            )

        state_after = cache.refresh()
        diff = state_before.diff(state_after)
        diff.update(payload)
        return PhaseResult.ok(diff)

    # ── private helpers ────────────────────────────────────────────────────

    def _scan_groups(
        self,
        client: BlenderClient,
        target_object: str,
    ) -> tuple[PhaseError | None, dict]:
        header = (
            f"import bpy, json, os\n"
            f"_target = {target_object!r}\n"
            f"_sentinel = {BLENDER_SENTINEL!r}\n"
        )
        body = textwrap.dedent("""\
            def _norm(p):
                if not p:
                    return ""
                try:
                    return os.path.normcase(os.path.normpath(bpy.path.abspath(p)))
                except Exception:
                    return p

            obj = bpy.data.objects.get(_target)
            if obj is None or obj.type != "MESH":
                print(_sentinel)
                print("PRECONDITION:object_not_found:" + _target)
            else:
                mat_textures = {}
                mat_basenames = {}
                for mat in obj.data.materials:
                    if mat is None:
                        continue
                    paths = set()
                    if mat.node_tree is not None:
                        for n in mat.node_tree.nodes:
                            if n.type == "TEX_IMAGE" and n.image is not None:
                                fp = _norm(n.image.filepath)
                                if fp:
                                    paths.add(fp)
                    key = tuple(sorted(paths))
                    mat_textures[mat.name] = key
                    mat_basenames[mat.name] = sorted({os.path.basename(p) for p in paths})

                groups_dict = {}
                for mat in obj.data.materials:
                    if mat is None or mat.name not in mat_textures:
                        continue
                    key = mat_textures[mat.name]
                    groups_dict.setdefault(key, []).append(mat.name)

                groups_out = []
                for key, members in groups_dict.items():
                    if not key:
                        for m in members:
                            groups_out.append({
                                "texture_set": [],
                                "materials": [m],
                                "keeper": m,
                                "to_remove": [],
                                "is_singleton": True,
                            })
                        continue
                    keeper = members[0]
                    to_remove = members[1:]
                    groups_out.append({
                        "texture_set": mat_basenames[keeper],
                        "materials": list(members),
                        "keeper": keeper,
                        "to_remove": to_remove,
                        "is_singleton": len(members) == 1,
                    })

                print(_sentinel)
                print("GROUPS:" + json.dumps({
                    "consolidation_groups": groups_out,
                    "material_count_before": len([m for m in obj.data.materials if m is not None]),
                }))
        """)
        code = header + body
        lines = client.execute_and_extract(code)
        return self._parse_scan_result(lines)

    @staticmethod
    def _parse_scan_result(lines: list[str]) -> tuple[PhaseError | None, dict]:
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator="",
                    message="Material consolidation scan returned no output from Blender.",
                ),
                {},
            )
        if lines[0].startswith("PRECONDITION:"):
            detail = lines[0][len("PRECONDITION:"):]
            return (
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Target object not found or not a mesh: {detail}",
                ),
                {},
            )
        if lines[0].startswith("GROUPS:"):
            try:
                return None, json.loads(lines[0][len("GROUPS:"):])
            except json.JSONDecodeError:
                return (
                    PhaseError(
                        category="unexpected",
                        operator="",
                        message="Could not parse consolidation scan JSON from Blender.",
                    ),
                    {},
                )
        return (
            PhaseError(
                category="unexpected",
                operator="",
                message=f"Unexpected output from consolidation scan: {lines[0]!r}",
            ),
            {},
        )

    def _execute_merges(
        self,
        client: BlenderClient,
        target_object: str,
        groups: list,
    ) -> tuple[PhaseError | None, dict]:
        groups_json = json.dumps(groups, ensure_ascii=False)
        header = (
            f"import bpy, json\n"
            f"_target = {target_object!r}\n"
            f"_groups = json.loads({groups_json!r})\n"
            f"_sentinel = {BLENDER_SENTINEL!r}\n"
        )
        body = textwrap.dedent("""\
            obj = bpy.data.objects.get(_target)
            if obj is None or obj.type != "MESH":
                print(_sentinel)
                print("PRECONDITION:object_not_found:" + _target)
            else:
                mesh = obj.data
                merged = []
                skipped = []
                count_before = len([m for m in mesh.materials if m is not None])

                for grp in _groups:
                    keeper = grp.get("keeper", "")
                    to_remove = list(grp.get("to_remove", []))
                    if not keeper or not to_remove:
                        continue

                    def _idx(name):
                        for i, m in enumerate(mesh.materials):
                            if m is not None and m.name == name:
                                return i
                        return -1

                    keeper_idx = _idx(keeper)
                    if keeper_idx < 0:
                        skipped.append(keeper + ":keeper_not_found")
                        continue

                    removed_here = []
                    for rm_name in to_remove:
                        rm_idx = _idx(rm_name)
                        if rm_idx < 0:
                            skipped.append(rm_name + ":not_found")
                            continue
                        if rm_idx == keeper_idx:
                            skipped.append(rm_name + ":same_as_keeper")
                            continue

                        for poly in mesh.polygons:
                            if poly.material_index == rm_idx:
                                poly.material_index = keeper_idx

                        mesh.materials.pop(index=rm_idx)

                        for poly in mesh.polygons:
                            if poly.material_index > rm_idx:
                                poly.material_index -= 1

                        if rm_idx < keeper_idx:
                            keeper_idx -= 1
                        removed_here.append(rm_name)

                    if removed_here:
                        merged.append({"keeper": keeper, "removed": removed_here})

                count_after = len([m for m in mesh.materials if m is not None])
                print(_sentinel)
                print("MERGED:" + json.dumps({
                    "merged_groups": merged,
                    "skipped": skipped,
                    "material_count_before": count_before,
                    "material_count_after": count_after,
                }))
        """)
        code = header + body
        lines = client.execute_and_extract(code)
        return self._parse_merge_result(lines)

    @staticmethod
    def _parse_merge_result(lines: list[str]) -> tuple[PhaseError | None, dict]:
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator="",
                    message="Material consolidation merge returned no output from Blender.",
                ),
                {},
            )
        if lines[0].startswith("PRECONDITION:"):
            detail = lines[0][len("PRECONDITION:"):]
            return (
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Target object not found or not a mesh: {detail}",
                ),
                {},
            )
        if lines[0].startswith("MERGED:"):
            try:
                return None, json.loads(lines[0][len("MERGED:"):])
            except json.JSONDecodeError:
                return (
                    PhaseError(
                        category="unexpected",
                        operator="",
                        message="Could not parse consolidation merge JSON from Blender.",
                    ),
                    {},
                )
        return (
            PhaseError(
                category="unexpected",
                operator="",
                message=f"Unexpected output from consolidation merge: {lines[0]!r}",
            ),
            {},
        )


# ── Phase 5A ──────────────────────────────────────────────────────────────────


class MaterialInspect(PhaseTool):
    """
    Phase 5A: Read-only inspector.

    Returns the data the agent loop needs to classify textures:
      - materials: list of material names on the target mesh
      - texture_files: list of absolute paths to image files in texture_dir
      - existing_connections: {mat_name: {slot: path_or_status}}
        slot status: absolute path string | "connected_no_image" | null

    The agent loop performs LLM filename classification + vision model fallback +
    user confirmation between this tool and MaterialSetup.
    """

    @property
    def name(self) -> str:
        return "material_inspect"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "material_inspect",
            "description": (
                "Phase 5A: Inspect a mesh object's materials and scan a texture directory. "
                "Returns material names, texture file paths, and existing Principled BSDF "
                "node connections. Use this before MaterialSetup to gather classification data."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_object": {
                        "type": "string",
                        "description": "Name of the MESH object in the Blender scene.",
                    },
                    "texture_dir": {
                        "type": "string",
                        "description": (
                            "Absolute path to the directory containing source texture files "
                            "(.png, .tga, .dds, .jpg, .tif, .bmp). All files in this "
                            "directory are included regardless of subdirectory."
                        ),
                    },
                },
                "required": ["target_object", "texture_dir"],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        target_object = params.get("target_object", "")
        texture_dir = params.get("texture_dir", "")

        if not target_object:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'target_object' is required.",
                )
            )
        if not texture_dir:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'texture_dir' is required.",
                )
            )

        tex_files = self._scan_texture_dir(texture_dir)
        if tex_files is None:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"texture_dir not found or is not a directory: {texture_dir!r}",
                    suggestion="Provide an absolute path to the folder containing texture files.",
                )
            )

        state_before = cache.refresh()

        try:
            err, materials, connections = self._inspect_scene(client, target_object)
            if err is not None:
                return PhaseResult.fail(err)
        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator="",
                    message="Blender error during material inspection.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator="",
                    message="Lost connection to Blender during material inspection.",
                    raw=str(exc),
                )
            )

        state_after = cache.refresh()
        diff = state_before.diff(state_after)
        diff["materials"] = materials
        diff["texture_files"] = tex_files
        diff["existing_connections"] = connections
        return PhaseResult.ok(diff)

    # ── private helpers ────────────────────────────────────────────────────

    @staticmethod
    def _scan_texture_dir(texture_dir: str) -> list[str] | None:
        p = Path(texture_dir)
        if not p.is_dir():
            return None
        return sorted(
            str(f) for f in p.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        )

    def _inspect_scene(
        self,
        client: BlenderClient,
        target_object: str,
    ) -> tuple[PhaseError | None, list[str], dict]:
        header = (
            f"import bpy, json\n"
            f"_target = {target_object!r}\n"
            f"_sentinel = {BLENDER_SENTINEL!r}\n"
        )
        body = textwrap.dedent("""\
            def _trace_to_image(node, depth=0):
                if depth > 6:
                    return "connected_no_image"
                if node.type == "TEX_IMAGE":
                    return bpy.path.abspath(node.image.filepath) if node.image else "connected_no_image"
                for inp in node.inputs:
                    if inp.is_linked:
                        r = _trace_to_image(inp.links[0].from_node, depth + 1)
                        if r:
                            return r
                return "connected_no_image"

            obj = bpy.data.objects.get(_target)
            if obj is None or obj.type != "MESH":
                print(_sentinel)
                print("PRECONDITION:object_not_found:" + _target)
            else:
                SLOTS = ("Base Color", "Alpha", "Roughness", "Metallic", "Emission", "Normal")
                materials = [m.name for m in obj.data.materials if m is not None]
                connections = {}
                for mat in obj.data.materials:
                    if mat is None:
                        continue
                    slot_state = {s: None for s in SLOTS}
                    if mat.node_tree is not None:
                        pbs = next(
                            (n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"),
                            None,
                        )
                        if pbs is not None:
                            for slot in SLOTS:
                                inp = pbs.inputs.get(slot)
                                if inp is not None and inp.is_linked:
                                    slot_state[slot] = _trace_to_image(inp.links[0].from_node)
                    connections[mat.name] = slot_state
                print(_sentinel)
                print("INSPECT:" + json.dumps({"materials": materials, "connections": connections}))
        """)
        code = header + body
        lines = client.execute_and_extract(code)
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator="",
                    message="Material inspection returned no output from Blender.",
                ),
                [],
                {},
            )
        if lines[0].startswith("PRECONDITION:"):
            detail = lines[0][len("PRECONDITION:"):]
            return (
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Target object not found or not a mesh: {detail}",
                    suggestion="Ensure the mesh object name is correct and exists in the scene.",
                ),
                [],
                {},
            )
        if lines[0].startswith("INSPECT:"):
            try:
                data = json.loads(lines[0][len("INSPECT:"):])
                return None, data.get("materials", []), data.get("connections", {})
            except json.JSONDecodeError:
                return (
                    PhaseError(
                        category="unexpected",
                        operator="",
                        message="Could not parse material inspection JSON from Blender.",
                    ),
                    [],
                    {},
                )
        return (
            PhaseError(
                category="unexpected",
                operator="",
                message=f"Unexpected output from material inspection: {lines[0]!r}",
            ),
            [],
            {},
        )


# ── Phase 5B ──────────────────────────────────────────────────────────────────


class MaterialSetup(PhaseTool):
    """
    Phase 5B: Wire confirmed texture mapping into Principled BSDF nodes.

    Receives the agent-loop-confirmed texture_mapping and executes node
    wiring for all materials. Normal slot wiring is x_preset-dependent:
      VRChat  — inserts Y-inversion chain (1-Y on green channel)
      終末地  — direct connection via NormalMap node

    MMD models skip this tool entirely.
    """

    @property
    def name(self) -> str:
        return "material_setup"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "material_setup",
            "description": (
                "Phase 5B: Wires texture image files into Principled BSDF (PBS) nodes. "
                "THIS TOOL IS FOR PRINCIPLED BSDF MATERIALS ONLY. "
                "Do NOT call this for Emission or MMDShaderDev materials — those shader types "
                "have their own relevant sockets (Emission: Color only; MMDShaderDev: Base Tex / "
                "Base Alpha) and are never wired via this tool. "
                "Call this only when material_inspect reports output_shader=BSDF and "
                "existing_connections shows null (blank) slots. "
                "LLM texture classification (filename rules + user confirmation) runs before "
                "calling this tool. "
                "Normal slot wiring is x_preset-dependent: VRChat (OpenGL normals) inserts a "
                "1-Y inversion chain; 終末地 (DirectX) connects directly. "
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_object": {
                        "type": "string",
                        "description": "Name of the MESH object in the Blender scene.",
                    },
                    "x_preset": {
                        "type": "string",
                        "enum": ["VRChat", "終末地"],
                        "description": (
                            "Source model preset. Controls Normal slot wiring: "
                            "VRChat=OpenGL (Y-inversion inserted), 終末地=DirectX (direct)."
                        ),
                    },
                    "texture_mapping": {
                        "type": "object",
                        "description": (
                            "Confirmed mapping: {mat_name: {slot: filepath_or_null}}. "
                            "Slot keys: 'Base Color', 'Alpha', 'Roughness', 'Metallic', "
                            "'Emission', 'Normal'. A null value skips that slot."
                        ),
                    },
                },
                "required": ["target_object", "x_preset", "texture_mapping"],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        target_object = params.get("target_object", "")
        x_preset = params.get("x_preset", "")
        texture_mapping: dict = params.get("texture_mapping", {})

        if not target_object:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'target_object' is required.",
                )
            )
        if x_preset not in VALID_SETUP_PRESETS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=(
                        f"'x_preset' must be one of {sorted(VALID_SETUP_PRESETS)}, "
                        f"got {x_preset!r}. MMD models skip MaterialSetup."
                    ),
                )
            )
        if not texture_mapping:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'texture_mapping' must be a non-empty dict.",
                    suggestion="Run material_inspect first, then confirm the mapping with the user.",
                )
            )

        state_before = cache.refresh()

        try:
            err, wired, skipped = self._wire_all_materials(
                client, target_object, x_preset, texture_mapping
            )
            if err is not None:
                return PhaseResult.fail(err)
        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator="",
                    message="Blender error during material node wiring.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator="",
                    message="Lost connection to Blender during material setup.",
                    raw=str(exc),
                )
            )

        state_after = cache.refresh()
        diff = state_before.diff(state_after)
        diff["materials_wired"] = wired
        if skipped:
            diff["slots_skipped"] = skipped
        return PhaseResult.ok(diff)

    # ── private helpers ────────────────────────────────────────────────────

    def _wire_all_materials(
        self,
        client: BlenderClient,
        target_object: str,
        x_preset: str,
        texture_mapping: dict,
    ) -> tuple[PhaseError | None, list[str], list[str]]:
        mapping_json = json.dumps(texture_mapping, ensure_ascii=False)
        normal_chain = _VCHAT_NORMAL_CHAIN if x_preset == "VRChat" else _ZENMO_NORMAL_CHAIN
        header = (
            f"import bpy, json\n"
            f"_target = {target_object!r}\n"
            f"_mapping = json.loads({mapping_json!r})\n"
            f"_sentinel = {BLENDER_SENTINEL!r}\n"
        )
        # __NC__ is at 32 spaces in the source string; after dedent removes the
        # 12-space common indent it lands at 20 spaces, matching the indent level
        # inside `if slot == _NORMAL:`. It is replaced with the x_preset-specific
        # normal chain fragment built above.
        body = textwrap.dedent("""\
            obj = bpy.data.objects.get(_target)
            if obj is None or obj.type != "MESH":
                print(_sentinel)
                print("PRECONDITION:object_not_found:" + _target)
            else:
                _NORMAL = "Normal"
                _wired = []
                _skipped = []
                for mat_name, slot_map in _mapping.items():
                    mat = bpy.data.materials.get(mat_name)
                    if mat is None:
                        _skipped.append(mat_name + ".(material_not_found)")
                        continue
                    if not mat.use_nodes:
                        mat.use_nodes = True
                    nodes = mat.node_tree.nodes
                    links = mat.node_tree.links
                    pbs = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
                    if pbs is None:
                        pbs = nodes.new("ShaderNodeBsdfPrincipled")
                        pbs.location = (0, 0)
                    y = 300
                    for slot, fp in slot_map.items():
                        if not fp:
                            continue
                        target_inp = pbs.inputs.get(slot)
                        if target_inp is None:
                            _skipped.append(mat_name + "." + slot + ".(unknown_slot)")
                            continue
                        try:
                            if slot == _NORMAL:
                                tex = nodes.new("ShaderNodeTexImage")
                                tex.image = bpy.data.images.load(fp, check_existing=True)
                                tex.location = (-900, y)
                                nm = nodes.new("ShaderNodeNormalMap")
                                nm.location = (-150, y)
                                __NC__
                                links.new(nm.outputs[0], target_inp)
                            else:
                                tex = nodes.new("ShaderNodeTexImage")
                                tex.image = bpy.data.images.load(fp, check_existing=True)
                                tex.location = (-500, y)
                                links.new(tex.outputs["Color"], target_inp)
                            _wired.append(mat_name + "." + slot)
                        except Exception as e:
                            _skipped.append(mat_name + "." + slot + ":" + str(e))
                        y -= 300
                print(_sentinel)
                print("WIRED:" + json.dumps({"wired": _wired, "skipped": _skipped}))
        """).replace("                    __NC__\n", normal_chain)
        code = header + body
        lines = client.execute_and_extract(code)
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator="",
                    message="Material node wiring returned no output from Blender.",
                ),
                [],
                [],
            )
        if lines[0].startswith("PRECONDITION:"):
            detail = lines[0][len("PRECONDITION:"):]
            return (
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Target object not found or not a mesh: {detail}",
                ),
                [],
                [],
            )
        if lines[0].startswith("WIRED:"):
            try:
                data = json.loads(lines[0][len("WIRED:"):])
                return None, data.get("wired", []), data.get("skipped", [])
            except json.JSONDecodeError:
                return (
                    PhaseError(
                        category="unexpected",
                        operator="",
                        message="Could not parse wiring result JSON from Blender.",
                    ),
                    [],
                    [],
                )
        return (
            PhaseError(
                category="unexpected",
                operator="",
                message=f"Unexpected output from material setup: {lines[0]!r}",
            ),
            [],
            [],
        )


# ── Phase 5C ──────────────────────────────────────────────────────────────────


class MaterialGenerate(PhaseTool):
    """
    Phase 5C: Configure and run the MDF2 generator.

    Steps:
      1. Set scene properties (natives_root, mesh_collection, texture_base_path).
      2. Call mdf_gen_refresh() — auto-detects node strategy + guesses presets.
      3. Resolve load_preset_enum_items("MHWILDS") to a {display_name: full_path} lookup.
      4. Apply agent-confirmed preset_mapping to material_list entries.
      5. Call mdf_gen_process().

    Returns mdf_collection name for use as mdf2_collection in Phase 6 (BatchExport).
    Materials not in preset_mapping are left with the auto-guessed preset from refresh
    and recorded in state_diff["presets_auto_guessed"].
    """

    @property
    def name(self) -> str:
        return "material_generate"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "material_generate",
            "description": (
                "Phase 5C: Reads each material's existing Blender shader node tree and produces "
                "MHWs-native .mdf2 material files and .tex texture files. No shader conversion "
                "or reformatting occurs — the generator reads whatever is already connected to "
                "Material Output. Call this once all material input sockets are wired "
                "(or confirmed already wired for Emission/MMD materials). "
                "OUTPUT: a new MDF2 collection of RE_MDF objects + .mdf2 / .tex files written "
                "under natives/STM/Art/<texture_base_path>/. The MDF2 collection name is "
                "returned for use as `mdf2_collection` in batch_export. "
                "\n"
                "Supported output shader types (auto-detected): Principled BSDF (each socket "
                "analyzed individually), Emission (Color socket read as emissive channel; toon "
                "mode auto-enabled), MMDShaderDev GROUP (Base Tex / Base Alpha sockets). "
                "For each connected socket the generator assigns: DIRECT if an Image Texture "
                "node is connected (path copied as-is), SOLID if a constant value is set "
                "(256×256 solid .tex generated), BAKE if a node chain drives the input "
                "(Cycles renders the result). "
                "PRESET CONCEPT: a preset is an MHWs game-side rendering material definition "
                "(a .json file in RE Mesh Editor's Presets/MHWILDS/ directory). It has NO "
                "relationship to Blender materials or shader types — it only tells the game "
                "engine how to render the mesh in-game. Each Blender material slot maps to "
                "exactly ONE preset (a plain string, never a list or combination). There is "
                "no mixing, stacking, or combining of presets for a single material slot."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "mesh_collection": {
                        "type": "string",
                        "description": (
                            "Blender collection name containing the mesh objects. "
                            "REQUIRED: call list_collections() first and use the exact name "
                            "returned — do not guess. This is the central collection created "
                            "in Phase 3, typically named 'MHWilds_Female.mesh'."
                        ),
                    },
                    "texture_base_path": {
                        "type": "string",
                        "description": (
                            "Sub-path under natives/STM/Art/ for texture output "
                            "(e.g. 'Author/CharName/'). User-specified; not derived from armor JSON."
                        ),
                    },
                    "preset_mapping": {
                        "type": "object",
                        "description": (
                            "Confirmed {mat_name: preset_display_name} map. "
                            "Display names are filenames without .json from "
                            "RE Mesh Editor's Presets/MHWILDS/ directory "
                            "(e.g. 'Hair', 'Skin', 'Character', 'cloth')."
                        ),
                    },
                    "mdf_collection_name": {
                        "type": "string",
                        "description": "Output MDF collection name. Generator auto-derives if omitted.",
                    },
                    "natives_root": {
                        "type": "string",
                        "description": (
                            "Absolute path to the user's mod root folder. "
                            "REQUIRED. Pass any folder path the user specifies — the toolkit "
                            "automatically creates the 'natives/' directory structure inside it "
                            "if it does not exist. Do not assume a 'natives/' subfolder must "
                            "already be present."
                        ),
                    },
                },
                "required": ["mesh_collection", "natives_root", "texture_base_path", "preset_mapping"],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        mesh_collection = params.get("mesh_collection", "")
        texture_base_path = params.get("texture_base_path", "")
        preset_mapping: dict = params.get("preset_mapping", {})
        mdf_collection_name = params.get("mdf_collection_name", "")
        natives_root = params.get("natives_root", "")

        if not mesh_collection:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'mesh_collection' is required.",
                )
            )
        if not natives_root:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=(
                        "'natives_root' is required. Ask the user for their mod root folder path "
                        "(any folder; the toolkit auto-creates the natives/ structure inside it)."
                    ),
                )
            )
        if not texture_base_path:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'texture_base_path' is required (e.g. 'Author/CharName/').",
                )
            )
        if preset_mapping is None:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="'preset_mapping' is required (may be empty dict if all presets are auto-guessed).",
                )
            )

        state_before = cache.refresh()

        try:
            err, result = self._run_generator(
                client,
                mesh_collection=mesh_collection,
                texture_base_path=texture_base_path,
                preset_mapping=preset_mapping,
                mdf_collection_name=mdf_collection_name,
                natives_root=natives_root,
            )
            if err is not None:
                return PhaseResult.fail(err)
        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=_OP_MDF_PROCESS,
                    message="Blender error during MDF2 generation.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator=_OP_MDF_PROCESS,
                    message="Lost connection to Blender during MDF2 generation.",
                    raw=str(exc),
                )
            )

        state_after = cache.refresh()
        diff = state_before.diff(state_after)
        diff.update(result)
        return PhaseResult.ok(diff)

    # ── private helpers ────────────────────────────────────────────────────

    def _run_generator(
        self,
        client: BlenderClient,
        *,
        mesh_collection: str,
        texture_base_path: str,
        preset_mapping: dict,
        mdf_collection_name: str,
        natives_root: str,
    ) -> tuple[PhaseError | None, dict]:
        mapping_json = json.dumps(preset_mapping, ensure_ascii=False)
        header = (
            f"import bpy, json, sys, io, contextlib, base64, traceback\n"
            f"_col_name = {mesh_collection!r}\n"
            f"_tex_base = {texture_base_path!r}\n"
            f"_mdf_name = {mdf_collection_name!r}\n"
            f"_natives = {natives_root!r}\n"
            f"_mapping = json.loads({mapping_json!r})\n"
            f"_sentinel = {BLENDER_SENTINEL!r}\n"
        )
        body = textwrap.dedent("""\
            # Top-level guard: any setup-phase exception (RE Mesh Editor missing,
            # property group not registered, mdf_gen_refresh failure, etc.) must
            # still reach the sentinel so the Python side gets a real traceback.
            _buf = io.StringIO()
            _exc_tb = ""
            _status = ""
            _result_data = None
            with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(_buf):
                try:
                    scene = bpy.context.scene
                    col = bpy.data.collections.get(_col_name)
                    if col is None:
                        _status = "PRECONDITION:collection_not_found:" + _col_name
                    else:
                        s = scene.mhws_mdf_generator
                        if _natives:
                            scene["mhws_natives_root"] = _natives
                        s.mesh_collection = col
                        s.texture_base_path = _tex_base
                        if _mdf_name:
                            s.mdf_collection_name = _mdf_name

                        # Pre-bake render config: Cycles + GPU + low samples.
                        # Without this Blender falls back to session defaults
                        # (often Eevee or Cycles+CPU+high samples), making bake
                        # an order of magnitude slower than necessary.
                        scene.render.engine = "CYCLES"
                        try:
                            scene.cycles.device = "GPU"
                            scene.cycles.samples = 4
                            scene.cycles.preview_samples = 4
                        except AttributeError:
                            # cycles addon not enabled — Cycles engine assignment
                            # above would have raised first; safe fallthrough.
                            pass

                        bpy.ops.mhws.mdf_gen_refresh()

                        _load_fn = None
                        for _mod in sys.modules.values():
                            try:
                                attr = getattr(_mod, "load_preset_enum_items", None)
                                if attr is not None and callable(attr):
                                    _load_fn = attr
                                    break
                            except Exception:
                                continue
                        preset_lookup = {}
                        if _load_fn is not None:
                            try:
                                for item in _load_fn("MHWILDS"):
                                    preset_lookup[item[1]] = item[0]
                            except Exception:
                                pass

                        auto_guessed = []
                        for entry in s.material_list:
                            mat_name = entry.blender_material or ""
                            dn = _mapping.get(mat_name)
                            if dn and dn in preset_lookup:
                                entry.material_preset = preset_lookup[dn]
                            else:
                                auto_guessed.append(mat_name)

                        ret = bpy.ops.mhws.mdf_gen_process()
                        processed = [e.blender_material for e in s.material_list if e.blender_material]
                        mdf_col = s.mdf_collection_name
                        if "FINISHED" not in str(ret):
                            _status = "CANCELLED:" + str(ret)
                        else:
                            _result_data = {
                                "mdf_collection": mdf_col,
                                "materials_processed": processed,
                                "presets_auto_guessed": auto_guessed,
                            }
                            _status = "RESULT:" + json.dumps(_result_data)
                except Exception:
                    _exc_tb = traceback.format_exc()

            _captured = _buf.getvalue()
            print(_sentinel)
            if _exc_tb:
                # Last non-blank traceback line is usually the exception summary.
                _tb_lines = [ln for ln in _exc_tb.splitlines() if ln.strip()]
                print("EXCEPTION:" + (_tb_lines[-1] if _tb_lines else "unknown"))
            else:
                print(_status)
            _payload = (_exc_tb + "\\n" + _captured) if _exc_tb else _captured
            print("STDERR_B64:" + base64.b64encode(_payload.encode("utf-8", "replace")).decode("ascii"))
        """)
        code = header + body
        # Bake can take several minutes on large meshes — far longer than the
        # 30s default socket timeout. Use a 10-minute window.
        lines = client.execute_and_extract(code, timeout=600)
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator=_OP_MDF_PROCESS,
                    message="MDF2 generator returned no output from Blender.",
                ),
                {},
            )

        # Pull out the captured stdout/stderr line (any position; usually last).
        import base64 as _b64
        captured = ""
        status_lines: list[str] = []
        for ln in lines:
            if ln.startswith("STDERR_B64:"):
                try:
                    captured = _b64.b64decode(ln[len("STDERR_B64:"):]).decode("utf-8", "replace")
                except Exception:
                    captured = ln[len("STDERR_B64:"):]
            else:
                status_lines.append(ln)

        status = status_lines[0] if status_lines else ""
        # Trim captured to keep PhaseError.raw reasonable in size.
        if len(captured) > 4000:
            captured = captured[-4000:]

        if status.startswith("PRECONDITION:"):
            detail = status[len("PRECONDITION:"):]
            return (
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Required scene object not found: {detail}",
                    suggestion="Ensure the mesh collection exists and contains mesh objects.",
                    raw=captured,
                ),
                {},
            )
        if status.startswith("EXCEPTION:"):
            exc_msg = status[len("EXCEPTION:"):]
            return (
                PhaseError(
                    category="unexpected",
                    operator=_OP_MDF_PROCESS,
                    message=f"MDF2 generator raised an exception: {exc_msg}",
                    suggestion=(
                        "Check that RE Mesh Editor is installed and enabled in Blender, "
                        "and that the mesh collection contains valid RE MESH objects."
                    ),
                    raw=captured,
                ),
                {},
            )
        if status.startswith("CANCELLED:"):
            return (
                PhaseError(
                    category="operator_failed",
                    operator=_OP_MDF_PROCESS,
                    message=f"MDF2 generator did not finish: {status}",
                    raw=captured,
                ),
                {},
            )
        if status.startswith("RESULT:"):
            try:
                data = json.loads(status[len("RESULT:"):])
                return None, data
            except json.JSONDecodeError:
                return (
                    PhaseError(
                        category="unexpected",
                        operator=_OP_MDF_PROCESS,
                        message="Could not parse MDF2 generator result JSON from Blender.",
                        raw=captured,
                    ),
                    {},
                )
        return (
            PhaseError(
                category="unexpected",
                operator=_OP_MDF_PROCESS,
                message=f"Unexpected output from MDF2 generator: {status!r}",
                raw=captured,
            ),
            {},
        )
