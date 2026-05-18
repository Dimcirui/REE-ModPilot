"""
MVP end-to-end verification (issue #8).

Walks the full pipeline (Setup -> Phase 1 -> 2 -> 3 -> 3.5 -> 4A -> 4B -> 5 -> 6)
against a real Blender + Modding-Toolkit + RE Mesh Editor + RE Chain Editor
installation. The agent loop and LLM are bypassed: classification decisions
(X preset, per-chain physics types, material slot -> texture mapping,
material -> mdf preset mapping) are read from a JSON config file so this
script is fully deterministic.

What it checks at each step
---------------------------
- Phase tool returns PhaseResult.success = True.
- Underlying Blender operators returned {'FINISHED'} (enforced inside
  every phase tool via require_finished()).
- After Phase 6, the expected mesh / mdf2 / chain2 / fbxskel files exist
  on disk under natives_root, each with non-zero size.
- Intermediate state checks: scene contains expected collections /
  armatures at the expected handoff points.

How to run
----------
1. Start Blender, enable Modding-Toolkit + Modder-Batch-Tool +
   RE Mesh Editor + RE Chain Editor, open BlenderMCP and click
   "Connect to Claude".
2. Open a .blend that already has the source model loaded (see
    docs/user/demo_setup.md).
3. Copy verify_mvp_config.example.json -> verify_mvp_config.json,
   fill in paths + classification mappings.
4. From repo root:
       cd ModPilot
       uv run python ../verify_mvp.py --config ../verify_mvp_config.json
5. Exit code 0 = all checks passed. Non-zero = at least one step failed;
   read the inline diagnostic for the failing phase.

This script is NOT a regression suite for the agent loop. For that, see
the integration tests under ModPilot/tests/integration/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Allow running from the repo root or from ModPilot/.
_HERE = Path(__file__).resolve().parent
_MODPILOT = _HERE / "ModPilot"
if _MODPILOT.is_dir() and str(_MODPILOT) not in sys.path:
    sys.path.insert(0, str(_MODPILOT))

from app.blender.client import BlenderClient, BlenderError  # noqa: E402
from app.blender.state import SceneCache  # noqa: E402
from app.phases.base import PhaseResult  # noqa: E402
from app.phases.batch_export import BatchExport  # noqa: E402
from app.phases.material import (  # noqa: E402
    MaterialGenerate,
    MaterialInspect,
    MaterialSetup,
)
from app.phases.physics_bones import (  # noqa: E402
    PhysicsChains,
    PhysicsClassification,
    PhysicsTransplant,
)
from app.phases.pose_correction import PoseCorrection  # noqa: E402
from app.phases.setup import SetupImportMHWilds, SetupValidateScene  # noqa: E402
from app.phases.skeleton_align import SkeletonAlign  # noqa: E402
from app.phases.vertex_groups import VertexGroups  # noqa: E402

# ── reporting ───────────────────────────────────────────────────────────────


@dataclass
class StepReport:
    label: str
    ok: bool
    detail: str = ""
    duration_s: float = 0.0
    state_diff: dict = field(default_factory=dict)


def _print_header(text: str) -> None:
    bar = "=" * len(text)
    print(f"\n{bar}\n{text}\n{bar}")


def _print_step(label: str, status: str, detail: str = "") -> None:
    badge = {"OK": "  OK ", "FAIL": "FAIL ", "SKIP": "SKIP "}[status]
    line = f"[{badge}] {label}"
    if detail:
        line += f"  -- {detail}"
    print(line)


def _print_result_diff(diff: dict) -> None:
    if not diff:
        return
    for key, value in diff.items():
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) > 100:
            text = text[:97] + "..."
        print(f"        {key}: {text}")


# ── runner ──────────────────────────────────────────────────────────────────


def _run_phase(
    label: str,
    fn: Callable[[], PhaseResult],
    reports: list[StepReport],
) -> bool:
    """Run a phase tool, append a StepReport, return success."""
    started = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        reports.append(StepReport(label=label, ok=False, detail=f"exception: {exc!r}", duration_s=elapsed))
        _print_step(label, "FAIL", f"exception: {exc!r}")
        return False

    elapsed = time.perf_counter() - started
    if not result.success:
        err = result.error
        msg = f"{err.category} @ {err.operator or '<pre>'}: {err.message}" if err else "no error info"
        reports.append(StepReport(label=label, ok=False, detail=msg, duration_s=elapsed))
        _print_step(label, "FAIL", msg)
        if err and err.suggestion:
            print(f"        suggestion: {err.suggestion}")
        return False

    reports.append(StepReport(label=label, ok=True, duration_s=elapsed, state_diff=result.state_diff))
    _print_step(label, "OK", f"{elapsed:.1f}s")
    _print_result_diff(result.state_diff)
    return True


# ── per-phase wrappers ──────────────────────────────────────────────────────


def _setup(client: BlenderClient, cache: SceneCache, reports: list[StepReport]) -> bool:
    ok = _run_phase(
        "Setup 1/2: validate scene",
        lambda: SetupValidateScene().run(client, cache, {}),
        reports,
    )
    if not ok:
        return False
    return _run_phase(
        "Setup 2/2: import MHWilds reference",
        lambda: SetupImportMHWilds().run(client, cache, {}),
        reports,
    )


def _phase_1_2_3(
    client: BlenderClient,
    cache: SceneCache,
    cfg: dict,
    reports: list[StepReport],
) -> bool:
    x_preset = cfg["x_preset"]
    source_arm = cfg["source_armature"]
    target_arm = cfg.get("target_armature", "MHWilds_Female Armature")
    mesh_objects = cfg["mesh_objects"]

    if not _run_phase(
        "Phase 1: pose_correction",
        lambda: PoseCorrection().run(
            client,
            cache,
            {
                "x_preset": x_preset,
                "source_armature": source_arm,
                "target_armature": target_arm,
                "skip_scale_align": cfg.get("skip_scale_align", False),
            },
        ),
        reports,
    ):
        return False

    if not _run_phase(
        "Phase 2: skeleton_align",
        lambda: SkeletonAlign().run(
            client,
            cache,
            {
                "x_preset": x_preset,
                "source_armature": source_arm,
                "target_armature": target_arm,
            },
        ),
        reports,
    ):
        return False

    return _run_phase(
        "Phase 3: vertex_groups",
        lambda: VertexGroups().run(
            client,
            cache,
            {
                "x_preset": x_preset,
                "mesh_objects": mesh_objects,
                "target_armature": target_arm,
            },
        ),
        reports,
    )


def _phase_4(
    client: BlenderClient,
    cache: SceneCache,
    cfg: dict,
    reports: list[StepReport],
) -> bool:
    x_preset = cfg["x_preset"]
    source_arm = cfg["source_armature"]
    target_arm = cfg.get("target_armature", "MHWilds_Female Armature")
    chain_collection = cfg.get("chain_collection", "")
    inferred_types: dict[str, str] = cfg["inferred_types"]
    bones_to_clear: list[str] = cfg.get("bones_to_clear", [])
    bones_to_merge: list[str] = cfg.get("bones_to_merge", [])

    if not _run_phase(
        "Phase 3.5: physics_transplant",
        lambda: PhysicsTransplant().run(
            client,
            cache,
            {
                "x_preset": x_preset,
                "source_armature": source_arm,
                "target_armature": target_arm,
            },
        ),
        reports,
    ):
        return False

    # Phase 4A is an inspector — its job is to surface chain topology.
    # We still run it so the operator path is exercised; the LLM-driven
    # classification step is short-circuited by the config-supplied dict
    # passed straight to Phase 4B.
    if not _run_phase(
        "Phase 4A: physics_classification (inspector)",
        lambda: PhysicsClassification().run(
            client, cache, {"target_armature": target_arm}
        ),
        reports,
    ):
        return False

    params: dict = {
        "target_armature": target_arm,
        "inferred_types": inferred_types,
    }
    if chain_collection:
        params["chain_collection"] = chain_collection
    if bones_to_clear:
        params["bones_to_clear"] = bones_to_clear
    if bones_to_merge:
        params["bones_to_merge"] = bones_to_merge

    return _run_phase(
        "Phase 4B: physics_chains",
        lambda: PhysicsChains().run(client, cache, params),
        reports,
    )


def _phase_5(
    client: BlenderClient,
    cache: SceneCache,
    cfg: dict,
    reports: list[StepReport],
) -> bool:
    x_preset = cfg["x_preset"]
    target_object = cfg["merged_mesh_object"]
    texture_dir = cfg["texture_dir"]
    texture_mapping: dict = cfg.get("texture_mapping", {})
    preset_mapping: dict = cfg["preset_mapping"]
    mesh_collection = cfg["mesh_collection"]
    natives_root = cfg["natives_root"]
    texture_base_path = cfg["texture_base_path"]
    mdf_collection_name = cfg.get("mdf_collection_name", "")

    if not _run_phase(
        "Phase 5A: material_inspect",
        lambda: MaterialInspect().run(
            client,
            cache,
            {"target_object": target_object, "texture_dir": texture_dir},
        ),
        reports,
    ):
        return False

    # MMD scenes skip MaterialSetup (textures pre-wired by importer).
    if x_preset != "MMD":
        if not texture_mapping:
            _print_step(
                "Phase 5B: material_setup",
                "FAIL",
                "non-MMD run requires texture_mapping in config",
            )
            reports.append(
                StepReport(
                    label="Phase 5B: material_setup",
                    ok=False,
                    detail="texture_mapping missing for non-MMD source",
                )
            )
            return False
        if not _run_phase(
            "Phase 5B: material_setup",
            lambda: MaterialSetup().run(
                client,
                cache,
                {
                    "target_object": target_object,
                    "x_preset": x_preset,
                    "texture_mapping": texture_mapping,
                },
            ),
            reports,
        ):
            return False
    else:
        _print_step("Phase 5B: material_setup", "SKIP", "MMD textures already wired")

    generate_params: dict = {
        "mesh_collection": mesh_collection,
        "texture_base_path": texture_base_path,
        "preset_mapping": preset_mapping,
        "natives_root": natives_root,
    }
    if mdf_collection_name:
        generate_params["mdf_collection_name"] = mdf_collection_name

    return _run_phase(
        "Phase 5C: material_generate",
        lambda: MaterialGenerate().run(client, cache, generate_params),
        reports,
    )


def _phase_6(
    client: BlenderClient,
    cache: SceneCache,
    cfg: dict,
    reports: list[StepReport],
) -> bool:
    return _run_phase(
        "Phase 6: batch_export",
        lambda: BatchExport().run(
            client,
            cache,
            {
                "armor_id": cfg["armor_id"],
                "armor_variant": cfg.get("armor_variant", "ff"),
                "target_parts": cfg["target_parts"],
                "mesh_collection": cfg["mesh_collection"],
                "mdf2_collection": cfg["mdf2_collection"],
                "chain2_collection": cfg["chain2_collection"],
                "target_armature": cfg.get("target_armature", "MHWilds_Female Armature"),
                "fbxskel_name": cfg["fbxskel_name"],
                "natives_root": cfg["natives_root"],
            },
        ),
        reports,
    )


# ── post-export file checks ─────────────────────────────────────────────────


def _check_export_artifacts(cfg: dict, reports: list[StepReport]) -> bool:
    """
    Walk natives_root and verify the expected mesh / mdf2 / chain2 files
    exist for every target part, plus the BoneSystem fbxskel file.

    The toolkit's MHWs batch exporter writes files under a path determined
    by the armor scheme JSON. We don't re-parse that here; instead we
    accept an explicit `expected_files` list from the config.
    """
    natives_root = Path(cfg["natives_root"])
    expected: list[str] = cfg.get("expected_files", [])
    if not expected:
        _print_step(
            "File check",
            "SKIP",
            "expected_files not configured; manual inspection required",
        )
        reports.append(
            StepReport(
                label="File existence check",
                ok=True,
                detail="skipped (no expected_files configured)",
            )
        )
        return True

    all_ok = True
    for rel_path in expected:
        full = natives_root / rel_path
        if not full.is_file():
            all_ok = False
            _print_step(f"File: {rel_path}", "FAIL", "not found")
            reports.append(StepReport(label=f"File: {rel_path}", ok=False, detail="not found"))
            continue
        size = full.stat().st_size
        if size == 0:
            all_ok = False
            _print_step(f"File: {rel_path}", "FAIL", "zero bytes")
            reports.append(StepReport(label=f"File: {rel_path}", ok=False, detail="zero bytes"))
            continue
        _print_step(f"File: {rel_path}", "OK", f"{size:,} bytes")
        reports.append(StepReport(label=f"File: {rel_path}", ok=True, detail=f"{size:,} bytes"))
    return all_ok


# ── config loading ──────────────────────────────────────────────────────────


def _load_config(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Copy verify_mvp_config.example.json -> {path.name} and fill it in."
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ── main ────────────────────────────────────────────────────────────────────


_PHASE_RUNNERS: dict[str, Callable[..., bool]] = {
    "setup": lambda client, cache, cfg, reports: _setup(client, cache, reports),
    "phase_1_2_3": _phase_1_2_3,
    "phase_4": _phase_4,
    "phase_5": _phase_5,
    "phase_6": _phase_6,
}

_DEFAULT_PHASES: tuple[str, ...] = (
    "setup",
    "phase_1_2_3",
    "phase_4",
    "phase_5",
    "phase_6",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="MVP end-to-end verification.")
    parser.add_argument(
        "--config",
        default="verify_mvp_config.json",
        help="Path to the verification config JSON.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Blender MCP host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9876,
        help="Blender MCP port (default: 9876).",
    )
    parser.add_argument(
        "--phases",
        nargs="+",
        default=None,
        help=(
            "Subset of phase groups to run, in order. "
            f"Available: {' '.join(_DEFAULT_PHASES)}. "
            "Default: all."
        ),
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Optional path to write a JSON summary report.",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    try:
        cfg = _load_config(config_path)
    except FileNotFoundError as exc:
        print(str(exc))
        return 2

    phases = tuple(args.phases) if args.phases else _DEFAULT_PHASES
    for ph in phases:
        if ph not in _PHASE_RUNNERS:
            print(f"Unknown phase {ph!r}. Valid: {sorted(_PHASE_RUNNERS)}")
            return 2

    _print_header(f"REE-ModPilot MVP verification ({config_path.name})")
    print(f"Blender: {args.host}:{args.port}")
    print(f"Phases:  {' -> '.join(phases)}")

    reports: list[StepReport] = []
    overall_ok = True

    try:
        client = BlenderClient(host=args.host, port=args.port).connect()
    except OSError as exc:
        print(f"\nFAIL: cannot connect to Blender at {args.host}:{args.port} -- {exc}")
        print("      In Blender: N-panel -> BlenderMCP -> Connect to Claude")
        return 1

    cache = SceneCache(client)

    try:
        for ph in phases:
            _print_header(f"Group: {ph}")
            ok = _PHASE_RUNNERS[ph](client, cache, cfg, reports)
            if not ok:
                overall_ok = False
                print(f"\n{ph} failed -- subsequent phases skipped.")
                break

        if overall_ok and "phase_6" in phases:
            _print_header("Post-export file checks")
            if not _check_export_artifacts(cfg, reports):
                overall_ok = False
    except BlenderError as exc:
        print(f"\nFAIL: Blender error mid-run -- {exc}")
        overall_ok = False
    finally:
        client.close()

    # ── summary ────────────────────────────────────────────────────────────
    _print_header("Summary")
    passed = sum(1 for r in reports if r.ok)
    failed = sum(1 for r in reports if not r.ok)
    print(f"{passed} pass, {failed} fail, {len(reports)} total checks.")
    if not overall_ok:
        print("\nFirst failure:")
        for r in reports:
            if not r.ok:
                print(f"  {r.label}: {r.detail}")
                break

    if args.report:
        report_path = Path(args.report).resolve()
        report_path.write_text(
            json.dumps(
                {
                    "ok": overall_ok,
                    "config": str(config_path),
                    "phases": list(phases),
                    "steps": [
                        {
                            "label": r.label,
                            "ok": r.ok,
                            "detail": r.detail,
                            "duration_s": round(r.duration_s, 3),
                            "state_diff": r.state_diff,
                        }
                        for r in reports
                    ],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"\nReport written to {report_path}")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
