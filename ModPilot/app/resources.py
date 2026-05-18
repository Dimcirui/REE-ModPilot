"""Resource path resolution for both dev and pyinstaller-frozen runs.

In dev, paths anchor on the source tree (`app/__file__`'s ancestors).
When frozen by pyinstaller, the resource tree is unpacked under `sys._MEIPASS`
and `__file__` lives in that temp dir — direct ancestor walks break.

Use `resource_root()` to get the directory under which `app/`, `docs/`, etc.
are visible. All asset reads should anchor there.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running inside a pyinstaller bundle (one-file or one-dir)."""
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """
    Directory containing the bundled resource tree.

    Frozen: pyinstaller's `_MEIPASS` (set on `sys` when frozen). The .spec
            file adds `app/data/`, `docs/agent_workflow.md`, and the React
            bundle under `app/static_built/` to this tree.
    Dev:    repo root (the parent of `ModPilot/`), so existing
            `docs/agent_workflow.md` and `ModPilot/app/...` paths resolve
            the same way they did before this helper existed.
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    # ModPilot/app/resources.py → ModPilot/app → ModPilot → repo root
    return Path(__file__).resolve().parent.parent.parent


def app_data_dir() -> Path:
    """Directory holding bundled JSON catalogs (armor_sets.json, physics_presets.json)."""
    if is_frozen():
        return resource_root() / "app" / "data"
    return Path(__file__).resolve().parent / "data"


def docs_dir() -> Path:
    """Directory holding bundled docs (agent_workflow.md)."""
    return resource_root() / "docs"


def static_built_dir() -> Path:
    """Directory holding the Vite SPA bundle (index.html + assets/)."""
    if is_frozen():
        return resource_root() / "app" / "static_built"
    return Path(__file__).resolve().parent / "static_built"
