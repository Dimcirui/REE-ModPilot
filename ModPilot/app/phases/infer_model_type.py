"""
Source-model type auto-inference (issue #4).

Runs between setup_validate_scene and setup_import_mhwilds. Reads the source
armature's bone list, walks every X-preset shipped with (or supplemented in)
Modding-Toolkit, and picks the preset with the highest bone-mapping coverage.

Decision branches:
  exact       — coverage == 1.0 → use the matched preset as-is.
  supplement  — 0.8 ≤ coverage < 1.0 → flow continues into issue #5
                (auto-supplement the partial preset's uncovered slots).
  custom      — 0 < coverage < 0.8 → flow continues into issue #6
                (synthesize a fresh preset for the source rig).
  unsupported — coverage == 0 → fail the phase; user gets retry/skip/force.

Wave 2 lands the inference itself plus the `model_type_inferred` event.
Waves 3 and 4 add the supplement / custom widget flows that consume the
non-exact decisions; until then the loop emits the event with the decision
tag and the LLM falls through to chat-level guidance.
"""

from __future__ import annotations

import json
from typing import Any

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.preset_catalog import (
    discover_preset_dir,
    enumerate_x_presets,
    pick_best_preset,
)
from app.blender.state import SceneCache
from app.phases.base import PhaseError, PhaseResult, PhaseTool

# Coverage thresholds from issue #4 + user-confirmed defaults
_THRESHOLD_EXACT = 1.0  # 100% → use directly
_THRESHOLD_SUPPLEMENT = 0.80  # 80–99% → supplement existing preset (issue #5)
# Anything in (0, 0.80) routes to "custom" → build new preset (issue #6)
# Anything == 0 is unsupported.


def _decide(coverage: float) -> str:
    if coverage >= _THRESHOLD_EXACT:
        return "exact"
    if coverage >= _THRESHOLD_SUPPLEMENT:
        return "supplement"
    if coverage > 0:
        return "custom"
    return "unsupported"


class InferModelType(PhaseTool):
    """
    Pick the best-matching X-preset for the imported source rig.

    Stateless and deterministic — no LLM call inside. The agent loop emits a
    `model_type_inferred` SSE event with the resulting decision so the UI can
    back-fill the session-config dropdown.
    """

    @property
    def name(self) -> str:
        return "setup_infer_model_type"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "setup_infer_model_type",
            "description": (
                "Setup step 2: detect the source model's type by comparing its "
                "armature bone names against every X-preset shipped with "
                "Modding-Toolkit. Returns the best-matching preset, its coverage "
                "ratio, and a decision tag (exact / supplement / custom / "
                "unsupported). Call this between setup_validate_scene and "
                "setup_import_mhwilds. The agent should report the decision "
                "to the user and only proceed to setup_import_mhwilds after "
                "user confirmation."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_armature": {
                        "type": "string",
                        "description": (
                            "Name of the source ARMATURE object (from "
                            "setup_validate_scene's state_diff)."
                        ),
                    },
                    "force_custom": {
                        "type": "boolean",
                        "description": (
                            "If true, bypass the coverage check and force the "
                            "'custom' decision (used by issue #6's [Force Custom] "
                            "error_choice button when no preset matches at all)."
                        ),
                    },
                },
                "required": ["source_armature"],
            },
        }

    def run(self, client: BlenderClient, cache: SceneCache, params: dict) -> PhaseResult:
        source_arm = params.get("source_armature", "")
        force_custom = bool(params.get("force_custom", False))
        if not source_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message="Missing required param 'source_armature'.",
                    suggestion=(
                        "Pass the source_armature value reported by "
                        "setup_validate_scene's state_diff."
                    ),
                )
            )

        # 1. Read source rig bone names
        bones, err = self._read_armature_bones(client, source_arm)
        if err is not None:
            return PhaseResult.fail(err)

        # 2. Locate the toolkit's X-preset folder
        try:
            preset_dir = discover_preset_dir(client)
        except (FileNotFoundError, BlenderError, OSError) as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message=(
                        "Could not locate the Modding-Toolkit X-preset folder. "
                        "Inference requires the toolkit addon to be installed."
                    ),
                    raw=str(exc),
                )
            )

        # 3. Enumerate + score
        catalog = enumerate_x_presets(preset_dir)
        if not catalog:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator=self.name,
                    message=(
                        "No X-presets found under "
                        f"{preset_dir} — toolkit install may be incomplete."
                    ),
                )
            )

        winner, all_reports = pick_best_preset(catalog.values(), set(bones))
        if winner is None:
            # Should be unreachable since `catalog` is non-empty, but guard anyway.
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=self.name,
                    message="pick_best_preset returned no winner on a non-empty catalog.",
                )
            )

        decision = "custom" if force_custom else _decide(winner.coverage)

        # 4. Build the result payload
        # Top-3 candidates carry name + coverage so the UI can offer alternates.
        top3 = [
            {"preset": r.preset_name, "coverage": round(r.coverage, 4)}
            for r in all_reports[:3]
        ]

        diff = {
            "inferred_preset": winner.preset_name,
            "coverage": round(winner.coverage, 4),
            "decision": decision,
            "covered_slots": winner.covered_slots,
            "uncovered_slots": winner.uncovered_slots,
            "optional_skipped_slots": winner.optional_skipped,
            "total_slots": winner.total_slots,
            "candidates": top3,
            "preset_path": str(catalog[winner.preset_name].path),
            "rig_bone_count": len(bones),
        }

        if decision == "unsupported":
            # 0-match path — the supplement/custom flows can't help here either,
            # since they also need *some* bones to map. Fail the phase; the
            # loop's error_choice widget will surface retry / skip / ask.
            return PhaseResult.fail(
                PhaseError(
                    category="unsupported_rig",
                    operator=self.name,
                    message=(
                        f"No X-preset matched any bone in {source_arm!r}. "
                        "The source rig may use an unsupported naming "
                        "convention."
                    ),
                    suggestion=(
                        "Verify the source armature has standard humanoid bone "
                        "names (Hips/Spine/Head or their MMD/VRChat/etc. "
                        "equivalents). If the rig uses custom names, the "
                        "[Force Custom] flow (issue #6) will let you build a "
                        "preset from scratch."
                    ),
                    raw=json.dumps(diff, ensure_ascii=False),
                )
            )

        return PhaseResult.ok(diff)

    # ── helpers ───────────────────────────────────────────────────────────

    def _read_armature_bones(
        self, client: BlenderClient, armature_name: str
    ) -> tuple[list[str], PhaseError | None]:
        """Return (bone_names, None) on success, ([], PhaseError) on failure.

        PhaseError is a dataclass, not an exception class, so we surface
        errors via a tuple rather than `raise`. Caller wraps PhaseError in
        PhaseResult.fail.
        """
        code = (
            "import bpy, json\n"
            f"_SEN = {BLENDER_SENTINEL!r}\n"
            f"_NAME = {armature_name!r}\n"
            "obj = bpy.data.objects.get(_NAME)\n"
            "if obj is None or obj.type != 'ARMATURE':\n"
            "    print(_SEN)\n"
            "    print(json.dumps({'error': 'NOT_ARMATURE'}))\n"
            "else:\n"
            "    print(_SEN)\n"
            "    print(json.dumps({'bones': [b.name for b in obj.data.bones]}))\n"
        )
        try:
            lines = client.execute_and_extract(code)
        except BlenderError as exc:
            return [], PhaseError(
                category="unexpected",
                operator=self.name,
                message="Blender error while reading source armature bones.",
                raw=str(exc),
            )
        except OSError as exc:
            return [], PhaseError(
                category="timeout",
                operator=self.name,
                message="Lost connection to Blender while reading source bones.",
                raw=str(exc),
            )
        if not lines:
            return [], PhaseError(
                category="unexpected",
                operator=self.name,
                message="Blender returned no output when reading source bones.",
            )
        try:
            payload = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            return [], PhaseError(
                category="unexpected",
                operator=self.name,
                message=f"Unparseable bone-list output: {lines[0]!r}",
                raw=str(exc),
            )
        if payload.get("error") == "NOT_ARMATURE":
            return [], PhaseError(
                category="precondition",
                operator=self.name,
                message=(
                    f"Object {armature_name!r} does not exist or is not an "
                    f"ARMATURE in the current scene."
                ),
            )
        return list(payload.get("bones", [])), None
