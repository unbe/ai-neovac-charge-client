"""Diagnostics support for NeoVac myCharge V."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from .coordinator import NeoVacDataUpdateCoordinator

TO_REDACT = {
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_ACCOUNT_ID,
    "accountId",
    "account_id",
    "siteId",
    "site_id",
    "serialNumber",
    "chargePointSerialNumber",
    "chargePointSerialNo",
    "serial_number",
    "rfidTag",
    "rfid_tag",
    "userEmailAddress",
    "email",
    "userId",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry with sensitive identifiers redacted."""
    coordinator: NeoVacDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinator_data": async_redact_data(coordinator.data or {}, TO_REDACT),
        "websocket_enabled": coordinator.enable_websocket,
    }
