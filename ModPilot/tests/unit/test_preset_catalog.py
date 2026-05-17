"""
Unit tests for app/blender/preset_catalog.py (Wave 1 / issue #4 foundation).

Covers:
  - enumerate_x_presets: loads valid X-presets, skips non-X-PRESET / corrupt files
  - compute_coverage: counts covered vs uncovered slots, handles malformed
    mappings entries, returns the right percentage
  - pick_best_preset: returns the highest-coverage preset, ties broken
    deterministically by name
  - fuzzy_match_bone: returns the closest match or None below the cutoff
  - discover_preset_dir: success path + NOT_FOUND fallback via a stub
    BlenderClient that mimics execute_and_extract

Run with: uv run pytest -m unit tests/unit/test_preset_catalog.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.blender.preset_catalog import (
    SHIPPED_X_PRESETS,
    PresetMeta,
    compute_coverage,
    discover_preset_dir,
    enumerate_x_presets,
    fuzzy_match_bone,
    pick_best_preset,
)

# ── fixtures ──────────────────────────────────────────────────────────────


def _write_preset(folder: Path, name: str, body: dict) -> Path:
    p = folder / f"{name}.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def preset_dir(tmp_path: Path) -> Path:
    """A folder with three valid X-presets + two files that should be skipped."""
    # Valid: matches a full-MMD rig
    _write_preset(
        tmp_path,
        "MMD",
        {
            "preset_info": {"name": "MMD", "type": "X_PRESET"},
            "exclude": ["センター"],
            "mappings": {
                "pelvis": {"main": ["Hips", "下半身"], "aux": []},
                "spine_01": {"main": ["Spine", "上半身"], "aux": []},
                "head": {"main": ["Head", "頭"], "aux": []},
            },
        },
    )
    # Valid: VRChat-style
    _write_preset(
        tmp_path,
        "VRChat",
        {
            "preset_info": {"name": "VRChat", "type": "X_PRESET"},
            "mappings": {
                "pelvis": {"main": ["Hips"], "aux": []},
                "spine_01": {"main": ["Spine"], "aux": []},
                "head": {"main": ["Head"], "aux": []},
            },
        },
    )
    # Valid: half-baked preset with only one slot
    _write_preset(
        tmp_path,
        "OneSlot",
        {
            "preset_info": {"name": "OneSlot", "type": "X_PRESET"},
            "mappings": {"pelvis": {"main": ["root"], "aux": []}},
        },
    )
    # Skipped: wrong type
    _write_preset(
        tmp_path,
        "NotAnX",
        {
            "preset_info": {"name": "NotAnX", "type": "Y_PRESET"},
            "mappings": {"pelvis": {"main": ["Hips"], "aux": []}},
        },
    )
    # Skipped: corrupt JSON
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    return tmp_path


# ── enumerate_x_presets ───────────────────────────────────────────────────


@pytest.mark.unit
def test_enumerate_loads_only_x_presets(preset_dir: Path) -> None:
    catalog = enumerate_x_presets(preset_dir)
    assert set(catalog.keys()) == {"MMD", "VRChat", "OneSlot"}
    mmd = catalog["MMD"]
    assert isinstance(mmd, PresetMeta)
    assert mmd.name == "MMD"
    assert mmd.path.name == "MMD.json"
    assert mmd.exclude == ["センター"]
    assert "pelvis" in mmd.mappings


@pytest.mark.unit
def test_enumerate_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert enumerate_x_presets(tmp_path) == {}


@pytest.mark.unit
def test_enumerate_nonexistent_dir_returns_empty(tmp_path: Path) -> None:
    assert enumerate_x_presets(tmp_path / "does-not-exist") == {}


# ── compute_coverage ──────────────────────────────────────────────────────


def _preset(mappings: dict) -> PresetMeta:
    return PresetMeta(name="test", path=Path("/tmp/test.json"), mappings=mappings)


@pytest.mark.unit
def test_coverage_full_match() -> None:
    p = _preset(
        {
            "pelvis": {"main": ["Hips"], "aux": []},
            "head": {"main": ["Head"], "aux": []},
        }
    )
    r = compute_coverage(p, {"Hips", "Head", "Spine"})
    assert r.coverage == 1.0
    assert r.covered_slots == {"pelvis": "Hips", "head": "Head"}
    assert r.uncovered_slots == []
    assert r.total_slots == 2


@pytest.mark.unit
def test_coverage_partial() -> None:
    p = _preset(
        {
            "pelvis": {"main": ["Hips"], "aux": []},
            "head": {"main": ["Head"], "aux": []},
            "spine_01": {"main": ["Spine"], "aux": []},
            "neck": {"main": ["Neck"], "aux": []},
        }
    )
    r = compute_coverage(p, {"Hips", "Head"})
    assert r.coverage == 0.5
    assert set(r.covered_slots.keys()) == {"pelvis", "head"}
    assert set(r.uncovered_slots) == {"spine_01", "neck"}


@pytest.mark.unit
def test_coverage_zero_match() -> None:
    p = _preset({"pelvis": {"main": ["Hips"], "aux": []}})
    r = compute_coverage(p, {"root", "spine"})
    assert r.coverage == 0.0
    assert r.covered_slots == {}
    assert r.uncovered_slots == ["pelvis"]


@pytest.mark.unit
def test_coverage_multi_candidate_picks_first_match() -> None:
    """When `main` has multiple candidate names, the first present in the
    source rig wins. This matches MMD.json's English+Japanese pattern."""
    p = _preset({"pelvis": {"main": ["Hips", "下半身"], "aux": []}})
    r = compute_coverage(p, {"下半身"})
    assert r.covered_slots == {"pelvis": "下半身"}


@pytest.mark.unit
def test_coverage_malformed_slot_counts_as_uncovered() -> None:
    """A slot whose `main` key is missing or not a list should be treated
    as uncovered, not crash."""
    p = _preset(
        {
            "pelvis": {"main": ["Hips"], "aux": []},
            "broken_a": "not a dict",  # malformed
            "broken_b": {"aux": []},  # missing main
        }
    )
    r = compute_coverage(p, {"Hips"})
    assert r.coverage == pytest.approx(1 / 3)
    assert set(r.uncovered_slots) == {"broken_a", "broken_b"}


@pytest.mark.unit
def test_coverage_empty_mappings() -> None:
    p = _preset({})
    r = compute_coverage(p, {"Hips"})
    assert r.coverage == 0.0
    assert r.total_slots == 0


# ── pick_best_preset ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_pick_best_returns_highest_coverage() -> None:
    a = _preset({"a": {"main": ["X"]}})  # 0% vs {Y}
    b = _preset({"a": {"main": ["Y"]}})  # 100% vs {Y}
    winner, all_reports = pick_best_preset([a, b], {"Y"})
    assert winner.preset_name == "test"  # both named "test" via helper
    assert winner.coverage == 1.0
    assert len(all_reports) == 2
    assert all_reports[0].coverage == 1.0


@pytest.mark.unit
def test_pick_best_breaks_ties_by_name() -> None:
    a = PresetMeta(name="alpha", path=Path("a"), mappings={"x": {"main": ["X"]}})
    b = PresetMeta(name="beta", path=Path("b"), mappings={"x": {"main": ["X"]}})
    winner, _ = pick_best_preset([b, a], {"X"})
    assert winner.preset_name == "alpha"


@pytest.mark.unit
def test_pick_best_empty_returns_none() -> None:
    winner, reports = pick_best_preset([], {"Hips"})
    assert winner is None
    assert reports == []


# ── fuzzy_match_bone ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_fuzzy_match_close_name() -> None:
    out = fuzzy_match_bone("upperarm_L", ["UpperArm.L", "Hips", "Head"])
    assert out == "UpperArm.L"


@pytest.mark.unit
def test_fuzzy_match_no_match_below_cutoff() -> None:
    assert fuzzy_match_bone("upperarm_L", ["Hips", "Head"], cutoff=0.9) is None


# ── discover_preset_dir ───────────────────────────────────────────────────


@pytest.mark.unit
def test_discover_preset_dir_success() -> None:
    client = MagicMock()
    client.execute_and_extract.return_value = ["C:\\fake\\path\\import"]
    path = discover_preset_dir(client)
    assert path == Path("C:\\fake\\path\\import")
    # Verify the code uses the two-strategy approach: module scan primary,
    # script_paths fallback with candidate names.
    sent_code = client.execute_and_extract.call_args[0][0]
    assert "sys.modules" in sent_code
    assert "bpy.utils.script_paths" in sent_code
    assert "Modding-Toolkit" in sent_code


@pytest.mark.unit
def test_discover_preset_dir_not_found_raises() -> None:
    client = MagicMock()
    client.execute_and_extract.return_value = ["NOT_FOUND"]
    with pytest.raises(FileNotFoundError):
        discover_preset_dir(client)


# ── shipped list sanity ───────────────────────────────────────────────────


@pytest.mark.unit
def test_shipped_list_matches_toolkit_x_presets() -> None:
    """Pins the count so that adding presets to the constant is intentional
    rather than accidental — keeps the fallback in sync with reality.

    13 not 14: 街霸6.json lives in the import/ folder but declares
    type="Y_PRESET", so enumerate_x_presets correctly skips it. The shipped
    list mirrors that filter."""
    assert len(SHIPPED_X_PRESETS) == 13
    assert "MMD" in SHIPPED_X_PRESETS
    assert "VRChat" in SHIPPED_X_PRESETS
    assert "怪猎荒野" in SHIPPED_X_PRESETS
    assert "街霸6" not in SHIPPED_X_PRESETS
