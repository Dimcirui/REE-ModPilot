"""
Unit tests for InferModelType (issue #4 / Wave 2).

Covers the four decision branches (exact / supplement / custom / unsupported),
the missing-armature precondition, the missing-toolkit precondition, and the
force_custom override path.

Phase tool tests use a MagicMock BlenderClient with patched
`execute_and_extract` so we can return both the bone-list JSON (the tool's
first call) and the discover_preset_dir output (the second call) without a
live Blender. The preset folder itself is a real on-disk tmp dir so the
catalog module's enumerate path is exercised end-to-end.

Run with: uv run pytest -m unit tests/unit/test_infer_model_type.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.blender.state import SceneCache
from app.phases.infer_model_type import InferModelType


def _write_preset(folder: Path, name: str, slots: dict[str, list[str]]) -> None:
    body = {
        "preset_info": {"name": name, "type": "X_PRESET"},
        "mappings": {key: {"main": names, "aux": []} for key, names in slots.items()},
    }
    (folder / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def preset_dir(tmp_path: Path) -> Path:
    """Four fixture presets sized to drive each decision branch when paired
    with the right rig:
      - FullMatch: 4 slots, all present in `bones_full`
      - PartialMatch: 5 slots, 4 of 5 present in `bones_full`     → 80% supplement
      - SmallMatch: 5 slots, 2 of 5 present in `bones_full`       → 40% custom
      - NoMatch:   3 slots, 0 present in `bones_full`             → 0% unsupported
    """
    _write_preset(tmp_path, "FullMatch", {
        "pelvis": ["Hips"], "spine": ["Spine"], "head": ["Head"], "neck": ["Neck"],
    })
    _write_preset(tmp_path, "PartialMatch", {
        "pelvis": ["Hips"], "spine": ["Spine"], "head": ["Head"], "neck": ["Neck"],
        "tail": ["TailRoot"],  # absent in rig
    })
    _write_preset(tmp_path, "SmallMatch", {
        "a": ["Hips"], "b": ["Spine"],
        "c": ["nope1"], "d": ["nope2"], "e": ["nope3"],
    })
    _write_preset(tmp_path, "NoMatch", {
        "x": ["xenobone1"], "y": ["xenobone2"], "z": ["xenobone3"],
    })
    return tmp_path


@pytest.fixture
def cache() -> SceneCache:
    """SceneCache stub — refresh is not exercised by InferModelType."""
    return MagicMock(spec=SceneCache)


def _build_client(bones: list[str], preset_dir: Path | str | None = None) -> MagicMock:
    """Build a BlenderClient mock that returns bones on call #1 and the
    preset_dir path on call #2 (matching the order InferModelType makes them).

    preset_dir=None simulates the 'toolkit not installed' error path.
    """
    client = MagicMock()
    bone_response = [json.dumps({"bones": bones})]
    if preset_dir is None:
        dir_response = ["NOT_FOUND"]
    else:
        dir_response = [str(preset_dir)]
    client.execute_and_extract.side_effect = [bone_response, dir_response]
    return client


# ── happy paths: one test per decision band ───────────────────────────────


@pytest.mark.unit
def test_decision_exact_100_percent(preset_dir: Path, cache: SceneCache) -> None:
    bones = ["Hips", "Spine", "Head", "Neck"]
    client = _build_client(bones, preset_dir)

    result = InferModelType().run(client, cache, {"source_armature": "MyRig"})

    assert result.success
    diff = result.state_diff
    assert diff["inferred_preset"] == "FullMatch"
    assert diff["coverage"] == 1.0
    assert diff["decision"] == "exact"
    assert diff["uncovered_slots"] == []
    # Top candidates list (≤3) sorted by coverage desc
    assert diff["candidates"][0]["preset"] == "FullMatch"
    assert diff["candidates"][0]["coverage"] == 1.0
    assert diff["rig_bone_count"] == 4


@pytest.mark.unit
def test_decision_supplement_80_percent(preset_dir: Path, cache: SceneCache) -> None:
    """4/5 slots of PartialMatch covered → 80% → supplement branch."""
    # Use exactly the bones for PartialMatch's first 4 slots; FullMatch only
    # has 4 slots which all match — that'd score 100% and win. So drop one
    # bone from FullMatch's set to make PartialMatch the winner at 80%.
    # FullMatch slots: Hips, Spine, Head, Neck → all 4 match → 100% (would win).
    # We need bones such that PartialMatch wins. Easiest: rename FullMatch
    # bones to break it. Bones we'll feed: include all of PartialMatch
    # except TailRoot, and ensure FullMatch *also* matches all 4 (so we get
    # a tie at coverage). pick_best_preset breaks ties by name alphabetically
    # → FullMatch wins. To force PartialMatch we'd need partial bones.
    #
    # Alternative simpler approach: drop FullMatch preset; use only the 5-slot
    # one. Repurpose this test as a focused coverage-band check.
    pdir = preset_dir
    (pdir / "FullMatch.json").unlink()  # remove the perfect-match preset
    (pdir / "SmallMatch.json").unlink()
    (pdir / "NoMatch.json").unlink()
    # Only PartialMatch remains. Bones cover 4/5.
    bones = ["Hips", "Spine", "Head", "Neck"]
    client = _build_client(bones, pdir)

    result = InferModelType().run(client, cache, {"source_armature": "MyRig"})

    assert result.success
    diff = result.state_diff
    assert diff["inferred_preset"] == "PartialMatch"
    assert diff["coverage"] == 0.8
    assert diff["decision"] == "supplement"
    assert "tail" in diff["uncovered_slots"]


@pytest.mark.unit
def test_decision_custom_40_percent(preset_dir: Path, cache: SceneCache) -> None:
    """2/5 slots of SmallMatch covered → 40% → custom branch."""
    pdir = preset_dir
    (pdir / "FullMatch.json").unlink()
    (pdir / "PartialMatch.json").unlink()
    (pdir / "NoMatch.json").unlink()
    bones = ["Hips", "Spine"]  # SmallMatch's a/b slots only
    client = _build_client(bones, pdir)

    result = InferModelType().run(client, cache, {"source_armature": "MyRig"})

    assert result.success
    assert result.state_diff["coverage"] == 0.4
    assert result.state_diff["decision"] == "custom"


@pytest.mark.unit
def test_decision_unsupported_zero_match(preset_dir: Path, cache: SceneCache) -> None:
    pdir = preset_dir
    (pdir / "FullMatch.json").unlink()
    (pdir / "PartialMatch.json").unlink()
    (pdir / "SmallMatch.json").unlink()
    bones = ["weird_bone_1", "weird_bone_2"]
    client = _build_client(bones, pdir)

    result = InferModelType().run(client, cache, {"source_armature": "MyRig"})

    assert not result.success
    assert result.error is not None
    assert result.error.category == "unsupported_rig"
    assert "MyRig" in result.error.message
    # Suggestion mentions [Force Custom] so the loop's error_choice can route
    # the user into the issue #6 path
    assert "Force Custom" in result.error.suggestion


# ── error paths ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_missing_source_armature_param(cache: SceneCache) -> None:
    client = MagicMock()
    result = InferModelType().run(client, cache, {})
    assert not result.success
    assert result.error.category == "precondition"
    assert "source_armature" in result.error.message
    # Should not have called Blender at all
    client.execute_and_extract.assert_not_called()


@pytest.mark.unit
def test_armature_not_found(preset_dir: Path, cache: SceneCache) -> None:
    """Blender reports the named armature doesn't exist."""
    client = MagicMock()
    client.execute_and_extract.return_value = [json.dumps({"error": "NOT_ARMATURE"})]
    result = InferModelType().run(client, cache, {"source_armature": "Bogus"})
    assert not result.success
    assert result.error.category == "precondition"
    assert "Bogus" in result.error.message


@pytest.mark.unit
def test_toolkit_not_installed(cache: SceneCache) -> None:
    """discover_preset_dir returns NOT_FOUND → wrapped in PhaseError."""
    bones = ["Hips", "Spine"]
    client = _build_client(bones, preset_dir=None)  # None → NOT_FOUND response
    result = InferModelType().run(client, cache, {"source_armature": "MyRig"})
    assert not result.success
    assert result.error.category == "precondition"
    assert "Modding-Toolkit" in result.error.message


@pytest.mark.unit
def test_empty_preset_folder(tmp_path: Path, cache: SceneCache) -> None:
    """Folder exists but contains no X-presets → fail with helpful message."""
    bones = ["Hips"]
    client = _build_client(bones, tmp_path)
    result = InferModelType().run(client, cache, {"source_armature": "MyRig"})
    assert not result.success
    assert result.error.category == "precondition"
    assert "No X-presets" in result.error.message


# ── force_custom override (issue #6 entry point) ──────────────────────────


@pytest.mark.unit
def test_force_custom_overrides_exact(preset_dir: Path, cache: SceneCache) -> None:
    """When the user clicks [Force Custom] from the error_choice widget,
    we re-run with force_custom=True and route to the custom path even on
    a 100%-coverage rig — letting the user manually map every slot."""
    bones = ["Hips", "Spine", "Head", "Neck"]
    client = _build_client(bones, preset_dir)
    result = InferModelType().run(
        client, cache, {"source_armature": "MyRig", "force_custom": True}
    )
    assert result.success
    # Coverage still reflects reality, but decision is forced
    assert result.state_diff["coverage"] == 1.0
    assert result.state_diff["decision"] == "custom"


# ── tool schema sanity ────────────────────────────────────────────────────


@pytest.mark.unit
def test_tool_schema_shape() -> None:
    schema = InferModelType.tool_schema()
    assert schema["name"] == "setup_infer_model_type"
    assert "description" in schema and len(schema["description"]) > 50
    assert schema["input_schema"]["required"] == ["source_armature"]
    assert "force_custom" in schema["input_schema"]["properties"]
