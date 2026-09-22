"""Runtime object for one Dahua access config entry."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from homeassistant.components import tag as ha_tag
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_MASK_CARDS_IN_LOGS,
    CONF_TAG_EVENTS,
    DEFAULT_MASK_CARDS_IN_LOGS,
    DEFAULT_TAG_EVENTS,
    DOMAIN,
    EVENT_ACCESS,
)
from .dahua import AccessCard, AccessDeviceInfo, AccessDoor, AccessEvent, AccessUser, DahuaAccessClient
from .dahua.normalizer import (
    controller_entity_name,
    door_id_to_sdk_channel,
    mask_card_number,
    resolve_door_label,
    tag_id_for_card,
)

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
        self.users_by_card: dict[str, AccessUser] = {}
        self.cards_by_number: dict[str, AccessCard] = {}
        self._identity_task: asyncio.Task[None] | None = None
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
        self._identity_task = self.hass.async_create_task(self._async_refresh_identities())

    async def async_stop(self) -> None:
        """Stop listening and disconnect."""
        if self._identity_task:
            self._identity_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._identity_task
        await self.hass.async_add_executor_job(self.client.stop_listening)
        await self.hass.async_add_executor_job(self.client.disconnect)
        self.online = False

    async def async_open_door(self, door_id: int, direction: str = "unknown") -> None:
        """Open one door remotely."""
        door = self.doors[door_id]
        await self.hass.async_add_executor_job(self.client.open_door, door.sdk_channel, direction)

    def entity_name_for_door(self, door_id: int) -> str:
        """Return controller-prefixed entity name for a door."""
        door = self.doors.get(door_id)
        label = door.label if door else resolve_door_label(door_id)
        return controller_entity_name(self.device_info.name, label)

    def device_info_payload(self) -> dict[str, Any]:
        """Return HA device_info payload shared by entities."""
        payload = {
            "identifiers": {self.device_identifier},
            "manufacturer": self.device_info.manufacturer,
            "name": self.device_info.name,
            "model": self.device_info.model or None,
            "serial_number": self.device_info.serial,
            "configuration_url": f"http://{self.entry.data.get('host')}",
        }
        if self.device_info.firmware:
            payload["sw_version"] = self.device_info.firmware
        if self.device_info.hardware:
            payload["hw_version"] = self.device_info.hardware
        return payload

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
            await self._async_scan_tag(enriched)

        async_dispatcher_send(self.hass, self.signal_update)

    async def _async_enrich_event(self, event: AccessEvent) -> AccessEvent:
        door_label = event.door_label
        if event.door_id:
            stored = self.doors.get(event.door_id)
            label = resolve_door_label(event.door_id, event.door_name, stored.label if stored else "")
            if event.door_name and (stored is None or stored.label != event.door_name):
                self.doors[event.door_id] = AccessDoor(
                    event.door_id,
                    event.door_name,
                    int(event.raw.get("sdk_channel", door_id_to_sdk_channel(event.door_id))),
                    "event",
                )
            door_label = label

        user_id = event.user_id
        user_name = event.user_name
        card_name = event.card_name
        if event.card_number:
            card = self.cards_by_number.get(event.card_number)
            card_name = card_name or (card.name if card else "")
            if not user_id and card:
                user_id = card.user_id
            cached_by_card = self.users_by_card.get(event.card_number)
            if cached_by_card:
                user_id = user_id or cached_by_card.user_id
                user_name = user_name or cached_by_card.name

        if user_id and not user_name:
            cached = self.users_by_id.get(user_id)
            if cached:
                user_name = cached.name
            else:
                try:
                    user = await asyncio.wait_for(
                        self.hass.async_add_executor_job(self.client.get_user, user_id),
                        timeout=1.5,
                    )
                except TimeoutError:
                    user = None
                except Exception:
                    _LOGGER.debug("Failed to resolve Dahua user %s", user_id, exc_info=True)
                    user = None
                if user:
                    self.users_by_id[user.user_id] = user
                    user_name = user.name

        if event.card_number and not user_name and not user_id:
            try:
                user = await asyncio.wait_for(
                    self.hass.async_add_executor_job(self.client.get_user_by_card, event.card_number),
                    timeout=2.0,
                )
            except TimeoutError:
                user = None
            except Exception:
                _LOGGER.debug("Failed to resolve Dahua card %s", event.card_number, exc_info=True)
                user = None
            if user:
                self.users_by_id[user.user_id] = user
                self.users_by_card[event.card_number] = user
                user_id = user.user_id
                user_name = user.name

        return replace(
            event,
            door_label=door_label,
            user_id=user_id,
            user_name=user_name,
            card_name=card_name,
        )

    async def _async_refresh_identities(self) -> None:
        """Warm user/card caches and create readable native HA tags."""
        try:
            cards = await self.hass.async_add_executor_job(self.client.list_cards)
            self.cards_by_number = {card.card_number: card for card in cards}
            user_ids = [card.user_id for card in cards if card.user_id]
            users: list[AccessUser] = []
            for offset in range(0, len(user_ids), 100):
                users.extend(
                    await self.hass.async_add_executor_job(self.client.get_users, user_ids[offset : offset + 100])
                )
            self.users_by_id.update({user.user_id: user for user in users})
            for card in cards:
                if user := self.users_by_id.get(card.user_id):
                    self.users_by_card[card.card_number] = user
                tag_name = (user.name if user else "") or card.name or (f"ID {card.user_id}" if card.user_id else "")
                await self._async_ensure_tag(card.card_number, tag_name)
            async_dispatcher_send(self.hass, self.signal_update)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("Failed to preload Dahua users and cards", exc_info=True)

    async def _async_scan_tag(self, event: AccessEvent) -> None:
        tag_id = tag_id_for_card(event.card_number)
        await self._async_ensure_tag(event.card_number, event.person_label)
        device = dr.async_get(self.hass).async_get_device(identifiers={self.device_identifier})
        await ha_tag.async_scan_tag(self.hass, tag_id, device.id if device else None)

    async def _async_ensure_tag(self, card_number: str, name: str) -> None:
        """Create a native HA tag and name it without replacing a user-customized name."""
        tag_id = tag_id_for_card(card_number)
        storage = self.hass.data[ha_tag.TAG_DATA]
        if tag_id not in storage.data:
            create_data = {ha_tag.TAG_ID: tag_id}
            if name:
                create_data[CONF_NAME] = name
            await storage.async_create_item(create_data)
            return
        if not name:
            return
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(ha_tag.DOMAIN, ha_tag.DOMAIN, tag_id)
        if entity_id and (entry := registry.async_get(entity_id)) and entry.name is None:
            registry.async_update_entity(entity_id, name=name)

    @callback
    def _remember_event(self, event: AccessEvent) -> None:
        self.last_event = event
        if event.door_id and "door_status" in event.raw:
            self.last_door_status[event.door_id] = str(event.raw["door_status"])


def runtime_from_entry(hass: HomeAssistant, entry: ConfigEntry) -> DahuaAccessRuntime:
    """Return runtime data for a config entry."""
    return hass.data[DOMAIN][entry.entry_id]
