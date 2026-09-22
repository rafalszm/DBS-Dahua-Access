"""DBS Dahua Access integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import CONF_DEVICE_NAME, CONF_MODEL, DOMAIN, PLATFORMS
from .dahua import (
    AccessControllerConfig,
    DahuaAuthError,
    DahuaConnectionError,
    DahuaSdkUnavailable,
    NetSDKAccessClient,
)
from .runtime import DahuaAccessRuntime

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DBS Dahua Access from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    config = AccessControllerConfig(
        host=entry.data[CONF_HOST],
        port=int(entry.data[CONF_PORT]),
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )
    client = NetSDKAccessClient(config, name_hint=entry.data.get(CONF_DEVICE_NAME, entry.title))

    try:
        device_info = await hass.async_add_executor_job(client.connect)
    except DahuaAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except DahuaSdkUnavailable as err:
        raise ConfigEntryNotReady(str(err)) from err
    except DahuaConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    updated_data = dict(entry.data)
    if device_info.name:
        updated_data[CONF_DEVICE_NAME] = device_info.name
    updated_data[CONF_MODEL] = device_info.model
    if updated_data != entry.data or (device_info.name and device_info.name != entry.title):
        hass.config_entries.async_update_entry(
            entry,
            title=device_info.name or entry.title,
            data=updated_data,
        )

    runtime = DahuaAccessRuntime(hass, entry, client, device_info)
    await runtime.async_start()
    hass.data[DOMAIN][entry.entry_id] = runtime
    _remove_stale_door_entities(hass, entry, runtime.device_info.serial, set(runtime.doors))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a DBS Dahua Access config entry."""
    runtime = hass.data[DOMAIN].pop(entry.entry_id)
    await runtime.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _remove_stale_door_entities(hass: HomeAssistant, entry: ConfigEntry, serial: str, door_ids: set[int]) -> None:
    """Remove door entities left behind by earlier over-broad probing."""
    registry = er.async_get(hass)
    valid_unique_ids = {
        f"{serial}_door_{door_id}_open"
        for door_id in door_ids
    } | {
        f"{serial}_door_{door_id}_door"
        for door_id in door_ids
    }
    stale_prefix = f"{serial}_door_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = str(entity.unique_id)
        if unique_id.startswith(stale_prefix) and unique_id not in valid_unique_ids:
            registry.async_remove(entity.entity_id)
