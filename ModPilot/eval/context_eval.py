"""
Real-LLM long-session eval for the context-management layer.

Drives a real LLM (Ollama Cloud) through every phase of the modding workflow
with stubbed phase tools and a fake Blender, then probes the LLM at each
phase boundary to measure whether it can recall earlier facts. The recall
target is precisely the kind of detail that compaction is supposed to push
off-prompt and that `query_history` is supposed to bring back: tool
arguments and state_diff values from phases the LLM has already passed.

What it measures
----------------
- Input tokens per turn (compaction effectiveness over time — should plateau
  rather than grow linearly as more phases complete).
- For each probe: did the LLM call `query_history`? Is the response
  factually correct against the planted ground truth?
- "Context leak" count: probe turns where the LLM neither queried history
  nor produced a correct answer — the failure mode that matters most.
- Compaction ratio: bytes of moves.jsonl / bytes of in-memory _global_history.

How to run
----------
    cd ModPilot
    .venv/Scripts/python.exe eval/context_eval.py \\
        --api-key <ollama-cloud-key> \\
        --model deepseek-v4-flash \\
        --report eval/report.json

Cost expectations: a full 12-phase run with one recall probe per advanced
phase is roughly 25 llm.chat calls. On deepseek-v4-flash that runs in
single-digit cents.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

_HERE = Path(__file__).resolve().parent
_MODPILOT = _HERE.parent
if str(_MODPILOT) not in sys.path:
    sys.path.insert(0, str(_MODPILOT))

# Force UTF-8 stdout so Chinese phase names and tool replies don't crash the
# Windows console (default cp936/gbk can't render most of the symbols we
# emit). `errors='replace'` keeps the run alive if anything still slips
# through the cracks.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from app.agent.history import QUERY_HISTORY_TOOL_NAME  # noqa: E402
from app.agent.loop import _PHASE_SEQUENCE, AgentLoop, LoopState  # noqa: E402
from app.llm.ollama_provider import OllamaProvider  # noqa: E402
from app.phases.base import PhaseResult  # noqa: E402


# ── stubs: each phase tool returns a realistic-looking state_diff ────────────
#
# These dicts are the ground truth the recall probes check against. The LLM
# sees them as tool_result content inside the active phase, after which they
# get compacted away into a one-line `[compacted]` summary. To answer a
# recall probe correctly the LLM has to call `query_history` and read the
# original tool entry off the disk-backed move log.

PHASE_TOOL_STUBS: dict[str, dict[str, Any]] = {
    "setup_import_source": {
        "import_status": "imported",
        "source_armature": "Armature",
        "imported_objects": ["Armature", "Body", "Hair", "Cloth", "Shoes"],
    },
    "setup_validate_scene": {
        "valid": True,
        "errors": [],
        "source_armature": "Armature",
        "child_meshes": ["Body", "Hair", "Cloth", "Shoes"],
        "mhwilds_imported": False,
    },
    "setup_infer_model_type": {
        "inferred_preset": "VRChat",
        "coverage": 1.0,
        "decision": "auto",
        "candidates": [{"preset": "VRChat", "coverage": 1.0}],
    },
    "preset_supplement_write": {"written": False, "skipped": True},
    "preset_custom_write": {"written": False, "skipped": True},
    "setup_import_mhwilds": {
        "mhwilds_collection": "MHWilds_Female.mesh",
        "mhwilds_armature": "MHWilds_Female Armature",
        "import_status": "imported",
        "object_count": {"before": 5, "after": 7},
    },
    "pose_correction": {
        "x_preset": "VRChat",
        "y_preset": "怪猎荒野",
        "scale_ratio": 1.05,
        "operator_status": "applied",
    },
    "skeleton_align": {
        "aligned_bone_count": 42,
        "source_armature": "Armature",
        "target_armature": "MHWilds_Female Armature",
    },
    "vertex_groups": {"remapped_groups": 18, "skipped_groups": 2},
    "physics_transplant": {
        "transplanted_chains": 6,
        "target_preset": "怪猎荒野",
    },
    # Inspectors: keep state_diff empty of widget-trigger keys so the
    # AgentLoop's _emit_widget_if_inspector doesn't fire. The LLM picks the
    # phase-advancing siblings (physics_chains, material_setup) next.
    "physics_classification": {"_summary": "classified 4 chains"},
    "physics_chains": {
        "realized_chains": 4,
        "inferred_types": {
            "chain_a": "hair", "chain_b": "hair",
            "chain_c": "cloth", "chain_d": "cloth",
        },
    },
    "physics_adjust": {"adjusted_params": {"damping": 0.5, "stiffness": 0.8}},
    "material_consolidate": {"merged_slots": 0},
    "material_inspect": {"_summary": "5 materials"},
    "material_setup": {"materials_setup": 5, "linked_textures": 12},
    "material_generate": {"generated_mdf_count": 5},
    "batch_export": {
        "exported_files": ["body.mesh", "body.mdf2", "body.chain2"],
    },
}


# ── probes: one recall question per advanced phase (Heavy mode) ──────────────
#
# Each probe asks about a fact planted in a state_diff at least 2 phases ago,
# so the answer is almost certainly compacted out of the active prompt by
# the time the probe lands. expected_substrings is OR-joined: any one match
# counts as correct.

PROBES: list[dict[str, Any]] = [
    {
        "after_phase_idx": 1,  # after setup_validate
        "question": (
            "Quick question — what file_path was passed to setup_import_source "
            "back in phase 0? Use the query_history tool to look it up if you "
            "do not remember, then answer in one sentence. Do not run any "
            "phase tools."
        ),
        "expected_substrings": ["test.fbx", "eval.fbx", ".fbx"],
    },
    {
        "after_phase_idx": 2,  # after setup_infer
        "question": (
            "Remind me — what armature name did setup_import_source report "
            "in its result? Use query_history if needed."
        ),
        "expected_substrings": ["Armature"],
    },
    {
        "after_phase_idx": 3,  # after setup_import
        "question": (
            "What inferred_preset did setup_infer_model_type pick? "
            "Look it up via query_history if you do not have it."
        ),
        "expected_substrings": ["VRChat"],
    },
    {
        "after_phase_idx": 4,  # after phase_1 (pose_correction)
        "question": (
            "Was the scene valid back in phase 1 (setup_validate_scene)? "
            "Use query_history if you need the exact answer."
        ),
        "expected_substrings": ["true", "valid", "yes"],
    },
    {
        "after_phase_idx": 5,  # after phase_2 (skeleton_align)
        "question": (
            "What x_preset did pose_correction use? Check query_history if "
            "you need to."
        ),
        "expected_substrings": ["VRChat"],
    },
    {
        "after_phase_idx": 6,  # after phase_3 (vertex_groups)
        "question": (
            "What was the target_armature name passed to skeleton_align? "
            "Use query_history."
        ),
        "expected_substrings": ["MHWilds_Female"],
    },
    {
        "after_phase_idx": 7,  # after phase_35 (physics_transplant)
        "question": (
            "How many vertex groups got remapped in phase 3? "
            "Check via query_history."
        ),
        "expected_substrings": ["18"],
    },
    {
        "after_phase_idx": 8,  # after phase_4a (physics_classification/chains)
        "question": (
            "What target_preset did physics_transplant use? "
            "Query history if needed."
        ),
        "expected_substrings": ["怪猎荒野", "wilds", "monster"],
    },
    {
        "after_phase_idx": 9,  # after phase_4b (physics_adjust)
        "question": (
            "How many physics chains got realized in phase 4A "
            "(physics_chains)? Use query_history."
        ),
        "expected_substrings": ["4"],
    },
    {
        "after_phase_idx": 10,  # after phase_5 (material_*)
        "question": (
            "What chain types appeared in physics_chains' inferred_types "
            "result? Use query_history."
        ),
        "expected_substrings": ["hair", "cloth"],
    },
    # No probe after the final phase — the loop transitions to DONE and
    # every subsequent message returns the canonical 'all phases complete'
    # reply without consulting the LLM.
]


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_blender() -> MagicMock:
    """Fake Blender that answers query-tool calls with plausible-looking
    canned data. The phase tools themselves never reach this — they're
    monkey-patched to return canned PhaseResults directly."""
    b = MagicMock()
    b.get_scene_info.return_value = {"name": "Scene", "object_count": 7}
    b.execute_and_extract.return_value = ["{}"]
    b.execute.return_value = "{}"
    return b


def _patch_phase_tools() -> ExitStack:
    """Install one patch per known phase tool so PhaseTool.run returns a
    pre-baked PhaseResult.ok without touching Blender. Returns an open
    ExitStack — caller closes it to undo."""
    from app.phases.batch_export import BatchExport
    from app.phases.infer_model_type import InferModelType
    from app.phases.material import (
        MaterialConsolidate, MaterialGenerate, MaterialInspect, MaterialSetup,
    )
    from app.phases.physics_bones import (
        PhysicsAdjust, PhysicsChains, PhysicsClassification, PhysicsTransplant,
    )
    from app.phases.pose_correction import PoseCorrection
    from app.phases.preset_write import PresetCustomWrite, PresetSupplementWrite
    from app.phases.setup import (
        SetupImportMHWilds, SetupImportSource, SetupValidateScene,
    )
    from app.phases.skeleton_align import SkeletonAlign
    from app.phases.vertex_groups import VertexGroups

    tool_classes: dict[str, type] = {
        "setup_import_source": SetupImportSource,
        "setup_validate_scene": SetupValidateScene,
        "setup_infer_model_type": InferModelType,
        "preset_supplement_write": PresetSupplementWrite,
        "preset_custom_write": PresetCustomWrite,
        "setup_import_mhwilds": SetupImportMHWilds,
        "pose_correction": PoseCorrection,
        "skeleton_align": SkeletonAlign,
        "vertex_groups": VertexGroups,
        "physics_transplant": PhysicsTransplant,
        "physics_classification": PhysicsClassification,
        "physics_chains": PhysicsChains,
        "physics_adjust": PhysicsAdjust,
        "material_consolidate": MaterialConsolidate,
        "material_inspect": MaterialInspect,
        "material_setup": MaterialSetup,
        "material_generate": MaterialGenerate,
        "batch_export": BatchExport,
    }

    stack = ExitStack()
    for tool_name, cls in tool_classes.items():
        diff = PHASE_TOOL_STUBS.get(tool_name, {})
        stack.enter_context(patch.object(
            cls, "run", return_value=PhaseResult.ok(dict(diff)),
        ))
    return stack


class MeteredOllamaProvider:
    """Thin wrapper around OllamaProvider that records per-call metrics
    (token counts from `prompt_eval_count` / `eval_count` in the raw
    response, plus wall-clock latency). Exposes the same chat() signature
    the AgentLoop expects."""

    def __init__(self, inner: OllamaProvider) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    def model_name(self) -> str:
        return self._inner.model_name()

    def chat(self, messages, *, system: str = "", tools=None, max_tokens: int = 4096):
        started = time.perf_counter()
        response = self._inner.chat(
            messages, system=system, tools=tools, max_tokens=max_tokens,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        raw = response.raw if isinstance(response.raw, dict) else {}
        self.calls.append({
            "ts": time.time(),
            "elapsed_ms": round(elapsed_ms, 1),
            "prompt_tokens": raw.get("prompt_eval_count"),
            "output_tokens": raw.get("eval_count"),
            "messages_in": len(messages),
            "tool_calls_out": len(response.tool_calls),
            "stop_reason": response.stop_reason,
        })
        return response


# ── driver ───────────────────────────────────────────────────────────────────


class EvalHarness:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        session_id: str,
        max_steps_per_phase: int = 3,
        max_probe_attempts: int = 1,
    ) -> None:
        self._inner_llm = OllamaProvider(
            api_key=api_key, model=model, base_url=base_url,
        )
        self.llm = MeteredOllamaProvider(self._inner_llm)
        self.blender = _make_blender()
        self.session_id = session_id
        self.events: list[dict] = []
        self.loop = AgentLoop(
            llm=self.llm,
            blender=self.blender,
            session_id=session_id,
            event_sink=self.events.append,
        )
        self.max_steps_per_phase = max_steps_per_phase
        self.max_probe_attempts = max_probe_attempts
        self.report: dict[str, Any] = {
            "config": {
                "provider": "ollama",
                "model": model,
                "base_url": base_url,
                "session_id": session_id,
            },
            "turns": [],
            "probes": [],
        }

    # ── event helpers ────────────────────────────────────────────────────

    def _events_since(self, mark: int) -> list[dict]:
        return self.events[mark:]

    def _query_history_called_since(self, mark: int) -> bool:
        return any(
            e.get("type") == "tool_call" and e.get("name") == QUERY_HISTORY_TOOL_NAME
            for e in self._events_since(mark)
        )

    # ── core drive ───────────────────────────────────────────────────────

    async def _send(self, user_message: str, label: str) -> dict:
        event_mark = len(self.events)
        call_mark = len(self.llm.calls)
        started = time.perf_counter()
        try:
            reply = await self.loop.step(user_message)
            error: str | None = None
        except Exception as exc:  # noqa: BLE001
            reply = ""
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        new_calls = self.llm.calls[call_mark:]
        new_events = self._events_since(event_mark)
        tool_calls_seen = [
            e["name"] for e in new_events if e.get("type") == "tool_call"
        ]
        turn_record = {
            "label": label,
            "user": user_message[:200],
            "reply_excerpt": reply[:300],
            "step_elapsed_ms": round(elapsed_ms, 1),
            "llm_calls": new_calls,
            "tool_calls": tool_calls_seen,
            "phase_idx_after": self.loop._phase_idx,
            "state_after": self.loop.state.value,
            "error": error,
        }
        self.report["turns"].append(turn_record)
        print(
            f"  > {label:<36} "
            f"phase_idx={self.loop._phase_idx} "
            f"state={self.loop.state.value} "
            f"tools={','.join(tool_calls_seen) or '-'} "
            f"reply={reply[:60]!r}"
        )
        return turn_record

    async def run(self) -> None:
        print(f"\n=== ModPilot context-management eval ===")
        print(f"  model:      {self.llm.model_name()}")
        print(f"  session_id: {self.session_id}")
        print(f"  phases:     {len(_PHASE_SEQUENCE)}")
        print(f"  probes:     {len(PROBES)}")
        print()

        await self._send(
            "Start the workflow. The source FBX path is eval.fbx. "
            "Walk through the phases one at a time and use the phase tools "
            "registered in this session. Do not ask me for paths I have "
            "already given.",
            label="phase 0 entry",
        )

        for phase_idx in range(len(_PHASE_SEQUENCE)):
            # Up to N user-side turns per phase: each "continue" lets the
            # LLM call one more tool (or wrap up). We stop early once
            # `_phase_idx` has actually advanced.
            phase_start_idx = self.loop._phase_idx
            for attempt in range(self.max_steps_per_phase):
                if self.loop._phase_idx > phase_start_idx:
                    break  # phase advanced — move on
                if self.loop.state == LoopState.DONE:
                    break
                if attempt == 0 and phase_idx == 0:
                    continue  # entry message already sent above
                await self._send(
                    "continue",
                    label=f"phase {phase_idx} step {attempt}",
                )

            # Probe (Heavy mode: one per completed phase).
            probe = next(
                (p for p in PROBES if p["after_phase_idx"] == phase_idx + 1),
                None,
            )
            if probe is not None and self.loop.state != LoopState.DONE:
                await self._run_probe(probe)

            if self.loop.state == LoopState.DONE:
                print("  [STOP] loop reached DONE -- stopping main walk.")
                break

        self._compute_summary()

    async def _run_probe(self, probe: dict) -> None:
        event_mark = len(self.events)
        call_mark = len(self.llm.calls)
        started = time.perf_counter()
        reply = await self.loop.step(probe["question"])
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        queried = self._query_history_called_since(event_mark)
        reply_low = reply.lower()
        substring_found = any(
            s.lower() in reply_low for s in probe["expected_substrings"]
        )
        new_calls = self.llm.calls[call_mark:]

        probe_record = {
            "after_phase_idx": probe["after_phase_idx"],
            "phase_name": _PHASE_SEQUENCE[probe["after_phase_idx"] - 1],
            "question": probe["question"],
            "expected_substrings": probe["expected_substrings"],
            "queried_history": queried,
            "substring_found": substring_found,
            "leak": (not queried) and (not substring_found),
            "reply": reply,
            "step_elapsed_ms": round(elapsed_ms, 1),
            "llm_calls": new_calls,
        }
        self.report["probes"].append(probe_record)
        if substring_found:
            marker = "[PASS]"
        elif queried:
            marker = "[MISS]"  # asked the log but answer didn't match
        else:
            marker = "[LEAK]"  # didn't even ask the log
        print(
            f"  {marker} probe after phase {probe['after_phase_idx']} "
            f"({probe_record['phase_name']}) "
            f"queried={queried} substring={substring_found} "
            f"reply={reply[:80]!r}"
        )

    def _compute_summary(self) -> None:
        all_calls = self.llm.calls
        total_prompt = sum(
            (c.get("prompt_tokens") or 0) for c in all_calls
        )
        total_output = sum(
            (c.get("output_tokens") or 0) for c in all_calls
        )
        total_latency_ms = sum(c.get("elapsed_ms", 0.0) for c in all_calls)

        probes = self.report["probes"]
        pass_count = sum(1 for p in probes if p["substring_found"])
        query_count = sum(1 for p in probes if p["queried_history"])
        leak_count = sum(1 for p in probes if p["leak"])

        # Compaction ratio: bytes on disk / bytes in prompt.
        move_log_path = (
            Path.home() / ".modpilot" / "sessions" / self.session_id / "moves.jsonl"
        )
        on_disk = move_log_path.stat().st_size if move_log_path.exists() else 0
        in_prompt = len(json.dumps(self.loop._global_history, ensure_ascii=False).encode("utf-8"))
        ratio = (on_disk / in_prompt) if in_prompt else 0.0

        # Per-turn token series for compaction-effectiveness inspection.
        prompt_series = [c.get("prompt_tokens") for c in all_calls]

        summary = {
            "llm_calls_total": len(all_calls),
            "prompt_tokens_total": total_prompt,
            "output_tokens_total": total_output,
            "wall_latency_ms_total": round(total_latency_ms, 1),
            "prompt_tokens_per_call": prompt_series,
            "probes_total": len(probes),
            "probes_substring_pass": pass_count,
            "probes_queried_history": query_count,
            "context_leaks": leak_count,
            "compaction_ratio_disk_over_prompt": round(ratio, 3),
            "final_phase_idx": self.loop._phase_idx,
            "final_state": self.loop.state.value,
            "global_history_len": len(self.loop._global_history),
            "move_log_bytes": on_disk,
            "global_history_bytes": in_prompt,
        }
        self.report["summary"] = summary

        print("\n=== summary ===")
        for k, v in summary.items():
            if k == "prompt_tokens_per_call":
                print(f"  {k}: {v}")
            else:
                print(f"  {k}: {v}")


# ── main ─────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-key", default=os.environ.get("OLLAMA_API_KEY"),
        help="Ollama Cloud API key. Falls back to OLLAMA_API_KEY env var.",
    )
    parser.add_argument(
        "--model", default="deepseek-v4-flash",
        help="Ollama Cloud model id (default: deepseek-v4-flash).",
    )
    parser.add_argument(
        "--base-url", default="https://ollama.com",
        help="Ollama base URL (default: https://ollama.com).",
    )
    parser.add_argument(
        "--session-id", default=f"eval_{uuid.uuid4().hex[:8]}",
        help="MoveLog session id. New random id each run by default.",
    )
    parser.add_argument(
        "--report", default=str(_HERE / "report.json"),
        help="Path to write the JSON report to.",
    )
    parser.add_argument(
        "--max-steps-per-phase", type=int, default=3,
        help="Cap on user-side 'continue' messages per phase before "
             "the harness gives up and moves on.",
    )
    return parser.parse_args()


async def _async_main() -> int:
    args = _parse_args()
    if not args.api_key:
        print("error: --api-key (or OLLAMA_API_KEY env var) is required.")
        return 2

    with _patch_phase_tools():
        harness = EvalHarness(
            api_key=args.api_key,
            model=args.model,
            base_url=args.base_url,
            session_id=args.session_id,
            max_steps_per_phase=args.max_steps_per_phase,
        )
        await harness.run()

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(harness.report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  report written to {report_path}")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
