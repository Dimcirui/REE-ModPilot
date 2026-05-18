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

from app.resources import docs_dir

_WORKFLOW_PATH = docs_dir() / "agent_workflow.md"
_WORKFLOW_TEXT: str = _WORKFLOW_PATH.read_text(encoding="utf-8")

_CONTEXT_MANAGEMENT_PROTOCOL: str = (
    "## Context Management Protocol\n"
    "After each phase completes, the verbose tool_use / tool_result / narration "
    "messages from that phase are collapsed in your visible chat history to a "
    "single summary message marked `[compacted]`. The full record (every tool "
    "call with its arguments and result, every user reply, every widget "
    "confirmation, every error choice, every interrupt) is preserved off-prompt "
    "in the session's move log on disk.\n"
    "\n"
    "When you need detail older than the current phase — for example, the exact "
    "`inferred_types` you confirmed in Phase 4A while now wiring Phase 4B, or "
    "which textures the user mapped in a Phase 5 widget — call the "
    "`query_history` meta-tool to retrieve it. Examples:\n"
    "\n"
    "- `query_history(phase=\"phase_4a\", kind=\"tool\")` → all tool calls "
    "recorded while Phase 4A was active.\n"
    "- `query_history(name=\"physics_classification\")` → every call to that "
    "tool, regardless of phase.\n"
    "- `query_history(kind=\"widget\", last_n=5)` → the five most recent widget "
    "confirmations and their JSON payloads.\n"
    "- `query_history()` → the most recent moves across all kinds and phases.\n"
    "\n"
    "Decision guide:\n"
    "- For CURRENT scene state (what bones exist now, what materials are now "
    "wired, what objects are selected), prefer the existing read-only Blender "
    "query tools (`scene_info`, `get_bone_info`, `get_material_info`, "
    "`list_objects`, etc.). They reflect live Blender, not the historical "
    "decision log.\n"
    "- For PAST decisions and arguments (what the user picked in a widget, what "
    "you classified in an earlier phase, what `inferred_types` dict you passed "
    "to `physics_chains`), use `query_history`.\n"
    "- Set `last_n` to a small value (e.g. 5-10) when you do not know the "
    "specific phase or tool to filter on. The server applies a default cap "
    "when you omit `last_n` and a hard ceiling when you pass a large value, "
    "so a no-args call cannot dump the whole log — but filtering narrowly "
    "still wastes fewer tokens than blanket scans."
)


_WIDGET_PROTOCOL: str = (
    "## Confirmation Widget Protocol (issue #7)\n"
    "Two inspector tools surface their results to the user via structured UI "
    "widgets instead of free-text Q&A. After they succeed, the next user message "
    "arrives with a special prefix carrying the user's confirmed selections as "
    "JSON. Do NOT re-ask the user for these values — parse the JSON and feed it "
    "into the next phase tool call directly.\n"
    "\n"
    "- After `physics_classification` succeeds, the next user message is prefixed\n"
    "  `[CONFIRMED_CLASSIFICATIONS]` followed by JSON with this structure:\n"
    "  `{\"inferred_types\": {\"bone_001\": \"hair_long_straight\", ...},\n"
    "   \"descriptions\": {\"bone_001\": \"optional override text\", ...},\n"
    "   \"bones_to_merge\": [\"Eye_L\", \"Eye_R\", ...]}`.\n"
    "  Call `physics_chains` with ALL THREE of:\n"
    "    - `inferred_types`  → the `inferred_types` dict (bone → preset key)\n"
    "    - `bones_to_merge`  → the `bones_to_merge` list (merge into parent first)\n"
    "    - `target_armature` → from session config\n"
    "  Do NOT include `bones_to_merge` entries as keys in `inferred_types`.\n"
    "\n"
    "- After `material_inspect` succeeds, the next user message is prefixed\n"
    "  `[CONFIRMED_MATERIAL_MAPPING]` followed by JSON like\n"
    "  `{\"matname\": {\"Base Color\": \"C:/path/to/diffuse.png\", \"Normal\": \"...\"}}`.\n"
    "  Pass that dict as `texture_mapping` to `material_setup` (skip materials\n"
    "  whose mapping is empty).\n"
    "\n"
    "- When `setup_infer_model_type` returns `decision='unsupported'` and the\n"
    "  user clicks the `[强制自定义]` button on the error_choice widget, the\n"
    "  next user message arrives prefixed `[FORCE_CUSTOM]`. Re-call\n"
    "  `setup_infer_model_type` with `force_custom=true` (and the same\n"
    "  source_armature) to enter the issue #6 custom-preset flow.\n"
    "\n"
    "If the prefix is absent, fall back to the legacy text-based confirmation flow."
)


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


def _render_session_config_block(cfg: dict) -> str:
    """Format the form-collected session config as a system-prompt section.

    Listed values are the deterministic params the user provided up front via
    the Stage 5 config form (issue #3). The LLM should pass them through to
    phase tool calls instead of asking the user mid-run.
    """
    model_type = cfg.get("model_type", "")
    if model_type == "MMD":
        x_preset_hint = "Use x_preset='MMD' for Phase 1 pose_correction."
    elif model_type == "VRChat":
        x_preset_hint = "Use x_preset='VRChat' for Phase 1 pose_correction."
    else:
        x_preset_hint = (
            "model_type is 'Other' — ask the user ONCE for the correct x_preset "
            "(valid: MMD / VRChat / 终末地) before running Phase 1."
        )

    author = cfg.get("author", "")
    character_name = cfg.get("character_name", "")
    texture_base_path = f"{author}/{character_name}/" if author and character_name else ""

    return (
        "## Pre-collected session parameters\n"
        "The user has provided the following deterministic parameters via the "
        "session-config form. Do NOT ask the user for these values; pass them "
        "through to phase tools as needed.\n"
        "\n"
        f"- model_path: {cfg.get('model_path', '')}\n"
        f"- model_type: {model_type}  ({x_preset_hint})\n"
        f"- texture_dir (for material_inspect): {cfg.get('texture_dir', '')}\n"
        f"- mod_root (= natives_root for batch_export): {cfg.get('mod_root', '')}\n"
        f"- author: {author}\n"
        f"- character_name: {character_name}\n"
        f"- texture_base_path (for material_generate): {texture_base_path}\n"
        f"- use_bone_system (= mhws_use_bonesystem for batch_export): "
        f"{cfg.get('use_bone_system', False)}\n"
        f"- body_parts (= target_parts for batch_export): {cfg.get('body_parts', [])}\n"
        # Issue #10: hunter type + equipment selection are now pre-collected.
        # Phase 6 must read these from this block instead of asking the user
        # or scanning an armor table inline.
        f"- armor_variant (= batch_export armor_variant): {cfg.get('armor_variant', 'ff')}\n"
        f"- armor_id (= batch_export armor_id): {cfg.get('armor_id', '')}\n"
    )


def build_system_prompt(
    physics_presets: dict | None = None,
    session_config: dict | None = None,
) -> str:
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
    session_config (issue #3) is appended as a final "Pre-collected parameters"
    section so the LLM doesn't need to ask the user for paths / names / toggles
    already supplied via the config form.
    """
    global_rules = _extract_section(_WORKFLOW_TEXT, "Global Behavior Rules")
    assessment_protocol = _extract_section(_WORKFLOW_TEXT, "Pipeline State Assessment Protocol")
    # Issue #15: pause-between-phases rule.  Paired with the AgentLoop rail
    # that breaks the tool-call loop after a phase-advancing tool succeeds.
    transition_protocol = _extract_section(_WORKFLOW_TEXT, "Phase Transition Protocol")
    phase_seq = _extract_section(_WORKFLOW_TEXT, "Phase Sequence")
    # Issue #4: Setup Phase now contains the model-type inference step
    # (#4/#5/#6); inject it so the LLM knows about the new step 1.5 and
    # the supplement/custom flows.
    setup_phase = _extract_section(_WORKFLOW_TEXT, "Setup Phase")
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
        transition_protocol,
        "",
        _CONTEXT_MANAGEMENT_PROTOCOL,
        "",
        _WIDGET_PROTOCOL,
        "",
        global_rules,
        "",
        phase_seq,
        "",
        setup_phase,
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

    if session_config:
        parts += ["", _render_session_config_block(session_config)]

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
