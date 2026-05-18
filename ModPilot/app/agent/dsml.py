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
import re

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
    tc_match = _RAW_TOOL_CALL_RE.search(content)
    if tc_match:
        block = tc_match.group(0)
    else:
        # Plain-string fallback — tolerates invisible character differences
        start = content.find(_DSML_OPEN_TAG)
        end = content.find(_DSML_CLOSE_TAG)
        if start == -1 or end == -1 or end < start:
            return []
        block = content[start : end + len(_DSML_CLOSE_TAG)]

    calls = []
    for i, invoke in enumerate(_DSML_INVOKE_RE.finditer(block)):
        tool_name = invoke.group("name")
        params: dict = {}
        for pm in _DSML_PARAM_RE.finditer(invoke.group("body")):
            raw = pm.group("value").strip()
            if pm.group("is_str") == "true":
                params[pm.group("name")] = raw
            else:
                try:
                    params[pm.group("name")] = json.loads(raw)
                except json.JSONDecodeError:
                    params[pm.group("name")] = raw
        calls.append({"id": f"dsml_{i}_{tool_name}", "name": tool_name, "input": params})
    return calls
