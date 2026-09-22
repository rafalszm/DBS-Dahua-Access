"""Normalize Dahua SDK values into Home Assistant friendly names."""

from __future__ import annotations

from typing import Any


def simplify_enum_name(name: str, *prefixes: str) -> str:
    """Strip SDK enum prefixes and normalize to lowercase."""
    value = str(name)
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.lower()


def enum_name(enum_cls: Any, value: Any) -> str:
    """Return an enum member name without leaking SDK exceptions."""
    try:
        return enum_cls(value).name
    except Exception:
        return str(int(value)) if isinstance(value, int) else str(value)


def normalize_open_method_name(raw_name: str) -> str:
    """Normalize a Dahua door-open method enum name."""
    short = simplify_enum_name(raw_name, "NET_ACCESS_DOOROPEN_METHOD_")
    return {
        "card": "card",
        "pwd_only": "pin",
        "remote": "remote",
        "button": "button",
        "fingerprint": "fingerprint",
        "face_recognition": "face",
        "qrcode": "qrcode",
    }.get(short, short)


def normalize_door_status_name(raw_name: str) -> str:
    """Normalize a Dahua door status enum name."""
    short = simplify_enum_name(raw_name, "NET_ACCESS_CTL_STATUS_TYPE_")
    return {
        "open": "open",
        "close": "closed",
        "abnormal": "abnormal",
        "fakelocked": "fake_locked",
        "closealways": "always_closed",
        "openalways": "always_open",
        "normal": "normal",
        "unknown": "unknown",
    }.get(short, short)


def normalize_event_type_name(raw_name: str) -> str:
    """Normalize a Dahua access event type enum name."""
    return simplify_enum_name(raw_name, "NET_ACCESS_CTL_EVENT_")


def normalize_access_result(status: int, error_code: int) -> str:
    """Normalize access grant/deny state."""
    if int(status) == 1 and int(error_code) == 0:
        return "access_granted"
    if int(status) == 0:
        return "access_denied"
    return "access_unknown"


def resolve_door_label(door_id: int, event_name: str = "", stored_name: str = "") -> str:
    """Resolve the user-facing door label."""
    return event_name.strip() or stored_name.strip() or f"Przejście {door_id}"


def controller_entity_name(controller_name: str, door_label: str) -> str:
    """Build the HA-facing controller + door entity prefix."""
    return f"{controller_name} · {door_label}"


def tag_id_for_card(card_number: str) -> str:
    """Build a namespaced Home Assistant tag id from a Dahua card number."""
    return f"dahua:{card_number.strip()}"


def mask_card_number(card_number: str) -> str:
    """Mask a card number for ordinary logs while preserving readability."""
    card_number = card_number.strip()
    if len(card_number) <= 4:
        return card_number
    return f"{'*' * (len(card_number) - 4)}{card_number[-4:]}"
