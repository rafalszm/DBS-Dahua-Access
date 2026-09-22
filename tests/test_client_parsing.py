"""Focused tests for SDK response validation."""

from __future__ import annotations

import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "dbs_dahua_access"))

from dahua.client import NetSDKAccessClient  # noqa: E402


def _response(channel: int, result: bool = True) -> bytes:
    return json.dumps({"result": result, "params": {"channel": channel, "table": {}}}).encode()


def test_channel_must_be_confirmed_by_controller_response() -> None:
    assert NetSDKAccessClient._config_confirms_channel(_response(3), 3)
    assert not NetSDKAccessClient._config_confirms_channel(_response(0), 3)
    assert not NetSDKAccessClient._config_confirms_channel(_response(3, result=False), 3)
    assert not NetSDKAccessClient._config_confirms_channel(b"", 3)


def test_ablock_members_are_not_treated_as_door_count() -> None:
    raw = json.dumps(
        {
            "result": True,
            "params": {"table": {"ABLock": {"Doors": [[0, 1]], "Enable": False}}},
        }
    ).encode()
    assert NetSDKAccessClient._door_count_from_config(raw) is None


def test_explicit_door_count_is_accepted() -> None:
    raw = json.dumps({"result": True, "params": {"table": {"DoorNum": 4}}}).encode()
    assert NetSDKAccessClient._door_count_from_config(raw) == 4
