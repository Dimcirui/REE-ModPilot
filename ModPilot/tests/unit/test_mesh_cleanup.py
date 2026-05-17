"""
Unit tests for `app/phases/mesh_cleanup.py` (Phase 5C).

`MeshCleanup` was previously only exercised indirectly through `BatchExport`
in `test_batch_export.py`. This module gives it direct coverage:

  - tool_schema shape
  - happy path: 1+ meshes cleaned, summary surfaced
  - error: collection missing → PhaseError precondition
  - error: malformed JSON output → PhaseError unexpected
  - error: empty output (no sentinel slice) → PhaseError operator_failed
  - per-mesh operator errors surface as `operator_warnings` in the diff
    but do NOT fail the phase (partial-failure tolerance)
  - generated Blender code contains the 4 RE Mesh ops + the built-in
    fallback path for `limit_total_normalize`

Run with: uv run pytest -m unit tests/unit/test_mesh_cleanup.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.phases.base import PhaseResult
from app.phases.mesh_cleanup import MeshCleanup


# ── fixtures ───────────────────────────────────────────────────────────────


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


# ── tool_schema ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_tool_schema_advertises_mesh_collection_param():
    schema = MeshCleanup.tool_schema()
    assert schema["name"] == "mesh_cleanup"
    props = schema["input_schema"]["properties"]
    assert "mesh_collection" in props
    # No required params — the default collection is used when unset.
    assert schema["input_schema"]["required"] == []


# ── happy path ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_run_success_with_default_collection():
    payload = json.dumps({
        "collection": "MHWilds_Female.mesh",
        "meshes_cleaned": 2,
        "results": [
            {"mesh": "Body", "errors": []},
            {"mesh": "Hair", "errors": []},
        ],
    })
    client = _make_client([payload])
    cache = _make_cache()
    result = MeshCleanup().run(client, cache, {})

    assert result.success is True
    diff = result.state_diff
    assert diff["collection"] == "MHWilds_Female.mesh"
    assert diff["meshes_cleaned"] == 2
    # No per-mesh errors → no warnings key in the diff.
    assert "operator_warnings" not in diff


@pytest.mark.unit
def test_run_success_with_custom_collection():
    """An explicit mesh_collection param overrides the default."""
    payload = json.dumps({
        "collection": "Other.mesh",
        "meshes_cleaned": 1,
        "results": [{"mesh": "Cube", "errors": []}],
    })
    client = _make_client([payload])
    cache = _make_cache()
    result = MeshCleanup().run(client, cache, {"mesh_collection": "Other.mesh"})

    assert result.success is True
    assert result.state_diff["collection"] == "Other.mesh"
    # The generated Blender code must reference the custom name.
    code = client.execute_and_extract.call_args[0][0]
    assert "'Other.mesh'" in code


@pytest.mark.unit
def test_per_mesh_errors_surface_as_warnings_without_failing_phase():
    """Partial failure tolerance: one op raising on one mesh keeps the phase
    successful but the diff carries the per-mesh error map."""
    payload = json.dumps({
        "collection": "MHWilds_Female.mesh",
        "meshes_cleaned": 3,
        "results": [
            {"mesh": "Body", "errors": []},
            {"mesh": "Hair", "errors": ["solve_repeated_uvs: stale UV"]},
            {"mesh": "Cloth", "errors": []},
        ],
    })
    client = _make_client([payload])
    cache = _make_cache()
    result = MeshCleanup().run(client, cache, {})

    assert result.success is True
    warnings = result.state_diff["operator_warnings"]
    assert "Hair" in warnings
    assert warnings["Hair"] == ["solve_repeated_uvs: stale UV"]
    # Bodies with no errors are NOT in the warnings map.
    assert "Body" not in warnings
    assert "Cloth" not in warnings


# ── error paths ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_collection_missing_returns_precondition_error():
    payload = json.dumps({"error": "Collection not found: Other.mesh"})
    client = _make_client([payload])
    cache = _make_cache()
    result = MeshCleanup().run(client, cache, {"mesh_collection": "Other.mesh"})

    assert result.success is False
    assert result.error.category == "precondition"
    assert "Other.mesh" in result.error.message
    # Suggestion should mention Phase 5B (the previous phase).
    assert "5B" in result.error.suggestion or "material_generate" in result.error.suggestion


@pytest.mark.unit
def test_empty_output_returns_operator_failed():
    """No lines after sentinel slice → operator hint."""
    client = _make_client([])
    cache = _make_cache()
    result = MeshCleanup().run(client, cache, {})

    assert result.success is False
    assert result.error.category == "operator_failed"


@pytest.mark.unit
def test_malformed_json_returns_unexpected_error():
    """Non-JSON line where JSON was expected."""
    client = _make_client(["not-json-not-{}"])
    cache = _make_cache()
    result = MeshCleanup().run(client, cache, {})

    assert result.success is False
    assert result.error.category == "unexpected"
    assert "parse" in result.error.message.lower()


# ── generated Blender code ─────────────────────────────────────────────────


@pytest.mark.unit
def test_generated_code_calls_all_four_operators_with_fallback():
    """The four RE Mesh ops are emitted in the expected order, and the
    `limit_total_normalize` op has a built-in fallback (issue: RE Mesh op
    can raise on dialog/poll problems)."""
    payload = json.dumps({
        "collection": "MHWilds_Female.mesh",
        "meshes_cleaned": 0,
        "results": [],
    })
    client = _make_client([payload])
    cache = _make_cache()
    MeshCleanup().run(client, cache, {})

    code = client.execute_and_extract.call_args[0][0]
    # All four RE Mesh ops are present.
    assert "bpy.ops.re_mesh.delete_loose()" in code
    assert "bpy.ops.re_mesh.solve_repeated_uvs()" in code
    assert "bpy.ops.re_mesh.remove_zero_weight_vertex_groups()" in code
    assert "bpy.ops.re_mesh.limit_total_normalize(maxWeights='12')" in code
    # Built-in fallback path for the last op.
    assert "bpy.ops.object.vertex_group_limit_total(limit=12)" in code
    assert "bpy.ops.object.vertex_group_normalize_all(lock_active=False)" in code


@pytest.mark.unit
def test_generated_code_uses_object_mode_and_deselect_first():
    """Operators require OBJECT mode and per-mesh active+select.  The code
    sets mode to OBJECT and deselects all before the loop."""
    payload = json.dumps({
        "collection": "MHWilds_Female.mesh",
        "meshes_cleaned": 0,
        "results": [],
    })
    client = _make_client([payload])
    cache = _make_cache()
    MeshCleanup().run(client, cache, {})

    code = client.execute_and_extract.call_args[0][0]
    assert "bpy.ops.object.mode_set(mode='OBJECT')" in code
    assert "bpy.ops.object.select_all(action='DESELECT')" in code


# ── exception bubbling ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_blender_error_during_exec_returns_unexpected():
    """A BlenderError raised by execute_and_extract should be caught and
    converted to PhaseResult.fail(unexpected) rather than escaping."""
    from app.blender.client import BlenderError

    client = MagicMock()
    client.execute_and_extract.side_effect = BlenderError("boom")
    cache = _make_cache()
    result = MeshCleanup().run(client, cache, {})

    assert result.success is False
    assert result.error.category == "unexpected"


@pytest.mark.unit
def test_socket_oserror_returns_timeout_category():
    """An OSError (e.g. socket disconnect mid-call) becomes a timeout error."""
    client = MagicMock()
    client.execute_and_extract.side_effect = OSError("connection reset")
    cache = _make_cache()
    result = MeshCleanup().run(client, cache, {})

    assert result.success is False
    assert result.error.category == "timeout"
