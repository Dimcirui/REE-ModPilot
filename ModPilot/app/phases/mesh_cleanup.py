"""
Phase 5C — Mesh Cleanup (pre-export)

Runs four RE Mesh Editor cleanup operators on every MESH object in the
target collection before batch export.  Operates in-place; no new objects
are created.

Operators (applied per mesh, in this order):
  1. re_mesh.delete_loose                       — remove disconnected geometry
  2. re_mesh.solve_repeated_uvs                 — deduplicate identical UV coords
  3. re_mesh.remove_zero_weight_vertex_groups   — drop empty vertex groups
  4. re_mesh.limit_total_normalize(maxWeights='12')
       Falls back to the two built-in equivalents if the RE Mesh op raises
       RuntimeError (e.g. dialog/poll issue):
         bpy.ops.object.vertex_group_limit_total(limit=12)
         bpy.ops.object.vertex_group_normalize_all(lock_active=False)

Default target collection: "MHWilds_Female.mesh"
(After Phase 5B the collection contains Group_0_Sub_* submeshes.)
"""

from __future__ import annotations

import json
from typing import Any

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.state import SceneCache
from app.phases.base import PhaseError, PhaseResult, PhaseTool

_DEFAULT_COLLECTION = "MHWilds_Female.mesh"
_MAX_WEIGHTS = 12


class MeshCleanup(PhaseTool):
    """
    Phase 5C: Run RE Mesh cleanup operators on all meshes before export.

    Steps per mesh object in mesh_collection:
      1. delete_loose
      2. solve_repeated_uvs
      3. remove_zero_weight_vertex_groups
      4. limit_total_normalize (12 weights); falls back to built-ins on failure
    """

    @property
    def name(self) -> str:
        return "mesh_cleanup"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "mesh_cleanup",
            "description": (
                "Phase 5C: Run RE Mesh Tools cleanup on every mesh in the target "
                "collection before batch export. "
                "Applies in order: delete loose geometry, solve repeated UVs, "
                "remove empty vertex groups, limit+normalize weights to 12. "
                "Call after material_generate and before batch_export."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "mesh_collection": {
                        "type": "string",
                        "description": (
                            f"Collection containing the mesh objects to clean. "
                            f"Defaults to '{_DEFAULT_COLLECTION}'."
                        ),
                    },
                },
                "required": [],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        mesh_collection = params.get("mesh_collection", "") or _DEFAULT_COLLECTION

        state_before = cache.refresh()

        try:
            err, summary = self._run_cleanup(client, mesh_collection)
            if err is not None:
                return PhaseResult.fail(err)
        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator="re_mesh.*",
                    message="Blender error during mesh cleanup.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator="re_mesh.*",
                    message="Lost connection to Blender during mesh cleanup.",
                    raw=str(exc),
                )
            )

        state_after = cache.refresh()
        diff = state_before.diff(state_after)
        diff.update(summary)
        return PhaseResult.ok(diff)

    # ── private ───────────────────────────────────────────────────────────

    def _run_cleanup(
        self,
        client: BlenderClient,
        mesh_collection: str,
    ) -> tuple[PhaseError | None, dict]:
        code = (
            f"import bpy, json\n"
            f"_col_name = {mesh_collection!r}\n"
            f"_sentinel = {BLENDER_SENTINEL!r}\n"
            f"_col = bpy.data.collections.get(_col_name)\n"
            f"if _col is None:\n"
            f"    print(_sentinel)\n"
            f"    print(json.dumps({{'error': 'Collection not found: ' + _col_name}}))\n"
            f"else:\n"
            f"    if bpy.context.mode != 'OBJECT':\n"
            f"        bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    _meshes = [o for o in _col.objects if o.type == 'MESH']\n"
            f"    _results = []\n"
            f"    for _obj in _meshes:\n"
            f"        bpy.context.view_layer.objects.active = _obj\n"
            f"        _obj.select_set(True)\n"
            f"        _errs = []\n"
            # 1. delete_loose
            f"        try:\n"
            f"            bpy.ops.re_mesh.delete_loose()\n"
            f"        except Exception as _e:\n"
            f"            _errs.append('delete_loose: ' + str(_e))\n"
            # 2. solve_repeated_uvs
            f"        try:\n"
            f"            bpy.ops.re_mesh.solve_repeated_uvs()\n"
            f"        except Exception as _e:\n"
            f"            _errs.append('solve_repeated_uvs: ' + str(_e))\n"
            # 3. remove_zero_weight_vertex_groups
            f"        try:\n"
            f"            bpy.ops.re_mesh.remove_zero_weight_vertex_groups()\n"
            f"        except Exception as _e:\n"
            f"            _errs.append('remove_zero_weight_vertex_groups: ' + str(_e))\n"
            # 4. limit_total_normalize — prefer RE Mesh op; fall back to built-ins
            f"        try:\n"
            f"            bpy.ops.re_mesh.limit_total_normalize(maxWeights='12')\n"
            f"        except Exception:\n"
            f"            try:\n"
            f"                bpy.ops.object.vertex_group_limit_total(limit=12)\n"
            f"                bpy.ops.object.vertex_group_normalize_all(lock_active=False)\n"
            f"            except Exception as _e2:\n"
            f"                _errs.append('limit_normalize: ' + str(_e2))\n"
            f"        _obj.select_set(False)\n"
            f"        _results.append({{'mesh': _obj.name, 'errors': _errs}})\n"
            f"    print(_sentinel)\n"
            f"    print(json.dumps({{\n"
            f"        'collection': _col_name,\n"
            f"        'meshes_cleaned': len(_meshes),\n"
            f"        'results': _results,\n"
            f"    }}, ensure_ascii=False))\n"
        )

        lines = client.execute_and_extract(code)
        if not lines:
            return (
                PhaseError(
                    category="operator_failed",
                    operator="re_mesh.*",
                    message="Mesh cleanup returned no output from Blender.",
                ),
                {},
            )

        try:
            data = json.loads(lines[0])
        except json.JSONDecodeError:
            return (
                PhaseError(
                    category="unexpected",
                    operator="re_mesh.*",
                    message=f"Could not parse mesh cleanup result: {lines[0]!r}",
                ),
                {},
            )

        if "error" in data:
            return (
                PhaseError(
                    category="precondition",
                    operator="",
                    message=data["error"],
                    suggestion=(
                        f"Ensure '{mesh_collection}' exists and contains MESH objects. "
                        "Phase 5B (material_generate) must complete first."
                    ),
                ),
                {},
            )

        # Surface any per-mesh operator errors as a warning in the diff,
        # but do not fail the phase — partial failures are acceptable here.
        per_mesh_errors = {
            r["mesh"]: r["errors"]
            for r in data.get("results", [])
            if r.get("errors")
        }
        summary = {
            "collection": mesh_collection,
            "meshes_cleaned": data.get("meshes_cleaned", 0),
        }
        if per_mesh_errors:
            summary["operator_warnings"] = per_mesh_errors

        return None, summary
