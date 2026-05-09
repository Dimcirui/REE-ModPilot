"""
Agent-side Blender scene state cache (design decision B5).

Philosophy:
  - The cache is a lightweight snapshot of what the agent *believes* Blender
    contains. It is NOT a full Blender state mirror.
  - Each phase tool refreshes the cache on entry (spot-check) and updates it
    on exit (diff). The agent loop should not query Blender on every LLM step.
  - SceneState is a plain dataclass — easy to serialize, diff, and log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.blender.client import BlenderClient


@dataclass
class ObjectInfo:
    """Lightweight summary of a single Blender object."""

    name: str
    type: str  # e.g. "MESH", "ARMATURE", "EMPTY"
    # Extend as phases require more fields (vertex group count, bone count, etc.)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SceneState:
    """
    A point-in-time snapshot of what the agent knows about the Blender scene.

    Fields are populated lazily — only data that a phase actually needs is
    fetched. Unpopulated fields remain None; callers should re-fetch when None.
    """

    scene_name: str = ""
    object_count: int = 0
    object_names: list[str] = field(default_factory=list)
    materials_count: int = 0
    # Detailed per-object info, keyed by name — populated on demand
    objects: dict[str, ObjectInfo] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self.scene_name == ""

    def diff(self, other: "SceneState") -> dict[str, Any]:
        """
        Return a dict describing what changed between self (old) and other (new).
        Useful for structured error messages and agent reasoning.
        """
        changes: dict[str, Any] = {}
        if self.object_count != other.object_count:
            changes["object_count"] = {"before": self.object_count, "after": other.object_count}
        old_names = set(self.object_names)
        new_names = set(other.object_names)
        added = new_names - old_names
        removed = old_names - new_names
        if added:
            changes["objects_added"] = sorted(added)
        if removed:
            changes["objects_removed"] = sorted(removed)
        if self.materials_count != other.materials_count:
            changes["materials_count"] = {
                "before": self.materials_count,
                "after": other.materials_count,
            }
        return changes


class SceneCache:
    """
    Wraps a BlenderClient and maintains an up-to-date SceneState.

    Intended usage (inside a phase tool):

        async def run(self, cache: SceneCache) -> Result:
            state = await cache.refresh()   # entry spot-check
            # ... call operators ...
            new_state = await cache.refresh()
            diff = state.diff(new_state)    # exit update
    """

    def __init__(self, client: BlenderClient) -> None:
        self._client = client
        self._state = SceneState()

    @property
    def state(self) -> SceneState:
        """Last cached state. Call refresh() to update."""
        return self._state

    def refresh(self) -> SceneState:
        """
        Query Blender for current scene info and update the cache.
        Returns the new SceneState.
        """
        raw = self._client.get_scene_info()
        self._state = _parse_scene_info(raw)
        return self._state

    def invalidate(self) -> None:
        """Reset cache to empty state (forces next access to re-fetch)."""
        self._state = SceneState()


# ── helpers ────────────────────────────────────────────────────────────────


def _parse_scene_info(raw: dict) -> SceneState:
    """
    Convert the raw get_scene_info payload into a SceneState.

    blender-mcp addon.py returns something like:
        {
          "name": "Scene",
          "object_count": 3,
          "objects": [{"name": "Cube", "type": "MESH"}, ...],
          "materials_count": 2,
        }
    Exact shape verified against verify_blender_mcp.py Stage 0 check 3.
    """
    objects_raw: list[dict] = raw.get("objects", [])
    objects = {
        obj["name"]: ObjectInfo(name=obj["name"], type=obj.get("type", "UNKNOWN"))
        for obj in objects_raw
    }
    return SceneState(
        scene_name=raw.get("name", ""),
        object_count=raw.get("object_count", len(objects_raw)),
        object_names=[obj["name"] for obj in objects_raw],
        materials_count=raw.get("materials_count", 0),
        objects=objects,
    )
