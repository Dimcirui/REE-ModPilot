"""Toolkit dependency preflight check.

Probes Blender for the presence/enabled state of every addon the phase
tool layer depends on. Surfaces a `{present, disabled, missing}` status
per tool so the frontend can tell the user precisely what's wrong instead
of letting a phase tool 500 mid-run.

The probe issues one `execute_code` round-trip via the existing socket.
Inside Blender it:
  - operator-namespace check: `key_op in dir(getattr(bpy.ops, ns))` —
    strongest signal that the addon is enabled and registered.
  - installed-but-disabled detection: scan `addon_utils.modules()` and
    compare module/bl_info names against per-tool hints. Lets us tell
    "not installed" from "installed but the user disabled it post-upgrade".

Status classification:
  op_present                                      → "present"
  not op_present AND installed_match              → "disabled"
  neither                                         → "missing"
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError

ToolStatusValue = Literal["present", "disabled", "missing"]


@dataclass(frozen=True)
class ToolStatus:
    id: str
    label: str
    status: ToolStatusValue
    critical: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


TOOL_SPECS: list[dict] = [
    {
        "id": "mbt",
        "ns": "mbt",
        "key_op": "import_mhwilds_fmesh",
        "label": "Modding-Toolkit",
        "hints": ["modding", "mhwilds", "mbt"],
    },
    {
        "id": "mhws",
        "ns": "mhws",
        "key_op": "batch_export",
        "label": "MHWs Plugin",
        "hints": ["mhws", "wilds"],
    },
    {
        "id": "re_mesh",
        "ns": "re_mesh",
        "key_op": "delete_loose",
        "label": "RE Mesh Editor",
        "hints": ["re_mesh", "re mesh", "remesh"],
    },
    {
        "id": "re_chain",
        "ns": "re_chain",
        "key_op": "create_chain_header",
        "label": "RE Chain Editor",
        "hints": ["re_chain", "re chain", "rechain"],
    },
]


def _build_probe_code() -> str:
    """Return the Python snippet that runs inside Blender.

    Prints `BLENDER_SENTINEL`, then a JSON list of
    `{id, label, op_present, installed_match}` per spec.
    """
    specs_py = json.dumps(TOOL_SPECS)
    return (
        "import bpy, addon_utils, json\n"
        f"_specs = json.loads({specs_py!r})\n"
        "try:\n"
        "    _all = list(addon_utils.modules())\n"
        "except Exception:\n"
        "    _all = []\n"
        "_installed = []\n"
        "for _mod in _all:\n"
        "    _name = (getattr(_mod, '__name__', '') or '').lower()\n"
        "    _info = getattr(_mod, 'bl_info', None) or {}\n"
        "    _display = str(_info.get('name', '') or '').lower()\n"
        "    _installed.append(_name + '|' + _display)\n"
        "_result = []\n"
        "for _s in _specs:\n"
        "    _ns_obj = getattr(bpy.ops, _s['ns'], None)\n"
        "    _op_present = False\n"
        "    if _ns_obj is not None:\n"
        "        try:\n"
        "            _op_present = _s['key_op'] in dir(_ns_obj)\n"
        "        except Exception:\n"
        "            _op_present = False\n"
        "    _installed_match = any(\n"
        "        any(_h in _line for _h in _s['hints'])\n"
        "        for _line in _installed\n"
        "    )\n"
        "    _result.append({\n"
        "        'id': _s['id'], 'label': _s['label'],\n"
        "        'op_present': _op_present,\n"
        "        'installed_match': _installed_match,\n"
        "    })\n"
        f"print({BLENDER_SENTINEL!r})\n"
        "print(json.dumps(_result))\n"
    )


def _classify(item: dict) -> ToolStatusValue:
    if item.get("op_present"):
        return "present"
    if item.get("installed_match"):
        return "disabled"
    return "missing"


def probe_toolkit(client: BlenderClient, timeout: float = 5.0) -> list[ToolStatus]:
    """Probe Blender for the state of every required addon.

    Returns one ToolStatus per `TOOL_SPECS` entry (deterministic order).
    Raises BlenderError on empty / non-JSON / non-list probe output. Underlying
    socket errors (BlenderError, OSError) from `execute_and_extract` propagate
    so the route layer can surface them as 503.
    """
    code = _build_probe_code()
    lines = client.execute_and_extract(code, timeout=timeout)
    if not lines:
        raise BlenderError("toolkit probe returned no output")
    # Probe template emits the JSON via a single print(); a single line is the
    # steady state. Empty-string join keeps us robust if a stray print or terminal
    # wrap ever splits the payload — concatenation reconstructs the original.
    raw = "".join(lines)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BlenderError(f"toolkit probe returned non-JSON: {raw!r}") from exc
    if not isinstance(payload, list):
        raise BlenderError(f"toolkit probe payload was not a list: {payload!r}")
    return [
        ToolStatus(
            id=item.get("id", "unknown"),
            label=item.get("label", item.get("id", "unknown")),
            status=_classify(item),
            critical=True,
        )
        for item in payload
    ]
