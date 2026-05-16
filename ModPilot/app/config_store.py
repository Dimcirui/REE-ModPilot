"""
Persisted global config (issue #9).

Layers the .env-derived Settings singleton with a user-editable JSON file at
`~/.modpilot/config.json`. Loaded once at app startup; written back whenever
the user submits the /config UI.

Only a curated set of fields are persisted — the ones the issue text calls
out as user-editable. App-level knobs (app_host / app_port / app_debug)
remain .env-only because they affect server bind behavior and shouldn't be
mutated from the UI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import Settings

# Fields the user can edit via the /config page. Keep this in sync with the
# form template (templates/config.html) and the AppConfigUpdate Pydantic
# model in main.py.
PERSISTED_FIELDS: tuple[str, ...] = (
    "llm_provider",
    "llm_api_key",
    "llm_model",
    "llm_base_url",
    "blender_host",
    "blender_port",
)


def _config_path() -> Path:
    """Return the OS-agnostic path to the persisted config JSON.

    Resolved at call time (not import time) so tests can monkeypatch
    Path.home() before any IO happens.
    """
    return Path.home() / ".modpilot" / "config.json"


def load() -> dict[str, Any]:
    """Read the persisted config dict. Returns {} when the file is absent
    or corrupt — startup must not crash on a malformed user file.
    """
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Drop unknown keys defensively so a future-removed field can't poison
    # the Settings mutation.
    return {k: v for k, v in data.items() if k in PERSISTED_FIELDS}


def save(values: dict[str, Any]) -> None:
    """Persist the given subset of fields. Creates the parent directory if
    missing. Unknown keys are dropped, same defensive filter as load().
    """
    filtered = {k: v for k, v in values.items() if k in PERSISTED_FIELDS}
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_to_settings(settings: Settings, values: dict[str, Any]) -> None:
    """Mutate the Settings singleton in place with the persisted values.

    pydantic-settings v2 BaseSettings is unfrozen, so setattr works as
    expected. Only known fields are applied; type coercion (e.g. int for
    blender_port) is delegated to Pydantic via model_validate / setattr.
    """
    for field in PERSISTED_FIELDS:
        if field not in values:
            continue
        # Pydantic v2 validates per-field assignment when
        # model_config validate_assignment=True; our Settings doesn't set
        # that flag, so manually coerce blender_port to int for safety.
        value = values[field]
        if field == "blender_port" and not isinstance(value, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        setattr(settings, field, value)
