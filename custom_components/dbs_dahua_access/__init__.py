"""DBS Dahua Access integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import CONF_DEVICE_NAME, DOMAIN, PLATFORMS
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

    runtime = DahuaAccessRuntime(hass, entry, client, device_info)
    await runtime.async_start()
    hass.data[DOMAIN][entry.entry_id] = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a DBS Dahua Access config entry."""
    runtime = hass.data[DOMAIN].pop(entry.entry_id)
    await runtime.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
