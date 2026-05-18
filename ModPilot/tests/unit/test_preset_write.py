"""
Unit tests for PresetSupplementWrite (issue #5) and PresetCustomWrite (issue #6).

Both tools are pure file writers — no Blender mutation, no LLM. Tests run
against an on-disk tmp preset folder; BlenderClient.execute_and_extract is
patched to return that folder via discover_preset_dir's contract.

Run with: uv run pytest -m unit tests/unit/test_preset_write.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.blender.state import SceneCache
from app.phases.base import X_PRESETS
from app.phases.preset_write import PresetCustomWrite, PresetSupplementWrite


def _build_client(preset_dir: Path | None) -> MagicMock:
    """Mock BlenderClient.execute_and_extract to return the preset dir path
    (or NOT_FOUND when preset_dir is None) to discover_preset_dir."""
    client = MagicMock()
    if preset_dir is None:
        client.execute_and_extract.return_value = ["NOT_FOUND"]
    else:
        client.execute_and_extract.return_value = [str(preset_dir)]
    return client


def _write_base(folder: Path, name: str, mappings: dict) -> Path:
    body = {
        "preset_info": {"name": name, "type": "X_PRESET", "version": "1.0"},
        "exclude": ["center_ik"],
        "mappings": mappings,
    }
    p = folder / f"{name}.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def cache() -> SceneCache:
    return MagicMock(spec=SceneCache)


# ── PresetSupplementWrite ─────────────────────────────────────────────────


@pytest.mark.unit
def test_supplement_writes_extended_alongside_base(tmp_path: Path, cache):
    _write_base(
        tmp_path,
        "MMD",
        {
            "pelvis": {"main": ["Hips", "下半身"], "aux": []},
            "spine_01": {"main": ["Spine", "上半身"], "aux": []},
        },
    )
    client = _build_client(tmp_path)
    params = {
        "base_preset_name": "MMD",
        "mappings": {
            "upperarm_L": "LeftUpperArm",  # new slot, not in base
            "pelvis": "MyHipsBone",  # existing slot, add to main candidates
        },
    }

    result = PresetSupplementWrite().run(client, cache, params)

    assert result.success
    diff = result.state_diff
    assert diff["new_preset_name"] == "MMD_extended"
    target = tmp_path / "MMD_extended.json"
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    # base is untouched on disk
    base_data = json.loads((tmp_path / "MMD.json").read_text(encoding="utf-8"))
    assert base_data["mappings"]["pelvis"]["main"] == ["Hips", "下半身"]
    # extended carries the merged data
    assert "upperarm_L" in data["mappings"]
    assert data["mappings"]["upperarm_L"]["main"] == ["LeftUpperArm"]
    # existing slot got the new candidate APPENDED, not replaced
    pelvis_main = data["mappings"]["pelvis"]["main"]
    assert "Hips" in pelvis_main and "下半身" in pelvis_main and "MyHipsBone" in pelvis_main
    # derived_from breadcrumb
    assert data["preset_info"]["derived_from"] == "MMD"
    # Registered into the runtime X_PRESETS set so validators see it
    assert "MMD_extended" in X_PRESETS


@pytest.mark.unit
def test_supplement_merges_into_existing_extended(tmp_path: Path, cache):
    """Second run on the same base should append to MMD_extended.json,
    not version it. New mappings stack additively; existing candidates stay."""
    _write_base(
        tmp_path,
        "MMD",
        {"pelvis": {"main": ["Hips"], "aux": []}},
    )
    client = _build_client(tmp_path)
    # First supplement adds bone A
    PresetSupplementWrite().run(
        client, cache,
        {"base_preset_name": "MMD", "mappings": {"upperarm_L": "BoneA"}},
    )
    # Second supplement adds bone B for same slot
    result = PresetSupplementWrite().run(
        client, cache,
        {"base_preset_name": "MMD", "mappings": {"upperarm_L": "BoneB"}},
    )
    assert result.success
    data = json.loads((tmp_path / "MMD_extended.json").read_text(encoding="utf-8"))
    assert data["mappings"]["upperarm_L"]["main"] == ["BoneA", "BoneB"]


@pytest.mark.unit
def test_supplement_dedups_existing_candidate(tmp_path: Path, cache):
    """If the bone is already in the slot's main list (whether from the base
    or from a prior _extended run), don't add it again."""
    _write_base(
        tmp_path,
        "MMD",
        {"pelvis": {"main": ["Hips"], "aux": []}},
    )
    client = _build_client(tmp_path)
    result = PresetSupplementWrite().run(
        client, cache,
        {"base_preset_name": "MMD", "mappings": {"pelvis": "Hips"}},
    )
    assert result.success
    data = json.loads((tmp_path / "MMD_extended.json").read_text(encoding="utf-8"))
    assert data["mappings"]["pelvis"]["main"] == ["Hips"]  # not duplicated


@pytest.mark.unit
def test_supplement_rejects_missing_base(tmp_path: Path, cache):
    client = _build_client(tmp_path)
    result = PresetSupplementWrite().run(
        client, cache,
        {"base_preset_name": "DoesNotExist", "mappings": {"pelvis": "Hips"}},
    )
    assert not result.success
    assert result.error.category == "precondition"
    assert "DoesNotExist" in result.error.message


@pytest.mark.unit
def test_supplement_rejects_empty_mappings(tmp_path: Path, cache):
    _write_base(tmp_path, "MMD", {"pelvis": {"main": ["Hips"], "aux": []}})
    client = _build_client(tmp_path)
    result = PresetSupplementWrite().run(
        client, cache,
        {"base_preset_name": "MMD", "mappings": {}},
    )
    assert not result.success
    assert result.error.category == "precondition"


@pytest.mark.unit
def test_supplement_rejects_non_string_bone_name(tmp_path: Path, cache):
    _write_base(tmp_path, "MMD", {"pelvis": {"main": ["Hips"], "aux": []}})
    client = _build_client(tmp_path)
    result = PresetSupplementWrite().run(
        client, cache,
        {"base_preset_name": "MMD", "mappings": {"upperarm_L": None}},
    )
    assert not result.success
    assert "non-empty bone name" in result.error.message


@pytest.mark.unit
def test_supplement_rejects_path_traversal_basename(tmp_path: Path, cache):
    client = _build_client(tmp_path)
    result = PresetSupplementWrite().run(
        client, cache,
        {"base_preset_name": "../etc/passwd", "mappings": {"x": "y"}},
    )
    assert not result.success
    assert result.error.category == "precondition"


@pytest.mark.unit
def test_supplement_fails_when_toolkit_not_installed(tmp_path: Path, cache):
    client = _build_client(preset_dir=None)
    result = PresetSupplementWrite().run(
        client, cache,
        {"base_preset_name": "MMD", "mappings": {"x": "y"}},
    )
    assert not result.success
    assert "preset folder" in result.error.message


@pytest.mark.unit
def test_supplement_does_not_advance_phase():
    assert PresetSupplementWrite().advances_phase is False


# ── PresetCustomWrite ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_custom_writes_new_preset(tmp_path: Path, cache):
    client = _build_client(tmp_path)
    params = {
        "character_name": "MyChar",
        "mappings": {
            "pelvis": "root",
            "spine_01": "spine",
            "head": "head_bone",
        },
        "description": "Test custom preset",
    }

    result = PresetCustomWrite().run(client, cache, params)

    assert result.success
    target = tmp_path / "MyChar_custom.json"
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["preset_info"]["type"] == "X_PRESET"
    assert data["preset_info"]["name"] == "MyChar (custom)"
    assert data["preset_info"]["description"] == "Test custom preset"
    assert data["preset_info"]["derived_from"] == "custom"
    assert data["mappings"]["pelvis"]["main"] == ["root"]
    assert data["mappings"]["spine_01"]["main"] == ["spine"]
    assert data["mappings"]["head"]["main"] == ["head_bone"]
    assert "MyChar_custom" in X_PRESETS


@pytest.mark.unit
def test_custom_default_description(tmp_path: Path, cache):
    """When description is omitted, generate a sensible one mentioning ModPilot."""
    client = _build_client(tmp_path)
    result = PresetCustomWrite().run(
        client, cache,
        {"character_name": "Nyx", "mappings": {"x": "y"}},
    )
    assert result.success
    data = json.loads((tmp_path / "Nyx_custom.json").read_text(encoding="utf-8"))
    assert "ModPilot" in data["preset_info"]["description"]
    assert "Nyx" in data["preset_info"]["description"]


@pytest.mark.unit
def test_custom_overwrites_existing_custom(tmp_path: Path, cache):
    """For #6, a fresh preset replaces any prior custom — the user re-ran
    the flow for a corrected mapping. We don't merge custom (unlike the
    supplement path) because the user is starting fresh by design."""
    client = _build_client(tmp_path)
    PresetCustomWrite().run(
        client, cache, {"character_name": "Foo", "mappings": {"x": "old"}},
    )
    result = PresetCustomWrite().run(
        client, cache, {"character_name": "Foo", "mappings": {"x": "new"}},
    )
    assert result.success
    data = json.loads((tmp_path / "Foo_custom.json").read_text(encoding="utf-8"))
    assert data["mappings"]["x"]["main"] == ["new"]  # not ["old", "new"]


@pytest.mark.unit
def test_custom_rejects_missing_character_name(tmp_path: Path, cache):
    client = _build_client(tmp_path)
    result = PresetCustomWrite().run(client, cache, {"mappings": {"x": "y"}})
    assert not result.success
    assert "character_name" in result.error.message


@pytest.mark.unit
def test_custom_rejects_empty_mappings(tmp_path: Path, cache):
    client = _build_client(tmp_path)
    result = PresetCustomWrite().run(
        client, cache, {"character_name": "Foo", "mappings": {}},
    )
    assert not result.success
    assert result.error.category == "precondition"


@pytest.mark.unit
def test_custom_does_not_advance_phase():
    assert PresetCustomWrite().advances_phase is False


# ── tool_schema sanity ────────────────────────────────────────────────────

# Note: the force-custom button (issue #6) is now rendered by the React
# ErrorChoice component, which keys off `event.category === "unsupported_rig"`.
# AgentLoop already passes `category` through the error_choice SSE payload —
# see test_post_failing_turn_emits_error_choice_in_queue in test_sse_routes.py
# for the queue-level evidence of that field.


@pytest.mark.unit
def test_schemas_required_params():
    sup = PresetSupplementWrite.tool_schema()
    assert sup["name"] == "setup_preset_supplement_write"
    assert set(sup["input_schema"]["required"]) == {"base_preset_name", "mappings"}

    cust = PresetCustomWrite.tool_schema()
    assert cust["name"] == "setup_preset_custom_write"
    assert set(cust["input_schema"]["required"]) == {"character_name", "mappings"}
