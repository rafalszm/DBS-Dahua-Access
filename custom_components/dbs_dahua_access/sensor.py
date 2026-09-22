"""Sensor platform for DBS Dahua Access."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .dahua.models import AccessEvent
from .entity import DahuaAccessEntity
from .runtime import DahuaAccessRuntime, runtime_from_entry


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up controller summary sensors."""
    runtime = runtime_from_entry(hass, entry)
    async_add_entities(
        [
            DahuaLastEventSensor(runtime, entry, "Last Event", "last_event", lambda event: event.event_name),
            DahuaLastEventSensor(runtime, entry, "Last User", "last_user", lambda event: event.person_label),
            DahuaLastEventSensor(runtime, entry, "Last Card", "last_card", lambda event: event.card_number),
            DahuaLastEventSensor(
                runtime,
                entry,
                "Last Door",
                "last_door",
                lambda event: event.door_label or (str(event.door_id) if event.door_id else ""),
            ),
            DahuaLastEventSensor(runtime, entry, "Last Result", "last_result", lambda event: event.result),
            DahuaIdentityCountSensor(runtime, entry, "Known Users", "known_users", "users_by_id"),
            DahuaIdentityCountSensor(runtime, entry, "Known Tags", "known_tags", "cards_by_number"),
        ]
    )


class DahuaLastEventSensor(DahuaAccessEntity, SensorEntity):
    """Sensor exposing one field from the last access event."""

    def __init__(
        self,
        runtime: DahuaAccessRuntime,
        entry: ConfigEntry,
        name_suffix: str,
        key: str,
        value_fn: Callable[[AccessEvent], str],
    ) -> None:
        super().__init__(runtime, entry)
        self._value_fn = value_fn
        self._attr_name = f"{runtime.device_info.name} · {name_suffix}"
        self._attr_unique_id = f"{runtime.device_info.serial}_{key}"

    @property
    def native_value(self) -> str | None:
        """Return the current sensor value."""
        if self.runtime.last_event is None:
            return None
        return self._value_fn(self.runtime.last_event) or None

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        """Return details of the last event."""
        event = self.runtime.last_event
        if event is None:
            return {}
        return {
            "controller": self.runtime.device_info.name,
            "controller_serial": self.runtime.device_info.serial,
            "door": event.door_id,
            "door_label": event.door_label,
            "reader": event.reader_id,
            "method": event.method,
            "user_id": event.user_id,
            "user_name": event.user_name,
            "device_time": event.device_time,
            "error": event.error_code,
        }


class DahuaIdentityCountSensor(DahuaAccessEntity, SensorEntity):
    """Expose the number of users or card tags loaded from the controller."""

    _attr_native_unit_of_measurement = "entries"

    def __init__(self, runtime, entry, name_suffix: str, key: str, collection_name: str) -> None:
        super().__init__(runtime, entry)
        self._collection_name = collection_name
        self._attr_name = f"{runtime.device_info.name} · {name_suffix}"
        self._attr_unique_id = f"{runtime.device_info.serial}_{key}"

    @property
    def native_value(self) -> int:
        return len(getattr(self.runtime, self._collection_name))
