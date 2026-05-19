"""DeepSeek inline-XML tool-call workaround ("DSML" markup).

DeepSeek V4 sometimes emits tool calls as inline XML-like markup embedded in
the text content instead of using the OpenAI API's structured `function_call`
fields. When the loop is in NEGOTIATING mode we strip the markup out of the
visible reply; when in RUNNING_PHASE we parse and execute the calls so they
are not silently dropped.

Extracted from `app.agent.loop` so the regexes, the fallback string-find
heuristic, and the canonical tool-call shape all live together.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_RAW_TOOL_CALL_RE = re.compile(
    r"<｜｜DSML｜｜tool_calls>.*?</｜｜DSML｜｜tool_calls>",
    re.DOTALL,
)
_DSML_INVOKE_RE = re.compile(
    r'<｜｜DSML｜｜invoke name="(?P<name>[^"]+)">(?P<body>.*?)</｜｜DSML｜｜invoke>',
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    r'<｜｜DSML｜｜parameter name="(?P<name>[^"]+)" string="(?P<is_str>true|false)">'
    r"(?P<value>.*?)</｜｜DSML｜｜parameter>",
    re.DOTALL,
)

_DSML_OPEN_TAG = "<｜｜DSML｜｜tool_calls>"
_DSML_CLOSE_TAG = "</｜｜DSML｜｜tool_calls>"

# Tolerant variants — match any DSML-ish markup regardless of exact pipe
# codepoint (ASCII `|` vs fullwidth `｜`), pipe count, or surrounding spaces.
# Defends against DeepSeek emitting new character variants of the same template.
_PIPE_CLS = r"[\|｜\s]*"
_TOLERANT_BLOCK_RE = re.compile(
    rf"<{_PIPE_CLS}DSML{_PIPE_CLS}tool_calls{_PIPE_CLS}>"
    r".*?"
    rf"<{_PIPE_CLS}/{_PIPE_CLS}DSML{_PIPE_CLS}tool_calls{_PIPE_CLS}>",
    re.DOTALL,
)
_TOLERANT_INVOKE_RE = re.compile(
    rf'<{_PIPE_CLS}DSML{_PIPE_CLS}invoke\s+name="(?P<name>[^"]+)"\s*>'
    r"(?P<body>.*?)"
    rf"<{_PIPE_CLS}/{_PIPE_CLS}DSML{_PIPE_CLS}invoke{_PIPE_CLS}>",
    re.DOTALL,
)
_TOLERANT_PARAM_RE = re.compile(
    rf'<{_PIPE_CLS}DSML{_PIPE_CLS}parameter\s+name="(?P<name>[^"]+)"'
    r'(?:\s+string="(?P<is_str>true|false)")?\s*>'
    r"(?P<value>.*?)"
    rf"<{_PIPE_CLS}/{_PIPE_CLS}DSML{_PIPE_CLS}parameter{_PIPE_CLS}>",
    re.DOTALL,
)
# Loose marker — any "DSML ... tool_calls/invoke/parameter" pattern, used
# only to flag suspicious content. Cheap; no capture groups.
_LOOSE_MARKER_RE = re.compile(
    r"DSML[\|｜\s]*(?:tool_calls|invoke|parameter)",
    re.IGNORECASE,
)


def looks_like_dsml(text: str) -> bool:
    """Cheap detector — True if text contains any DSML-ish marker token."""
    if not text:
        return False
    return bool(_LOOSE_MARKER_RE.search(text))


def sanitize_outbound(text: str) -> str:
    """Final-stage stripper for content leaving the agent toward the UI.

    Runs strict, then tolerant, then a truncate-at-orphan-marker fallback.
    Use at the SSE chokepoint so variant-character markup can never reach
    the chat bubble even when per-branch strippers miss it.
    """
    if not text:
        return text
    out = _RAW_TOOL_CALL_RE.sub("", text)
    out = _TOLERANT_BLOCK_RE.sub("", out)
    # Orphan marker (no matching close tag) — cut from its opening '<'.
    m = _LOOSE_MARKER_RE.search(out)
    if m:
        lt = out.rfind("<", 0, m.start())
        if lt != -1:
            out = out[:lt]
    return out.strip()


def strip_dsml_block(text: str) -> str:
    """
    Remove the DSML tool-call block from text, returning only the prose part.

    Tries regex first; if it leaves any DSML behind (due to invisible Unicode
    differences), falls back to plain-string truncation at the open tag.
    """
    text = _RAW_TOOL_CALL_RE.sub("", text).strip()
    # Greedy fallback: regex may miss the block if chars look identical but differ
    start = text.find(_DSML_OPEN_TAG)
    if start != -1:
        text = text[:start].rstrip()
    return text


def parse_dsml_tool_calls(content: str) -> list[dict]:
    """
    Extract tool calls from DeepSeek DSML markup embedded in text content.

    Returns a list of canonical tool-call dicts ({id, name, input}) that can
    be passed directly to AgentLoop._execute_tool_call().  Returns [] when
    no DSML markup block is found.

    Uses regex first; falls back to plain str.find() when the outer block
    regex fails (e.g. due to invisible Unicode differences between the source
    pattern and the model's output that visually appear identical).
    """
    # 1. Strict block + strict invokes (the happy path).
    tc_match = _RAW_TOOL_CALL_RE.search(content)
    if tc_match:
        calls = _extract_invokes(tc_match.group(0), _DSML_INVOKE_RE, _DSML_PARAM_RE)
        if calls:
            return calls

    # 2. Plain-find on strict tag constants — tolerates a missing close tag.
    start = content.find(_DSML_OPEN_TAG)
    end = content.find(_DSML_CLOSE_TAG)
    if start != -1 and end != -1 and end > start:
        block = content[start : end + len(_DSML_CLOSE_TAG)]
        calls = _extract_invokes(block, _DSML_INVOKE_RE, _DSML_PARAM_RE)
        if calls:
            return calls

    # 3. Tolerant block + tolerant invokes — handles variant pipe codepoints,
    #    pipe-count drift, and missing `string=` attribute on parameters.
    tol_block = _TOLERANT_BLOCK_RE.search(content)
    search_in = tol_block.group(0) if tol_block else content
    calls = _extract_invokes(search_in, _TOLERANT_INVOKE_RE, _TOLERANT_PARAM_RE)
    if calls:
        logger.warning(
            "DSML tool calls parsed via tolerant fallback — a new char variant "
            "may have shipped. n_calls=%d",
            len(calls),
        )
    return calls


def _extract_invokes(block: str, invoke_re: re.Pattern, param_re: re.Pattern) -> list[dict]:
    calls: list[dict] = []
    for i, invoke in enumerate(invoke_re.finditer(block)):
        tool_name = invoke.group("name")
        params: dict = {}
        for pm in param_re.finditer(invoke.group("body")):
            raw = pm.group("value").strip()
            is_str_group = pm.groupdict().get("is_str")
            if is_str_group == "true" or is_str_group is None:
                params[pm.group("name")] = raw
            else:
                try:
                    params[pm.group("name")] = json.loads(raw)
                except json.JSONDecodeError:
                    params[pm.group("name")] = raw
        calls.append({"id": f"dsml_{i}_{tool_name}", "name": tool_name, "input": params})
    return calls
