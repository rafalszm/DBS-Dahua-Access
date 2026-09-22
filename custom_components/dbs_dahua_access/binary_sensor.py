"""Binary sensor platform for DBS Dahua Access."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DahuaAccessEntity, DahuaDoorEntity
from .runtime import runtime_from_entry


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up binary sensors."""
    runtime = runtime_from_entry(hass, entry)
    entities: list[BinarySensorEntity] = [DahuaControllerOnlineSensor(runtime, entry)]
    entities.extend(DahuaDoorStatusSensor(runtime, entry, door_id) for door_id in sorted(runtime.doors))
    async_add_entities(entities)


class DahuaControllerOnlineSensor(DahuaAccessEntity, BinarySensorEntity):
    """Controller online sensor."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, runtime, entry) -> None:
        super().__init__(runtime, entry)
        self._attr_name = f"{runtime.device_info.name} · Online"
        self._attr_unique_id = f"{runtime.device_info.serial}_online"

    @property
    def is_on(self) -> bool:
        """Return whether the controller is online."""
        return self.runtime.online


class DahuaDoorStatusSensor(DahuaDoorEntity, BinarySensorEntity):
    """Door open/closed sensor."""

    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, runtime, entry, door_id: int) -> None:
        super().__init__(runtime, entry, door_id, "Door")

    @property
    def is_on(self) -> bool | None:
        """Return whether the door is open."""
        status = self.runtime.last_door_status.get(self.door_id)
        if status is None:
            return None
        return status in {"open", "abnormal", "always_open", "forced_open", "held_open"}

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        """Return door attributes."""
        data = super().extra_state_attributes
        data["door_status"] = self.runtime.last_door_status.get(self.door_id, "unknown")
        return data
