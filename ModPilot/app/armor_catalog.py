"""
MHWilds armor-set catalog (issue #10).

Static list of (armor_id, name) pairs, sourced from the Modding-Toolkit's
`assets/mhws/armor_sets/mhws_armor_sets.json`. Shipped in-tree at
`app/data/armor_sets.json` so the session-config form can populate its
equipment dropdown without requiring Blender to be running at boot.
"""

from __future__ import annotations

import json

from app.resources import app_data_dir

_CATALOG_PATH = app_data_dir() / "armor_sets.json"
_CACHE: list[dict[str, str]] | None = None


def _load() -> list[dict[str, str]]:
    global _CACHE
    if _CACHE is None:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        _CACHE = list(data.get("sets", []))
    return _CACHE


def list_armor_sets() -> list[dict[str, str]]:
    """Return the full catalog as [{id, name}, ...]."""
    return list(_load())


def is_valid_armor_id(armor_id: str) -> bool:
    """True iff armor_id appears in the catalog. Case-sensitive."""
    return any(entry["id"] == armor_id for entry in _load())
