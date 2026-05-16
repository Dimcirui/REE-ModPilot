"""
Modding-Toolkit X-preset catalog (issues #4 / #5 / #6 foundation).

Discovers and enumerates the X-presets shipped with (and supplemented by)
the Modding-Toolkit Blender addon. Each preset declares:

  preset_info  — name, type ("X_PRESET"), version, description
  exclude      — source-rig bone names the conversion phases should ignore
  mappings     — {standard_key: {"main": [candidate names...], "aux": [...]}}

Coverage of a preset against a source rig is defined as: of the slots in
the preset's `mappings`, how many have at least one `main` candidate present
in the source rig's bone list. This is the metric issue #4 uses to pick the
best-matching preset for an imported source model.

Public API:
  discover_preset_dir(client)        — locate the import/ folder via Blender
  enumerate_x_presets(preset_dir)    — load all X-preset JSON files
  compute_coverage(preset, bones)    — coverage report for one preset
  pick_best_preset(presets, bones)   — pick the highest-coverage preset
  fuzzy_match_bone(key, bones)       — string-similarity fallback (Waves 3-4)
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app.blender.client import BLENDER_SENTINEL, BlenderClient

# 13 X-presets shipped with Modding-Toolkit at the time of writing. Used as
# a fallback in app.phases.base.X_PRESETS when Blender isn't reachable at
# server startup, and as the default test fixture for unit tests that don't
# go through the lifespan handler.
#
# Note: the toolkit folder also contains 街霸6.json, but it declares
# `preset_info.type = "Y_PRESET"` (target-game preset) despite living in
# the import/ folder. enumerate_x_presets() correctly filters it out;
# we mirror that filter here.
SHIPPED_X_PRESETS: tuple[str, ...] = (
    "MMD",
    "VRChat",
    "Valve社",
    "怪猎世界",
    "怪猎崛起",
    "怪猎荒野",
    "生化危机4",
    "生化危机9",
    "碧蓝幻想",
    "终末地",
    "绝地潜兵2",
    "赛马娘",
    "鬼泣5",
)

# Candidate addon folder names. Modding-Toolkit installs under different
# names depending on whether the user grabbed the GitHub zip (suffix
# "-main") or installed via Blender's preferences UI. Probed in order.
_ADDON_DIR_CANDIDATES: tuple[str, ...] = (
    "Modding-Toolkit",
    "Modding-Toolkit-main",
    "Modder_Batch_Tool-main",
)


@dataclass
class PresetMeta:
    """One X-preset, loaded into memory.

    `mappings` is kept as the raw dict from JSON so callers can read both
    `main` and `aux` lists without us re-flattening.
    """

    name: str
    path: Path
    mappings: dict
    exclude: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class CoverageReport:
    """Result of computing one preset's coverage against a source rig."""

    preset_name: str
    coverage: float  # 0.0 .. 1.0
    covered_slots: dict[str, str]  # slot_key -> the matched candidate bone name
    uncovered_slots: list[str]  # slot_keys with no candidate present in source rig
    total_slots: int


def discover_preset_dir(client: BlenderClient, timeout: float = 5.0) -> Path:
    """Locate the toolkit's X-preset folder via blender-mcp.

    Walks `bpy.utils.user_resource('SCRIPTS')` / addons / <candidate>
    / assets / presets / import for each candidate addon folder name,
    returning the first one that exists.

    Raises FileNotFoundError if none of the candidates exist — the caller
    is responsible for falling back gracefully (e.g. shipping a fixed
    SHIPPED_X_PRESETS list at server boot).
    """
    candidates_py = ",\n        ".join(repr(name) for name in _ADDON_DIR_CANDIDATES)
    code = (
        "import bpy, os\n"
        f"print({BLENDER_SENTINEL!r})\n"
        "scripts = bpy.utils.user_resource('SCRIPTS')\n"
        "candidates = [\n"
        f"        {candidates_py},\n"
        "]\n"
        "found = ''\n"
        "for name in candidates:\n"
        "    p = os.path.join(scripts, 'addons', name, 'assets', 'presets', 'import')\n"
        "    if os.path.isdir(p):\n"
        "        found = p\n"
        "        break\n"
        "print(found or 'NOT_FOUND')\n"
    )
    lines = client.execute_and_extract(code, timeout=timeout)
    if not lines or lines[0] == "NOT_FOUND":
        raise FileNotFoundError(
            "Modding-Toolkit X-preset folder not found under any of "
            f"{list(_ADDON_DIR_CANDIDATES)} in Blender's user scripts dir."
        )
    return Path(lines[0])


def enumerate_x_presets(preset_dir: Path) -> dict[str, PresetMeta]:
    """Load every X-preset JSON in preset_dir into a {name: PresetMeta} dict.

    Files where `preset_info.type` is not "X_PRESET" are skipped. Corrupt
    JSON files are silently skipped so one bad file can't break server boot.
    """
    result: dict[str, PresetMeta] = {}
    if not preset_dir.is_dir():
        return result
    for p in sorted(preset_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        info = data.get("preset_info") or {}
        if not isinstance(info, dict) or info.get("type") != "X_PRESET":
            continue
        mappings = data.get("mappings")
        if not isinstance(mappings, dict) or not mappings:
            continue
        result[p.stem] = PresetMeta(
            name=p.stem,
            path=p,
            mappings=mappings,
            exclude=list(data.get("exclude") or []),
            description=str(info.get("description") or ""),
        )
    return result


def compute_coverage(preset: PresetMeta, source_bones: set[str]) -> CoverageReport:
    """Compute how well a preset matches the source rig's bone names.

    A slot counts as covered iff at least one of its `main` candidate names
    is in `source_bones` (exact, case-sensitive match — Blender's bone
    name lookup is case-sensitive and presets use exact names).
    """
    covered: dict[str, str] = {}
    uncovered: list[str] = []
    for slot_key, slot in preset.mappings.items():
        main_list = slot.get("main") if isinstance(slot, dict) else None
        if not isinstance(main_list, list):
            uncovered.append(slot_key)
            continue
        match = next((b for b in main_list if isinstance(b, str) and b in source_bones), None)
        if match is None:
            uncovered.append(slot_key)
        else:
            covered[slot_key] = match
    total = len(preset.mappings)
    coverage = (len(covered) / total) if total else 0.0
    return CoverageReport(
        preset_name=preset.name,
        coverage=coverage,
        covered_slots=covered,
        uncovered_slots=uncovered,
        total_slots=total,
    )


def pick_best_preset(
    presets: Iterable[PresetMeta], source_bones: set[str]
) -> tuple[CoverageReport | None, list[CoverageReport]]:
    """Pick the highest-coverage preset for a source rig.

    Returns (winner_report, all_reports_sorted_desc). winner_report is None
    when `presets` is empty. Ties are broken by preset name (alphabetical)
    purely so the result is deterministic; ties are rare in practice since
    different presets target structurally different rigs.
    """
    reports = [compute_coverage(p, source_bones) for p in presets]
    if not reports:
        return None, []
    reports.sort(key=lambda r: (-r.coverage, r.preset_name))
    return reports[0], reports


def fuzzy_match_bone(
    target_key: str,
    candidates: list[str],
    cutoff: float = 0.6,
) -> str | None:
    """Best-effort string-similarity match for a slot key against bone names.

    Used as a fallback in Waves 3 and 4 when the LLM returns an empty pick
    for a slot. Not used by issue #4's inference path itself — pure exact
    matching is the right metric there.
    """
    matches = difflib.get_close_matches(target_key, candidates, n=1, cutoff=cutoff)
    return matches[0] if matches else None
