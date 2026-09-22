"""Runtime object for one Dahua access config entry."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_MASK_CARDS_IN_LOGS,
    CONF_TAG_EVENTS,
    DEFAULT_MASK_CARDS_IN_LOGS,
    DEFAULT_TAG_EVENTS,
    DOMAIN,
    EVENT_ACCESS,
    EVENT_TAG_SCANNED,
)
from .dahua import AccessDeviceInfo, AccessDoor, AccessEvent, AccessUser, DahuaAccessClient
from .dahua.normalizer import controller_entity_name, mask_card_number, resolve_door_label, tag_id_for_card

_LOGGER = logging.getLogger(__name__)


class DahuaAccessRuntime:
    """Runtime state and event bridge for one controller."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: DahuaAccessClient,
        device_info: AccessDeviceInfo,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.device_info = device_info
        self.doors: dict[int, AccessDoor] = {}
        self.last_event: AccessEvent | None = None
        self.last_door_status: dict[int, str] = {}
        self.users_by_id: dict[str, AccessUser] = {}
        self.online = True

    @property
    def signal_update(self) -> str:
        """Return dispatcher signal for entity updates."""
        return f"{DOMAIN}_{self.entry.entry_id}_update"

    @property
    def device_identifier(self) -> tuple[str, str]:
        """Return HA device identifier."""
        return (DOMAIN, self.device_info.serial)

    async def async_start(self) -> None:
        """Discover doors and start listening."""
        doors = await self.hass.async_add_executor_job(self.client.discover_doors)
        self.doors = {door.door_id: door for door in doors}
        await self.hass.async_add_executor_job(self.client.start_listening, self._threadsafe_event_callback)

    async def async_stop(self) -> None:
        """Stop listening and disconnect."""
        await self.hass.async_add_executor_job(self.client.stop_listening)
        await self.hass.async_add_executor_job(self.client.disconnect)
        self.online = False

    async def async_open_door(self, door_id: int, direction: str = "unknown") -> None:
        """Open one door remotely."""
        await self.hass.async_add_executor_job(self.client.open_door, door_id, direction)

    def entity_name_for_door(self, door_id: int) -> str:
        """Return controller-prefixed entity name for a door."""
        door = self.doors.get(door_id)
        label = door.label if door else resolve_door_label(door_id)
        return controller_entity_name(self.device_info.name, label)

    def device_info_payload(self) -> dict[str, Any]:
        """Return HA device_info payload shared by entities."""
        return {
            "identifiers": {self.device_identifier},
            "manufacturer": self.device_info.manufacturer,
            "name": self.device_info.name,
            "model": self.device_info.model or None,
            "serial_number": self.device_info.serial,
            "configuration_url": f"http://{self.entry.data.get('host')}",
        }

    def event_payload(self, event: AccessEvent) -> dict[str, Any]:
        """Build event payload for Home Assistant."""
        card_number = event.card_number
        visible_card = mask_card_number(card_number) if self._mask_cards and card_number else card_number
        return {
            "controller": self.device_info.name,
            "controller_serial": self.device_info.serial,
            "controller_ip": self.entry.data.get("host"),
            "door": event.door_id,
            "door_name": event.door_name,
            "door_label": event.door_label,
            "entity_name": self.entity_name_for_door(event.door_id) if event.door_id else "",
            "reader": event.reader_id,
            "event": event.event_name,
            "kind": event.kind,
            "result": event.result,
            "method": event.method,
            "user_id": event.user_id,
            "user_name": event.user_name,
            "card_number": visible_card,
            "card_tag_id": tag_id_for_card(card_number) if card_number else "",
            "error": event.error_code,
            "device_time": event.device_time,
            "raw_event_type": event.raw_event_type,
            "raw_status": event.raw_status,
            **dict(event.raw),
        }

    @property
    def _tag_events_enabled(self) -> bool:
        return bool(self.entry.options.get(CONF_TAG_EVENTS, DEFAULT_TAG_EVENTS))

    @property
    def _mask_cards(self) -> bool:
        return bool(self.entry.options.get(CONF_MASK_CARDS_IN_LOGS, DEFAULT_MASK_CARDS_IN_LOGS))

    def _threadsafe_event_callback(self, event: AccessEvent) -> None:
        self.hass.loop.call_soon_threadsafe(lambda: self.hass.async_create_task(self._async_handle_event(event)))

    async def _async_handle_event(self, event: AccessEvent) -> None:
        enriched = await self._async_enrich_event(event)
        self._remember_event(enriched)
        payload = self.event_payload(enriched)
        self.hass.bus.async_fire(EVENT_ACCESS, payload)

        if self._tag_events_enabled and enriched.card_number:
            self.hass.bus.async_fire(
                EVENT_TAG_SCANNED,
                {
                    "tag_id": tag_id_for_card(enriched.card_number),
                    "name": enriched.person_label or enriched.card_number,
                    "user_id": enriched.user_id,
                    "user_name": enriched.user_name,
                    "door": enriched.door_id,
                    "door_label": enriched.door_label,
                    "controller": self.device_info.name,
                },
            )

        async_dispatcher_send(self.hass, self.signal_update)

    async def _async_enrich_event(self, event: AccessEvent) -> AccessEvent:
        door_label = event.door_label
        if event.door_id:
            stored = self.doors.get(event.door_id)
            label = resolve_door_label(event.door_id, event.door_name, stored.label if stored else "")
            if event.door_name and (stored is None or stored.label != event.door_name):
                self.doors[event.door_id] = AccessDoor(event.door_id, event.door_name, "event")
            door_label = label

        user_name = event.user_name
        if event.user_id and not user_name:
            cached = self.users_by_id.get(event.user_id)
            if cached:
                user_name = cached.name
            else:
                try:
                    user = await asyncio.wait_for(
                        self.hass.async_add_executor_job(self.client.get_user, event.user_id),
                        timeout=1.5,
                    )
                except TimeoutError:
                    user = None
                except Exception:
                    _LOGGER.debug("Failed to resolve Dahua user %s", event.user_id, exc_info=True)
                    user = None
                if user:
                    self.users_by_id[user.user_id] = user
                    user_name = user.name

        return replace(event, door_label=door_label, user_name=user_name)

    @callback
    def _remember_event(self, event: AccessEvent) -> None:
        self.last_event = event
        if event.door_id and "door_status" in event.raw:
            self.last_door_status[event.door_id] = str(event.raw["door_status"])


def runtime_from_entry(hass: HomeAssistant, entry: ConfigEntry) -> DahuaAccessRuntime:
    """Return runtime data for a config entry."""
    return hass.data[DOMAIN][entry.entry_id]
