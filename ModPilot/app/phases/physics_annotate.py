"""Physics-chain LLM annotation pipeline.

After PhysicsClassification extracts chain heads from Blender, the agent
needs to label each chain (hair / cloth / ribbon / tail / ...) and pick a
preset physics type so the user sees meaningful defaults in the
classification widget instead of a blank table.

The labelling is an LLM call, plus three deterministic recovery passes:

1. Main LLM pass — produce one annotation per chain.
2. Base-name propagation — copy a sibling's annotation onto numbered /
   lateral variants the LLM grouped under a single representative entry.
3. Single-chain fallback — issue a targeted second LLM call for any chain
   that was outright dropped (no sibling to propagate from).
4. Apply deterministic merge rules — `suggest_merge` and a fallback
   `guessed_nature` label are derived from the group/depth, not from the
   LLM.

Extracted from `app.agent.loop` so the prompt engineering does not crowd
the ReAct state machine.  `annotate_chains` is the only public entry point;
callers pass an LLM client and an optional emit callback for debug events.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable

from app.llm.client import LLMClient

_GROUP_FALLBACK_NATURE: dict[str, str] = {
    "hair":        "头发",
    "cloth":       "布料",
    "ribbon":      "飘带",
    "tail":        "尾巴",
    "non_physics": "辅助骨",
}


def _apply_merge_rules(chain: dict) -> dict:
    """Apply deterministic post-processing after LLM annotation.

    1. suggest_merge: derived from group and depth — not left to the LLM.
    2. guessed_nature fallback: if the LLM omitted it but group is known,
       substitute a generic label so the UI never shows a bare dash.
    """
    grp = chain.get("group", "other")
    should_merge = grp == "non_physics" or int(chain.get("depth", 0)) <= 1
    nature = chain.get("guessed_nature") or _GROUP_FALLBACK_NATURE.get(grp, "")
    return {**chain, "suggest_merge": should_merge, "guessed_nature": nature}


def _extract_partial_json_objects(text: str) -> list[dict]:
    """Extract all complete JSON objects from potentially truncated or malformed text.

    Uses JSONDecoder.raw_decode to scan forward from each '{', collecting every
    successfully-parsed object.  Objects after the truncation point are silently
    skipped, so callers get the best partial result available.
    """
    results: list[dict] = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start == -1:
            break
        try:
            obj, end_idx = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                results.append(obj)
            idx = end_idx
        except json.JSONDecodeError:
            idx = start + 1
    return results


async def annotate_chains(
    llm: LLMClient,
    chains: list[dict],
    *,
    emit: Callable[..., None] | None = None,
) -> list[dict]:
    """Call LLM to add guessed_category, suggested_type, suggest_merge to each chain.

    Falls back to original chains (with safe defaults applied) on any error.
    """
    from app.phases.physics_bones import list_inferred_types  # local import avoids cycle
    known_types = list_inferred_types()
    _defaults: dict = {
        "guessed_nature": "",
        "group": "other",
        "suggested_type": "",
        "suggest_merge": False,
    }

    if not chains:
        return chains

    chain_summary = json.dumps(chains, ensure_ascii=False, indent=2)
    prompt = (
        "You are annotating physics chain heads for a character mod.\n"
        "For each chain, infer the following THREE fields in ORDER:\n\n"
        '1. "guessed_nature": What this chain physically IS (specific, Chinese or English).\n'
        "   Examples: '头发' for hair, '眼睛' for eye, '带子' for belt/ribbon, '裙子' for skirt,\n"
        "   '布料' for generic cloth, '尾巴' for tail, '袖子' for sleeve.\n"
        "   Base this on the bone name, depth, parent, and role — be specific to each chain.\n\n"
        '2. "group": The physics simulation category (for UI grouping). '
        "Use EXACTLY one of:\n"
        "   'hair'        — hair chains\n"
        "   'cloth'       — cloth / skirt / dress chains\n"
        "   'ribbon'      — belt / sash / ribbon chains\n"
        "   'tail'        — tail chains\n"
        "   'non_physics' — bones that should NOT have physics "
        "(eyes, face accessories, minor helpers)\n"
        "   'other'       — anything else\n\n"
        '3. "suggested_type": The best-matching physics preset key, '
        "derived FROM guessed_nature.\n"
        f"   Must be one of: {json.dumps(known_types)}\n"
        "   For non_physics chains use the closest available or 'body_jiggle'.\n\n"
        "Chain heads (JSON array):\n"
        f"{chain_summary}\n\n"
        "Reply ONLY with a JSON array in the SAME ORDER as the input. "
        "Each element must include ALL original keys plus the three new fields."
    )
    raw_response = ""
    try:
        response = await asyncio.to_thread(
            llm.chat,
            [{"role": "user", "content": prompt}],
            system="You are a concise JSON assistant. Output only valid JSON, no markdown fences.",
            max_tokens=8192,
        )
        raw_response = response.content.strip()
        content = raw_response

        # Robust extraction:
        # 1. Direct: already starts with "["
        # 2. Markdown fence: ```json ... ```
        # 3. Regex: find first [...] block (handles any preamble text)
        # 4. Dict wrapper: {"result": [...]} or similar
        if not content.startswith("["):
            fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
            if fence_match:
                content = fence_match.group(1).strip()
        if not content.startswith("["):
            arr_match = re.search(r"\[[\s\S]*\]", content)
            if arr_match:
                content = arr_match.group(0)

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Truncated / partially-malformed array: scan for individual objects.
            # This recovers all chains that were successfully generated before
            # the LLM truncated or introduced a syntax error.
            parsed = _extract_partial_json_objects(content)

        # Unwrap dict if LLM returned {"chains": [...]} or similar
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    parsed = v
                    break

        if isinstance(parsed, list):
            # Exact length match: zip by position
            if len(parsed) == len(chains):
                result = [{**_defaults, **orig, **anno}
                          for orig, anno in zip(chains, parsed, strict=False)]
            else:
                # Length mismatch: match by "name" field so partial results still help
                name_to_anno: dict[str, dict] = {
                    item.get("name"): item
                    for item in parsed
                    if isinstance(item, dict) and item.get("name")
                }
                result = [{**_defaults, **c, **name_to_anno.get(c.get("name", ""), {})}
                          for c in chains]
            # Propagate annotation to same-base-name variants still at defaults.
            # LLMs often group numbered siblings (Skirt_L.001/006/011…) under one
            # representative entry, so exact name matching leaves them blank.
            # Strip trailing variant suffixes — any combination of .NNN, .L/.R,
            # _L/_R, _End — to derive a common base name, then copy the first
            # matched annotation to all unannotated siblings.
            #
            # Examples (covers real bone names from Phase 3.5 + MMD/VRC sources):
            #   "Skirt_L.001"            → "Skirt"           (strip .001, then _L)
            #   "Shoes ribbon_L.001.L"   → "Shoes ribbon"    (strip .L, .001, _L)
            #   "Shoes ribbon.L_End"     → "Shoes ribbon"    (strip _End, then .L)
            #   "Half twin tail_R.007"   → "Half twin tail"
            # Lateral specificity (L vs R) is intentionally lost: physics type
            # for left/right mirrored chains is always identical anyway.
            _variant_suffix = re.compile(r'(\.\d+|[._][LR]|_End)+$')
            _anno_fields = ("guessed_nature", "group", "suggested_type")
            base_to_anno: dict[str, dict] = {}
            for c in result:
                if c.get("suggested_type"):
                    base = _variant_suffix.sub('', c.get("name", ""))
                    base_to_anno.setdefault(base, {k: c[k] for k in _anno_fields if k in c})
            result = [
                {**c, **base_to_anno[_variant_suffix.sub('', c.get("name", ""))]}
                if not c.get("suggested_type") and _variant_suffix.sub('', c.get("name", "")) in base_to_anno
                else c
                for c in result
            ]
            # Single-condition fallback retry: scan for chains the LLM
            # silently dropped (no sibling for propagation either — typical
            # case is a lone bone like "Tail.001" that gets overlooked when
            # buried among 60+ chains).  Make ONE small targeted LLM call
            # for the remaining empties.  Non-fatal — keep going on any error.
            still_empty = [c for c in result if not c.get("suggested_type")]
            if still_empty:
                fb_anno = await _fallback_annotate_individual(
                    llm, still_empty, known_types, emit=emit,
                )
                if fb_anno:
                    result = [
                        {**c, **fb_anno[c["name"]]}
                        if not c.get("suggested_type") and c.get("name") in fb_anno
                        else c
                        for c in result
                    ]
            # Derive suggest_merge deterministically from group and depth.
            # The LLM is not asked to set this field; rules are applied here
            # so the result is predictable regardless of LLM output.
            result = [_apply_merge_rules(c) for c in result]
            # Debug trace: emit first annotated chain so mismatch is visible in debug mode
            if emit is not None:
                emit(
                    "tool_result",
                    id="annotate_chains",
                    name="annotate_chains",
                    success=True,
                    summary=f"annotated {len(result)} chains; sample={json.dumps(result[0] if result else {})[:200]}",
                )
            return result

    except Exception as exc:
        # Non-fatal: emit as debug-only tool_result so it's visible in debug mode
        # without triggering the frontend error state.
        if emit is not None:
            emit(
                "tool_result",
                id="annotate_chains",
                name="annotate_chains",
                success=False,
                summary=f"annotation LLM call failed ({type(exc).__name__}: {exc}); raw={raw_response[:300]}",
            )
    return [_apply_merge_rules({**_defaults, **c}) for c in chains]


async def _fallback_annotate_individual(
    llm: LLMClient,
    missing: list[dict],
    known_types: list[str],
    *,
    emit: Callable[..., None] | None = None,
) -> dict[str, dict]:
    """Targeted second-pass LLM call for chains the main annotation pass
    dropped (no sibling for base-name propagation to copy from).

    Returns a dict keyed by chain `name` → annotation dict containing
    guessed_nature/group/suggested_type.  Returns {} on any error.
    Non-fatal — caller falls back to `_defaults` if this returns empty.
    """
    if not missing:
        return {}

    chain_summary = json.dumps(missing, ensure_ascii=False, indent=2)
    prompt = (
        "The PRIOR annotation pass missed these chain heads. "
        "Output a JSON array with ONE entry per input — preserve the original "
        "`name` field exactly, and add the three fields below:\n"
        '  "guessed_nature": Chinese or English noun for what this chain physically is.\n'
        '  "group": one of "hair" / "cloth" / "ribbon" / "tail" / "non_physics" / "other".\n'
        f'  "suggested_type": one of {json.dumps(known_types)}.\n'
        "DO NOT skip any entry. Reply ONLY with a JSON array, no markdown.\n\n"
        "Chains needing annotation:\n"
        f"{chain_summary}"
    )
    try:
        response = await asyncio.to_thread(
            llm.chat,
            [{"role": "user", "content": prompt}],
            system="You are a concise JSON assistant. Output only valid JSON, no markdown fences.",
            max_tokens=2048,
        )
        content = response.content.strip()
        # Same extraction logic as the main pass.
        if not content.startswith("["):
            fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
            if fence:
                content = fence.group(1).strip()
        if not content.startswith("["):
            arr = re.search(r"\[[\s\S]*\]", content)
            if arr:
                content = arr.group(0)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = _extract_partial_json_objects(content)
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    parsed = v
                    break
        if not isinstance(parsed, list):
            return {}
        result: dict[str, dict] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            # Only carry forward the three annotation fields — don't let
            # the LLM overwrite role/depth/parent etc.
            annotation = {
                k: item[k]
                for k in ("guessed_nature", "group", "suggested_type")
                if k in item and item[k]
            }
            if annotation.get("suggested_type"):
                result[name] = annotation
        if emit is not None:
            emit(
                "tool_result",
                id="annotate_chains_fallback",
                name="_fallback_annotate_individual",
                success=True,
                summary=f"recovered {len(result)}/{len(missing)} dropped annotations",
            )
        return result
    except Exception as exc:
        if emit is not None:
            emit(
                "tool_result",
                id="annotate_chains_fallback",
                name="_fallback_annotate_individual",
                success=False,
                summary=f"fallback failed ({type(exc).__name__}: {exc})",
            )
        return {}
