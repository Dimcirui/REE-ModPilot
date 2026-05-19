"""Unit tests for app/toolkit_probe.py.

Covers:
  - _classify rules (the three-state taxonomy)
  - probe_toolkit happy paths (all present / all missing / mixed)
  - disabled vs missing distinction via installed_match
  - error surfaces: empty output / non-JSON / non-list / BlenderError / OSError propagation
  - TOOL_SPECS shape (no silent gaps in the required tool list)
  - ToolStatus serialization

Run with: uv run pytest -m unit tests/unit/test_toolkit_probe.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.toolkit_probe import (
    TOOL_SPECS,
    ToolStatus,
    _build_probe_code,
    _classify,
    probe_toolkit,
)


@pytest.mark.unit
class TestClassify:
    def test_op_present_returns_present(self):
        assert _classify({"op_present": True, "installed_match": False}) == "present"
        assert _classify({"op_present": True, "installed_match": True}) == "present"

    def test_installed_without_op_returns_disabled(self):
        assert _classify({"op_present": False, "installed_match": True}) == "disabled"

    def test_neither_returns_missing(self):
        assert _classify({"op_present": False, "installed_match": False}) == "missing"

    def test_missing_keys_default_to_missing(self):
        assert _classify({}) == "missing"


@pytest.mark.unit
class TestProbeToolkit:
    @staticmethod
    def _stub_client(payload):
        client = MagicMock(spec=BlenderClient)
        client.execute_and_extract.return_value = [json.dumps(payload)]
        return client

    def test_all_present(self):
        payload = [
            {"id": "mbt", "label": "Modding-Toolkit",
             "op_present": True, "installed_match": True},
            {"id": "mhws", "label": "MHWs Plugin",
             "op_present": True, "installed_match": True},
            {"id": "re_mesh", "label": "RE Mesh Editor",
             "op_present": True, "installed_match": True},
            {"id": "re_chain", "label": "RE Chain Editor",
             "op_present": True, "installed_match": True},
        ]
        client = self._stub_client(payload)
        result = probe_toolkit(client)
        assert len(result) == 4
        assert all(s.status == "present" for s in result)
        assert [s.id for s in result] == ["mbt", "mhws", "re_mesh", "re_chain"]

    def test_all_missing(self):
        payload = [
            {"id": spec["id"], "label": spec["label"],
             "op_present": False, "installed_match": False}
            for spec in TOOL_SPECS
        ]
        client = self._stub_client(payload)
        result = probe_toolkit(client)
        assert all(s.status == "missing" for s in result)

    def test_mixed_missing_disabled_present(self):
        payload = [
            {"id": "mbt", "label": "Modding-Toolkit",
             "op_present": False, "installed_match": False},
            {"id": "mhws", "label": "MHWs Plugin",
             "op_present": False, "installed_match": True},
            {"id": "re_mesh", "label": "RE Mesh Editor",
             "op_present": True, "installed_match": True},
            {"id": "re_chain", "label": "RE Chain Editor",
             "op_present": True, "installed_match": True},
        ]
        client = self._stub_client(payload)
        result = probe_toolkit(client)
        statuses = {s.id: s.status for s in result}
        assert statuses == {
            "mbt": "missing",
            "mhws": "disabled",
            "re_mesh": "present",
            "re_chain": "present",
        }

    def test_labels_preserved_from_payload(self):
        payload = [
            {"id": spec["id"], "label": spec["label"],
             "op_present": True, "installed_match": True}
            for spec in TOOL_SPECS
        ]
        client = self._stub_client(payload)
        result = probe_toolkit(client)
        labels = {s.id: s.label for s in result}
        assert labels["mbt"] == "Modding-Toolkit"
        assert labels["mhws"] == "MHWs Plugin"
        assert labels["re_mesh"] == "RE Mesh Editor"
        assert labels["re_chain"] == "RE Chain Editor"

    def test_critical_flag_default_true(self):
        payload = [
            {"id": spec["id"], "label": spec["label"],
             "op_present": True, "installed_match": True}
            for spec in TOOL_SPECS
        ]
        client = self._stub_client(payload)
        result = probe_toolkit(client)
        assert all(s.critical is True for s in result)

    def test_timeout_threaded_through(self):
        payload = [
            {"id": spec["id"], "label": spec["label"],
             "op_present": True, "installed_match": True}
            for spec in TOOL_SPECS
        ]
        client = self._stub_client(payload)
        probe_toolkit(client, timeout=10.0)
        _, kwargs = client.execute_and_extract.call_args
        assert kwargs.get("timeout") == 10.0

    def test_empty_output_raises(self):
        client = MagicMock(spec=BlenderClient)
        client.execute_and_extract.return_value = []
        with pytest.raises(BlenderError, match="no output"):
            probe_toolkit(client)

    def test_non_json_output_raises(self):
        client = MagicMock(spec=BlenderClient)
        client.execute_and_extract.return_value = ["not json"]
        with pytest.raises(BlenderError, match="non-JSON"):
            probe_toolkit(client)

    def test_non_list_payload_raises(self):
        client = MagicMock(spec=BlenderClient)
        client.execute_and_extract.return_value = [json.dumps({"oops": "dict"})]
        with pytest.raises(BlenderError, match="not a list"):
            probe_toolkit(client)

    def test_blender_error_propagates(self):
        client = MagicMock(spec=BlenderClient)
        client.execute_and_extract.side_effect = BlenderError("connection lost")
        with pytest.raises(BlenderError, match="connection lost"):
            probe_toolkit(client)

    def test_os_error_propagates(self):
        client = MagicMock(spec=BlenderClient)
        client.execute_and_extract.side_effect = OSError("socket closed")
        with pytest.raises(OSError):
            probe_toolkit(client)

    def test_multi_line_payload_concatenated(self):
        """Long JSON output may wrap across multiple printed lines."""
        payload = [
            {"id": spec["id"], "label": spec["label"],
             "op_present": True, "installed_match": True}
            for spec in TOOL_SPECS
        ]
        raw = json.dumps(payload)
        half = len(raw) // 2
        client = MagicMock(spec=BlenderClient)
        client.execute_and_extract.return_value = [raw[:half], raw[half:]]
        result = probe_toolkit(client)
        assert all(s.status == "present" for s in result)


@pytest.mark.unit
class TestToolSpecs:
    def test_required_namespaces_present(self):
        ids = {s["id"] for s in TOOL_SPECS}
        assert ids == {"mbt", "mhws", "re_mesh", "re_chain"}

    def test_each_spec_has_required_keys(self):
        for spec in TOOL_SPECS:
            assert {"id", "ns", "key_op", "label", "hints"} <= set(spec.keys())
            assert isinstance(spec["hints"], list) and spec["hints"]
            assert isinstance(spec["label"], str) and spec["label"]


@pytest.mark.unit
class TestProbeCodeShape:
    def test_includes_sentinel(self):
        code = _build_probe_code()
        assert BLENDER_SENTINEL in code

    def test_imports_required_modules(self):
        code = _build_probe_code()
        assert "import bpy" in code
        assert "addon_utils" in code

    def test_includes_every_tool_namespace(self):
        code = _build_probe_code()
        for spec in TOOL_SPECS:
            assert spec["ns"] in code
            assert spec["key_op"] in code


@pytest.mark.unit
class TestToolStatusSerialization:
    def test_to_dict_round_trip(self):
        s = ToolStatus(id="mbt", label="Modding-Toolkit",
                       status="present", critical=True)
        d = s.to_dict()
        assert d == {
            "id": "mbt",
            "label": "Modding-Toolkit",
            "status": "present",
            "critical": True,
        }
