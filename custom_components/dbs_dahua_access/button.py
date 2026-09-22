"""Button platform for DBS Dahua Access."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DahuaDoorEntity
from .runtime import runtime_from_entry


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up door open buttons."""
    runtime = runtime_from_entry(hass, entry)
    async_add_entities(DahuaOpenDoorButton(runtime, entry, door_id) for door_id in sorted(runtime.doors))


class DahuaOpenDoorButton(DahuaDoorEntity, ButtonEntity):
    """Button that sends a remote open command."""

    def __init__(self, runtime, entry, door_id: int) -> None:
        super().__init__(runtime, entry, door_id, "Open")
        self._attr_icon = "mdi:door-open"

    async def async_press(self) -> None:
        """Open the door."""
        await self.runtime.async_open_door(self.door_id)
