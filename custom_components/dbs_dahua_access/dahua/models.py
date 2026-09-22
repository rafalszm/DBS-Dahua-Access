"""Data models for Dahua access controllers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AccessControllerConfig:
    """Connection details for one access controller."""

    host: str
    port: int
    username: str
    password: str


@dataclass(frozen=True, slots=True)
class AccessDeviceInfo:
    """Stable device information returned by the controller."""

    serial: str
    name: str
    model: str = ""
    manufacturer: str = "Dahua"
    firmware: str = ""
    hardware: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AccessDoor:
    """One logical door or passage on the controller."""

    door_id: int
    label: str
    sdk_channel: int
    source: str = "unknown"


@dataclass(frozen=True, slots=True)
class AccessUser:
    """Access-control user resolved from the controller."""

    user_id: str
    name: str = ""
    status: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AccessCard:
    """One card credential stored by the access controller."""

    card_number: str
    user_id: str = ""
    name: str = ""
    status: int | None = None


@dataclass(frozen=True, slots=True)
class AccessEvent:
    """Normalized access-control event."""

    kind: str
    result: str = ""
    door_id: int | None = None
    door_name: str = ""
    door_label: str = ""
    reader_id: str = ""
    method: str = ""
    user_id: str = ""
    user_name: str = ""
    card_number: str = ""
    card_name: str = ""
    error_code: int | None = None
    device_time: str = ""
    raw_event_type: int | None = None
    raw_status: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def event_name(self) -> str:
        """Return the best high-level event name for Home Assistant."""
        return self.result or self.kind

    @property
    def person_label(self) -> str:
        """Return a human-readable person label."""
        if self.user_name or self.card_name or self.user_id:
            return self.user_name or self.card_name or f"ID {self.user_id}"
        if self.method == "pin":
            return "PIN kontrolera"
        return ""
