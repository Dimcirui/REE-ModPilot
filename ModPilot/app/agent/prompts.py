"""
Prompt builder functions for the ModPilot agent loop (design decision E24).

Sources:
  - docs/agent_workflow.md  — machine-readable workflow (C11 amendment)
  - physics_presets dict    — injected into system prompt at startup (E19)

All functions are pure: they read the workflow doc and return strings.
No LLM calls happen here.
"""

from __future__ import annotations

import json
from pathlib import Path

_WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "docs" / "agent_workflow.md"
)
_WORKFLOW_TEXT: str = _WORKFLOW_PATH.read_text(encoding="utf-8")

_PHASE_HEADER_MAP: dict[str, str] = {
    "phase_1": "Phase 1: Pose Correction",
    "phase_2": "Phase 2: Skeleton Alignment",
    "phase_3": "Phase 3: Vertex Groups",
    "phase_35": "Phase 3.5: Physics Bone Transplant",
    "phase_4a": "Phase 4A: Physics Bone Classification",
    "phase_4b": "Phase 4B: Physics File Creation",
    "phase_5": "Phase 5: Material Processing",
    "phase_6": "Phase 6: Batch Export",
}


# ── section extraction ─────────────────────────────────────────────────────


def _extract_section(text: str, header_substring: str) -> str:
    """
    Find the first markdown header whose title contains header_substring
    (case-insensitive). Return that header line plus all content up to the
    next header at equal or higher depth level.
    """
    lines = text.splitlines()
    start_idx: int | None = None
    section_depth: int = 0

    for i, line in enumerate(lines):
        if not line.startswith("#"):
            continue
        depth = len(line) - len(line.lstrip("#"))
        title = line.lstrip("#").strip()
        if header_substring.lower() in title.lower():
            start_idx = i
            section_depth = depth
            break

    if start_idx is None:
        return ""

    result = [lines[start_idx]]
    for line in lines[start_idx + 1 :]:
        if line.startswith("#"):
            depth = len(line) - len(line.lstrip("#"))
            if depth <= section_depth:
                break
        result.append(line)

    return "\n".join(result).strip()


# ── public builders ────────────────────────────────────────────────────────


def build_system_prompt(physics_presets: dict | None = None) -> str:
    """
    Build the session-level system prompt (injected once at AgentLoop init).

    Includes: agent identity statement, Global Behavior Rules, Phase Sequence
    diagram, and the full workflow for all phases (1-3 preprocessing block plus
    Phase 3.5/4A/4B/5/6).  Including all phases unconditionally ensures the
    agent has complete workflow context even in resume scenarios where _phase_idx
    has not advanced to the current phase (e.g. prior phases were done manually
    in a previous session).  Per-phase injection in _run_react_turn still runs
    as a reminder but is no longer the primary context source for later phases.

    physics_presets are appended inline when provided (E19).
    """
    global_rules = _extract_section(_WORKFLOW_TEXT, "Global Behavior Rules")
    assessment_protocol = _extract_section(_WORKFLOW_TEXT, "Pipeline State Assessment Protocol")
    phase_seq = _extract_section(_WORKFLOW_TEXT, "Phase Sequence")
    preprocessing = _extract_section(_WORKFLOW_TEXT, "Phase 1–3: Preprocessing Block")
    phase_35 = _extract_section(_WORKFLOW_TEXT, "Phase 3.5: Physics Bone Transplant")
    phase_4a = _extract_section(_WORKFLOW_TEXT, "Phase 4A: Physics Bone Classification")
    phase_4b = _extract_section(_WORKFLOW_TEXT, "Phase 4B: Physics File Creation")
    phase_5 = _extract_section(_WORKFLOW_TEXT, "Phase 5: Material Processing")
    phase_6 = _extract_section(_WORKFLOW_TEXT, "Phase 6: Batch Export")

    parts = [
        "You are ModPilot, an AI agent that automates MHWs (Monster Hunter Wilds) "
        "character mod creation inside Blender. You control Blender by calling phase "
        "tools in sequence. Follow the workflow instructions below exactly.",
        "",
        "LANGUAGE RULE: Always respond in Simplified Chinese. "
        "Keep technical terms (operator names, object names, file paths, code) in English. "
        "This applies to ALL replies including error explanations and phase summaries.",
        "",
        # Assessment protocol first — placed before all phase content so the LLM
        # sees it before encountering any object/collection names from phase descriptions
        # that might prime history-based reasoning instead of fresh tool calls.
        assessment_protocol,
        "",
        global_rules,
        "",
        phase_seq,
        "",
        preprocessing,
        "",
        phase_35,
        "",
        phase_4a,
        "",
        phase_4b,
        "",
        phase_5,
        "",
        phase_6,
    ]

    if physics_presets:
        parts += [
            "",
            "## Physics Presets Reference (Phase 4B)",
            "Map inferred_type values to RE Chain preset names using this table:",
            "```json",
            json.dumps(physics_presets, ensure_ascii=False, indent=2),
            "```",
        ]

    return "\n".join(parts)


def build_phase_prompt(phase_name: str) -> str:
    """
    Return the agent_workflow.md section for the given phase identifier.
    Injected at the start of each NEGOTIATING phase history (E24).
    Returns empty string for unknown phase_name.
    """
    header = _PHASE_HEADER_MAP.get(phase_name, "")
    if not header:
        return ""
    return _extract_section(_WORKFLOW_TEXT, header)


def build_error_prompt(operator: str, message: str, suggestion: str) -> str:
    """
    Build a prompt asking the LLM to translate a PhaseError into plain user
    language, then append [Retry] / [Skip] / [Ask] options (B7).
    """
    lines = [
        "A Blender phase has failed. Translate this technical error into a concise "
        "plain-language message in Simplified Chinese for a modder (2-3 sentences max). "
        "Keep technical terms (operator names, object names) in English.",
        "After the explanation, always end with exactly:",
        "[Retry] — 重新执行  |  [Skip] — 跳过继续  |  [Ask] — 查看详情",
        "",
        f"Operator: {operator or '(pre-operator validation)'}",
        f"Error: {message}",
    ]
    if suggestion:
        lines.append(f"Suggested fix: {suggestion}")
    return "\n".join(lines)
