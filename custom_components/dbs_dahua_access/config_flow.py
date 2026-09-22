"""Config flow for DBS Dahua Access."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_DEVICE_NAME,
    CONF_MASK_CARDS_IN_LOGS,
    CONF_MODEL,
    CONF_SERIAL,
    CONF_TAG_EVENTS,
    DEFAULT_MASK_CARDS_IN_LOGS,
    DEFAULT_PORT,
    DEFAULT_TAG_EVENTS,
    DOMAIN,
)
from .dahua import (
    AccessControllerConfig,
    AccessDeviceInfo,
    DahuaAuthError,
    DahuaConnectionError,
    DahuaSdkUnavailable,
    NetSDKAccessClient,
)


async def _async_validate_input(hass: HomeAssistant, user_input: dict[str, Any]) -> AccessDeviceInfo:
    """Validate controller credentials and return device info."""
    config = AccessControllerConfig(
        host=user_input[CONF_HOST],
        port=int(user_input[CONF_PORT]),
        username=user_input[CONF_USERNAME],
        password=user_input[CONF_PASSWORD],
    )
    client = NetSDKAccessClient(config, name_hint=user_input[CONF_HOST])
    try:
        return await hass.async_add_executor_job(client.connect)
    finally:
        await hass.async_add_executor_job(client.disconnect)


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


class DahuaAccessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a DBS Dahua Access config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                device = await _async_validate_input(self.hass, user_input)
            except DahuaAuthError:
                errors["base"] = "invalid_auth"
            except DahuaSdkUnavailable:
                errors["base"] = "sdk_unavailable"
            except DahuaConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device.serial)
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: int(user_input[CONF_PORT]),
                    }
                )
                data = {
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: int(user_input[CONF_PORT]),
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_SERIAL: device.serial,
                    CONF_DEVICE_NAME: device.name,
                    CONF_MODEL: device.model,
                }
                return self.async_create_entry(title=device.name, data=data)

        return self.async_show_form(step_id="user", data_schema=_schema(user_input), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Update host, port and credentials for an existing controller."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        defaults = {
            CONF_HOST: entry.data.get(CONF_HOST, ""),
            CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
            CONF_USERNAME: entry.data.get(CONF_USERNAME, ""),
        }

        if user_input is not None:
            try:
                device = await _async_validate_input(self.hass, user_input)
            except DahuaAuthError:
                errors["base"] = "invalid_auth"
            except DahuaSdkUnavailable:
                errors["base"] = "sdk_unavailable"
            except DahuaConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device.serial)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: int(user_input[CONF_PORT]),
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_DEVICE_NAME: device.name,
                        CONF_MODEL: device.model,
                    },
                )

        return self.async_show_form(step_id="reconfigure", data_schema=_schema(defaults), errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Start reauthentication."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Update credentials after auth failure."""
        entry = self._reauth_entry
        errors: dict[str, str] = {}
        defaults = {
            CONF_HOST: entry.data.get(CONF_HOST, ""),
            CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
            CONF_USERNAME: entry.data.get(CONF_USERNAME, ""),
        }

        if user_input is not None:
            try:
                device = await _async_validate_input(self.hass, user_input)
            except DahuaAuthError:
                errors["base"] = "invalid_auth"
            except DahuaSdkUnavailable:
                errors["base"] = "sdk_unavailable"
            except DahuaConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if device.serial != entry.unique_id:
                    return self.async_abort(reason="unique_id_mismatch")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: int(user_input[CONF_PORT]),
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                    reason="reauth_successful",
                )

        return self.async_show_form(step_id="reauth_confirm", data_schema=_schema(defaults), errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return DahuaAccessOptionsFlow(config_entry)


class DahuaAccessOptionsFlow(config_entries.OptionsFlow):
    """Handle DBS Dahua Access options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_TAG_EVENTS, default=options.get(CONF_TAG_EVENTS, DEFAULT_TAG_EVENTS)): bool,
                vol.Required(
                    CONF_MASK_CARDS_IN_LOGS,
                    default=options.get(CONF_MASK_CARDS_IN_LOGS, DEFAULT_MASK_CARDS_IN_LOGS),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
