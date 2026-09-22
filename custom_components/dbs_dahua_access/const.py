"""Constants for DBS Dahua Access."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform

DOMAIN = "dbs_dahua_access"

DEFAULT_PORT = 37777

CONF_SERIAL = "serial"
CONF_DEVICE_NAME = "device_name"
CONF_MODEL = "model"
CONF_TAG_EVENTS = "tag_events"
CONF_MASK_CARDS_IN_LOGS = "mask_cards_in_logs"

DEFAULT_TAG_EVENTS = True
DEFAULT_MASK_CARDS_IN_LOGS = True

EVENT_ACCESS = f"{DOMAIN}_event"
EVENT_TAG_SCANNED = "tag_scanned"

DATA_RUNTIME = "runtime"

PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
)

CONFIG_KEYS = (CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD)
