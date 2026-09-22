"""Shared DBS Dahua Access entity helpers."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .runtime import DahuaAccessRuntime


class DahuaAccessEntity(Entity):
    """Base entity for DBS Dahua Access."""

    _attr_has_entity_name = False

    def __init__(self, runtime: DahuaAccessRuntime, entry: ConfigEntry) -> None:
        self.runtime = runtime
        self.entry = entry
        self._attr_device_info = runtime.device_info_payload()

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime updates."""
        self.async_on_remove(async_dispatcher_connect(self.hass, self.runtime.signal_update, self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return if the controller is available."""
        return self.runtime.online


class DahuaDoorEntity(DahuaAccessEntity):
    """Base entity tied to one door."""

    def __init__(self, runtime: DahuaAccessRuntime, entry: ConfigEntry, door_id: int, suffix: str) -> None:
        super().__init__(runtime, entry)
        self.door_id = door_id
        self._name_suffix = suffix
        self._attr_unique_id = f"{runtime.device_info.serial}_door_{door_id}_{suffix.lower().replace(' ', '_')}"

    @property
    def name(self) -> str:
        """Return a name that can follow labels learned from controller events."""
        return f"{self.runtime.entity_name_for_door(self.door_id)} · {self._name_suffix}"

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        """Return common door attributes."""
        return {
            "controller": self.runtime.device_info.name,
            "controller_serial": self.runtime.device_info.serial,
            "door": self.door_id,
            "sdk_channel": self.runtime.doors.get(self.door_id).sdk_channel if self.door_id in self.runtime.doors else None,
            "door_label": self.runtime.doors.get(self.door_id).label if self.door_id in self.runtime.doors else "",
        }


def build_last_event_value(runtime: DahuaAccessRuntime, getter: Callable[[object], str]) -> str | None:
    """Return a value from the last event if available."""
    if runtime.last_event is None:
        return None
    return getter(runtime.last_event) or None
