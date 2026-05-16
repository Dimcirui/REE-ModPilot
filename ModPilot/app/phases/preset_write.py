"""
Preset write tools (issues #5 / #6).

Two phase tools, both pure file-writers — no Blender mutation:

  PresetSupplementWrite — issue #5: when InferModelType's matched preset has
    80-99% coverage, augment it by adding source-rig bone names to the
    `main` candidate lists of the uncovered slots. Saved as
    `<base>_extended.json` next to the shipped preset (the shipped file is
    NEVER overwritten). If `<base>_extended.json` already exists, new
    candidates are merged in (added to the .main list); existing values
    are preserved.

  PresetCustomWrite — issue #6: when coverage is <80% or the user clicks
    [Force Custom] on the unsupported error, build a brand-new X-preset
    from scratch with one slot per (slot_key, bone_name) entry the LLM
    provides. Saved as `<character_name>_custom.json`.

The LLM is the slot-mapping classifier (per the design A1 'LLM at
classification points' rule). It reads `uncovered_slots` + `rig_bones`
from InferModelType's state_diff, picks the best source-rig bone for each
slot, presents the draft to the user in chat, applies any corrections,
and finally calls one of these two tools with the confirmed mapping.

Neither tool calls the LLM internally — both are deterministic file
writers consistent with the PhaseTool base contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.blender.client import BlenderClient
from app.blender.preset_catalog import discover_preset_dir
from app.blender.state import SceneCache
from app.phases.base import PhaseError, PhaseResult, PhaseTool, add_x_preset

_VALID_NAME = re.compile(r"^[\w一-鿿\- ]+$")  # ASCII word / CJK / hyphen / space


def _safe_filename(stem: str) -> str:
    """Reject paths that could escape the preset folder or break the FS."""
    if not stem or stem in (".", "..") or "/" in stem or "\\" in stem:
        raise ValueError(f"Invalid preset filename stem {stem!r}")
    if not _VALID_NAME.match(stem):
        raise ValueError(
            f"Preset filename stem {stem!r} contains disallowed characters; "
            "use letters / digits / underscore / hyphen / space / CJK only."
        )
    return stem


def _pick_target_path(folder: Path, base_name: str, suffix: str) -> Path:
    """Return the path to write into, merging into an existing variant when
    possible. Result: <folder>/<base_name><suffix>.json the first time;
    subsequent writes update that same file (mappings are merged additively
    in PresetSupplementWrite, so a v2/v3 chain isn't needed)."""
    return folder / f"{_safe_filename(base_name)}{suffix}.json"


class PresetSupplementWrite(PhaseTool):
    """
    Issue #5: write `<base>_extended.json` with the user-confirmed mappings.

    Merges into the existing `<base>_extended.json` if present (adds bone
    names to .main lists, never removes). Never overwrites the shipped
    preset itself.
    """

    @property
    def name(self) -> str:
        return "setup_preset_supplement_write"

    @property
    def advances_phase(self) -> bool:
        # Sub-step within setup_infer/setup_import — file write only. The
        # phase index already advanced when InferModelType succeeded; the
        # next phase-advancing call is setup_import_mhwilds.
        return False

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "setup_preset_supplement_write",
            "description": (
                "Issue #5: augment a partially-matching X-preset with the "
                "user-confirmed slot→bone mappings for slots that the shipped "
                "preset didn't cover. Saves as <base_preset_name>_extended.json "
                "next to the shipped preset (never overwrites the original). "
                "Call this after InferModelType returned decision='supplement' "
                "and the user has confirmed your draft mappings. Required "
                "inputs come from InferModelType's state_diff: "
                "base_preset_name=inferred_preset, mappings={slot_key: "
                "bone_name} for each slot in uncovered_slots."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "base_preset_name": {
                        "type": "string",
                        "description": (
                            "Name of the matched shipped preset to augment "
                            "(e.g. 'MMD', '怪猎崛起'). Pass the "
                            "`inferred_preset` field from InferModelType's "
                            "state_diff verbatim."
                        ),
                    },
                    "mappings": {
                        "type": "object",
                        "description": (
                            "Object mapping {slot_key: bone_name}, one entry "
                            "per uncovered slot. slot_key matches a standard "
                            "slot in the base preset's `mappings` dict "
                            "(e.g. 'upperarm_L'); bone_name is the source-rig "
                            "bone the user confirmed as the best fit "
                            "(e.g. 'LeftArm.001'). Slots not present here "
                            "are left untouched."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["base_preset_name", "mappings"],
            },
        }

    def run(self, client: BlenderClient, cache: SceneCache, params: dict) -> PhaseResult:
        base_name = str(params.get("base_preset_name", "")).strip()
        mappings = params.get("mappings") or {}
        if not base_name:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message="Missing required param 'base_preset_name'.",
                )
            )
        if not isinstance(mappings, dict) or not mappings:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message="Param 'mappings' must be a non-empty {slot: bone} object.",
                )
            )
        # Reject mappings whose values aren't non-empty strings — common
        # LLM-shape mistake (None / list / number for a bone name).
        bad = [k for k, v in mappings.items() if not isinstance(v, str) or not v.strip()]
        if bad:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message=(
                        "Each mapping value must be a non-empty bone name "
                        f"string. Invalid slots: {bad}"
                    ),
                )
            )

        try:
            preset_dir = discover_preset_dir(client)
        except (FileNotFoundError, OSError) as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message="Could not locate the toolkit's preset folder.",
                    raw=str(exc),
                )
            )

        base_path = preset_dir / f"{base_name}.json"
        if not base_path.is_file():
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message=(
                        f"Base preset {base_name!r} not found at {base_path}. "
                        "Pass the exact name reported by setup_infer_model_type."
                    ),
                )
            )

        try:
            base_data = json.loads(base_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=self.name,
                    message=f"Could not read base preset {base_path}: {exc}",
                )
            )

        try:
            target = _pick_target_path(preset_dir, base_name, "_extended")
        except ValueError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message=str(exc),
                )
            )

        # Start from the existing _extended if present (additive merge),
        # else seed from the base preset so the new file is self-contained.
        if target.is_file():
            try:
                merged = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                merged = json.loads(json.dumps(base_data))  # deep-copy via roundtrip
        else:
            merged = json.loads(json.dumps(base_data))

        # Mark this preset as derived in preset_info so it's traceable.
        info = merged.setdefault("preset_info", {})
        info["type"] = "X_PRESET"
        info.setdefault("name", base_name)
        info["name"] = f"{info['name']}" if "_extended" in info.get("name", "") else f"{info.get('name', base_name)} (extended)"
        info["derived_from"] = base_name

        slot_dict = merged.setdefault("mappings", {})
        if not isinstance(slot_dict, dict):
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=self.name,
                    message="Base preset's `mappings` is not an object.",
                )
            )

        added_slots: list[str] = []
        added_candidates: list[tuple[str, str]] = []
        for slot_key, bone_name in mappings.items():
            slot = slot_dict.get(slot_key)
            if not isinstance(slot, dict):
                slot = {"main": [], "aux": []}
                slot_dict[slot_key] = slot
                added_slots.append(slot_key)
            main_list = slot.setdefault("main", [])
            aux_list = slot.setdefault("aux", [])
            if not isinstance(main_list, list):
                main_list = list(main_list) if main_list is not None else []
                slot[slot_key] = main_list
            if not isinstance(aux_list, list):
                aux_list = []
                slot["aux"] = aux_list
            if bone_name not in main_list:
                main_list.append(bone_name)
                added_candidates.append((slot_key, bone_name))

        try:
            target.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=self.name,
                    message=f"Could not write {target}: {exc}",
                )
            )

        new_name = target.stem
        add_x_preset(new_name)

        return PhaseResult.ok({
            "new_preset_name": new_name,
            "new_preset_path": str(target),
            "base_preset_name": base_name,
            "added_slots": added_slots,
            "added_candidates": [
                {"slot": s, "bone": b} for s, b in added_candidates
            ],
            "total_slot_count": len(slot_dict),
        })


class PresetCustomWrite(PhaseTool):
    """
    Issue #6: build a brand-new X-preset from the user-confirmed mappings.

    Used when InferModelType returned decision='custom' (1-79% coverage) or
    when the user clicks [Force Custom] on an unsupported-rig error.
    """

    @property
    def name(self) -> str:
        return "setup_preset_custom_write"

    @property
    def advances_phase(self) -> bool:
        return False  # sub-step; see PresetSupplementWrite.advances_phase

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "setup_preset_custom_write",
            "description": (
                "Issue #6: synthesize a new X-preset from the user-confirmed "
                "slot→bone mappings. Saves as <character_name>_custom.json in "
                "the toolkit's preset folder. Call this after InferModelType "
                "returned decision='custom' (or after the user picked [Force "
                "Custom] from the unsupported_rig error), and after the user "
                "has confirmed your full mapping draft."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "character_name": {
                        "type": "string",
                        "description": (
                            "Filename stem for the new preset. The session "
                            "config provides this (config.character_name); "
                            "pass it verbatim. Suffix '_custom' is appended "
                            "automatically."
                        ),
                    },
                    "mappings": {
                        "type": "object",
                        "description": (
                            "Object mapping {slot_key: bone_name} for every "
                            "standard slot the source rig provides. Use the "
                            "canonical slot keys (pelvis, spine_01, head, "
                            "upperarm_L, etc.) and the source rig's bone "
                            "names. Slots not present are simply absent from "
                            "the new preset (downstream phases will report "
                            "missing slots if any are critical)."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "Optional human-readable description for "
                            "preset_info.description. Defaults to a generated "
                            "string mentioning ModPilot + the character name."
                        ),
                    },
                },
                "required": ["character_name", "mappings"],
            },
        }

    def run(self, client: BlenderClient, cache: SceneCache, params: dict) -> PhaseResult:
        char_name = str(params.get("character_name", "")).strip()
        mappings = params.get("mappings") or {}
        desc = str(params.get("description", "")).strip() or (
            f"Custom X-preset synthesized by ModPilot for {char_name}."
        )

        if not char_name:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message="Missing required param 'character_name'.",
                )
            )
        if not isinstance(mappings, dict) or not mappings:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message="Param 'mappings' must be a non-empty {slot: bone} object.",
                )
            )
        bad = [k for k, v in mappings.items() if not isinstance(v, str) or not v.strip()]
        if bad:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message=(
                        "Each mapping value must be a non-empty bone name "
                        f"string. Invalid slots: {bad}"
                    ),
                )
            )

        try:
            preset_dir = discover_preset_dir(client)
        except (FileNotFoundError, OSError) as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message="Could not locate the toolkit's preset folder.",
                    raw=str(exc),
                )
            )

        try:
            target = _pick_target_path(preset_dir, char_name, "_custom")
        except ValueError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message=str(exc),
                )
            )

        new_preset = {
            "preset_info": {
                "name": f"{char_name} (custom)",
                "type": "X_PRESET",
                "version": "1.0",
                "description": desc,
                "derived_from": "custom",
            },
            "exclude": [],
            "mappings": {
                slot_key: {"main": [bone_name], "aux": []}
                for slot_key, bone_name in mappings.items()
            },
        }

        try:
            target.write_text(
                json.dumps(new_preset, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=self.name,
                    message=f"Could not write {target}: {exc}",
                )
            )

        new_name = target.stem
        add_x_preset(new_name)

        return PhaseResult.ok({
            "new_preset_name": new_name,
            "new_preset_path": str(target),
            "character_name": char_name,
            "slot_count": len(mappings),
        })
