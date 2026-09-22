"""Tests for Dahua access event normalization."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_NORMALIZER_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "dbs_dahua_access"
    / "dahua"
    / "normalizer.py"
)
_SPEC = importlib.util.spec_from_file_location("dbs_dahua_access_normalizer", _NORMALIZER_PATH)
normalizer = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(normalizer)

controller_entity_name = normalizer.controller_entity_name
mask_card_number = normalizer.mask_card_number
normalize_access_result = normalizer.normalize_access_result
normalize_door_status_name = normalizer.normalize_door_status_name
normalize_open_method_name = normalizer.normalize_open_method_name
resolve_door_label = normalizer.resolve_door_label
tag_id_for_card = normalizer.tag_id_for_card


def test_normalize_open_method_aliases() -> None:
    assert normalize_open_method_name("NET_ACCESS_DOOROPEN_METHOD_CARD") == "card"
    assert normalize_open_method_name("NET_ACCESS_DOOROPEN_METHOD_PWD_ONLY") == "pin"
    assert normalize_open_method_name("NET_ACCESS_DOOROPEN_METHOD_FACE_RECOGNITION") == "face"


def test_normalize_access_result() -> None:
    assert normalize_access_result(1, 0) == "access_granted"
    assert normalize_access_result(0, 16) == "access_denied"
    assert normalize_access_result(1, 16) == "access_unknown"


def test_normalize_door_status() -> None:
    assert normalize_door_status_name("NET_ACCESS_CTL_STATUS_TYPE_OPEN") == "open"
    assert normalize_door_status_name("NET_ACCESS_CTL_STATUS_TYPE_CLOSE") == "closed"
    assert normalize_door_status_name("NET_ACCESS_CTL_STATUS_TYPE_FAKELOCKED") == "fake_locked"


def test_resolve_door_label_prefers_controller_event_name() -> None:
    assert resolve_door_label(2, event_name="Magazyn", stored_name="Drzwi 2") == "Magazyn"
    assert resolve_door_label(2, stored_name="Drzwi 2") == "Drzwi 2"
    assert resolve_door_label(2) == "Przejście 2"


def test_entity_name_and_tag_id() -> None:
    assert controller_entity_name("KD1 PIWNICA KOZLA", "Magazyn") == "KD1 PIWNICA KOZLA · Magazyn"
    assert tag_id_for_card("058D1C32") == "dahua:058D1C32"


def test_mask_card_number() -> None:
    assert mask_card_number("058D1C32") == "****1C32"
    assert mask_card_number("1234") == "1234"
