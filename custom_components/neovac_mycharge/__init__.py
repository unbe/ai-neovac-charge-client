"""The NeoVac myCharge V integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NeoVacApiError, NeoVacAsyncClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CHARGE_POINT_ID,
    CONF_REFRESH_TOKEN,
    DEFAULT_REPORT_DAYS,
    DOMAIN,
    SERVICE_ADD_RFID,
    SERVICE_DELETE_RFID,
    SERVICE_DOWNLOAD_INVOICE_PDF,
    SERVICE_RENAME_RFID,
    SERVICE_REQUEST_EMAIL_REPORT,
    SERVICE_START_CHARGING,
    SERVICE_STOP_CHARGING,
)
from .coordinator import NeoVacDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.TEXT,
    Platform.EVENT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up NeoVac myCharge V from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    session = async_get_clientsession(hass)

    @callback
    def _async_persist_tokens(access_token: str, refresh_token: str) -> None:
        """Persist refreshed OAuth2 tokens into the config entry."""
        new_data = {
            **entry.data,
            CONF_ACCESS_TOKEN: access_token,
            CONF_REFRESH_TOKEN: refresh_token,
        }
        hass.config_entries.async_update_entry(entry, data=new_data)

    client = NeoVacAsyncClient(
        session=session,
        access_token=entry.data.get(CONF_ACCESS_TOKEN, ""),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN, ""),
        token_update_callback=_async_persist_tokens,
    )

    coordinator = NeoVacDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    _async_register_services(hass)
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: NeoVacDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_stop_websocket_listeners()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


def _get_target_coordinator_and_cp(
    hass: HomeAssistant, charge_point_id: int | None = None
) -> tuple[NeoVacDataUpdateCoordinator, int]:
    """Resolves the coordinator and charge_point_id for a service call."""
    coordinators: dict[str, NeoVacDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise HomeAssistantError("No NeoVac myCharge V entries configured.")

    if charge_point_id is not None:
        target_id = int(charge_point_id)
        for coord in coordinators.values():
            if coord.data and target_id in coord.data.get("charge_points", {}):
                return coord, target_id

    first_coord = next(iter(coordinators.values()))
    cps = first_coord.data.get("charge_points", {}) if first_coord.data else {}
    if charge_point_id is not None:
        return first_coord, int(charge_point_id)
    if cps:
        return first_coord, next(iter(cps.keys()))
    raise HomeAssistantError("No charge points discovered in NeoVac account.")


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Registers custom domain services for NeoVac myCharge V."""
    if hass.services.has_service(DOMAIN, SERVICE_START_CHARGING):
        return

    async def _handle_start_charging(call: ServiceCall) -> None:
        cp_id_opt = call.data.get(CONF_CHARGE_POINT_ID)
        rfid_tag = call.data.get("rfid_tag")
        coord, cp_id = _get_target_coordinator_and_cp(hass, cp_id_opt)
        if not rfid_tag:
            rfid_tag = coord.selected_rfid_tags.get(cp_id)
        try:
            await coord.client.async_start_charging(cp_id, rfid_tag=rfid_tag)
            await coord.async_request_refresh()
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err

    async def _handle_stop_charging(call: ServiceCall) -> None:
        cp_id_opt = call.data.get(CONF_CHARGE_POINT_ID)
        coord, cp_id = _get_target_coordinator_and_cp(hass, cp_id_opt)
        try:
            await coord.client.async_stop_charging(cp_id)
            await coord.async_request_refresh()
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err

    async def _handle_add_rfid(call: ServiceCall) -> None:
        coord, _ = _get_target_coordinator_and_cp(hass)
        try:
            await coord.client.async_add_rfid(
                rfid_tag=call.data["rfid_tag"],
                rfid_name=call.data["rfid_name"],
            )
            await coord.async_request_refresh()
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err

    async def _handle_rename_rfid(call: ServiceCall) -> None:
        coord, _ = _get_target_coordinator_and_cp(hass)
        try:
            await coord.client.async_update_rfid(
                rfid_id=int(call.data["rfid_id"]),
                rfid_name=call.data["rfid_name"],
            )
            await coord.async_request_refresh()
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err

    async def _handle_delete_rfid(call: ServiceCall) -> None:
        coord, _ = _get_target_coordinator_and_cp(hass)
        try:
            await coord.client.async_delete_rfid(rfid_id=int(call.data["rfid_id"]))
            await coord.async_request_refresh()
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err

    async def _handle_request_email_report(call: ServiceCall) -> None:
        cp_id_opt = call.data.get(CONF_CHARGE_POINT_ID)
        days = int(call.data.get("days", DEFAULT_REPORT_DAYS))
        earliest = call.data.get("earliest_date")
        latest = call.data.get("latest_date")
        coord, cp_id = _get_target_coordinator_and_cp(hass, cp_id_opt)
        try:
            await coord.client.async_request_email_report(
                cp_id, days=days, earliest_date=earliest, latest_date=latest
            )
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err

    async def _handle_download_invoice_pdf(call: ServiceCall) -> None:
        coord, _ = _get_target_coordinator_and_cp(hass)
        invoice_id = call.data["invoice_id"]
        target_path = Path(call.data["target_path"])
        try:
            pdf_bytes = await coord.client.async_download_invoice_pdf_bytes(invoice_id)
            await hass.async_add_executor_job(target_path.write_bytes, pdf_bytes)
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_CHARGING,
        _handle_start_charging,
        schema=vol.Schema(
            {
                vol.Optional(CONF_CHARGE_POINT_ID): cv.positive_int,
                vol.Optional("rfid_tag"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_CHARGING,
        _handle_stop_charging,
        schema=vol.Schema({vol.Optional(CONF_CHARGE_POINT_ID): cv.positive_int}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_RFID,
        _handle_add_rfid,
        schema=vol.Schema(
            {
                vol.Required("rfid_tag"): cv.string,
                vol.Required("rfid_name"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RENAME_RFID,
        _handle_rename_rfid,
        schema=vol.Schema(
            {
                vol.Required("rfid_id"): cv.positive_int,
                vol.Required("rfid_name"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_RFID,
        _handle_delete_rfid,
        schema=vol.Schema({vol.Required("rfid_id"): cv.positive_int}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_EMAIL_REPORT,
        _handle_request_email_report,
        schema=vol.Schema(
            {
                vol.Optional(CONF_CHARGE_POINT_ID): cv.positive_int,
                vol.Optional("days", default=DEFAULT_REPORT_DAYS): cv.positive_int,
                vol.Optional("earliest_date"): cv.string,
                vol.Optional("latest_date"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_INVOICE_PDF,
        _handle_download_invoice_pdf,
        schema=vol.Schema(
            {
                vol.Required("invoice_id"): cv.string,
                vol.Required("target_path"): cv.string,
            }
        ),
    )
