"""DataUpdateCoordinator and real-time SignalR push manager for NeoVac myCharge V."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    NeoVacApiError,
    NeoVacAsyncClient,
    NeoVacAuthError,
    evaluate_state_transitions,
)
from .const import (
    CONF_ENABLE_WEBSOCKET,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_BUS_NAME,
    EVENT_CHARGING_STARTED,
    EVENT_CHARGING_STOPPED,
    EVENT_PRICE_UPDATED,
    EVENT_SETTINGS_CHANGED,
    EVENT_TRANSACTION_REPORT,
    HUB_CONNECTOR,
    HUB_EVENTS,
    STATUS_CHARGING,
    STATUS_FINISHING,
)

_LOGGER = logging.getLogger(__name__)


def _get_ci(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Case-insensitive / camelCase + PascalCase dictionary lookup helper."""
    if not isinstance(data, dict):
        return default
    for k in keys:
        if k in data and data[k] is not None:
            return data[k]
        # Try camelCase / PascalCase variants
        camel = k[0].lower() + k[1:] if k else k
        pascal = k[0].upper() + k[1:] if k else k
        if camel in data and data[camel] is not None:
            return data[camel]
        if pascal in data and data[pascal] is not None:
            return data[pascal]
    return default


class NeoVacDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages polling NeoVac REST endpoints and streaming real-time SignalR hubs."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: NeoVacAsyncClient,
    ) -> None:
        self.entry = entry
        self.client = client
        scan_seconds = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        self.enable_websocket: bool = entry.options.get(
            CONF_ENABLE_WEBSOCKET,
            entry.data.get(CONF_ENABLE_WEBSOCKET, True),
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=int(scan_seconds)),
        )

        self._stop_event = asyncio.Event()
        self._ws_tasks: list[asyncio.Task[None]] = []
        self._event_listeners: dict[int, list[Callable[[str, dict[str, Any]], None]]] = {}
        self._previous_statuses: dict[int, str] = {}
        self.selected_rfid_tags: dict[int, str | None] = {}
        self._live_transactions: dict[int, dict[str, Any]] = {}

    def register_event_listener(
        self,
        charge_point_id: int,
        listener: Callable[[str, dict[str, Any]], None],
    ) -> Callable[[], None]:
        """Registers an EventEntity listener for a specific charge point."""
        listeners = self._event_listeners.setdefault(int(charge_point_id), [])
        listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in listeners:
                listeners.remove(listener)

        return _remove

    @callback
    def _emit_event(
        self,
        charge_point_id: int,
        event_type: str,
        event_data: dict[str, Any] | None = None,
    ) -> None:
        """Dispatches an event to both HA EventEntity listeners and the HA Event Bus."""
        cp_id = int(charge_point_id)
        payload = {
            "charge_point_id": cp_id,
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(event_data or {}),
        }

        if self.data and cp_id in self.data.get("charge_points", {}):
            self.data["charge_points"][cp_id]["last_event"] = payload

        # 1. Fire generic and specific events on the Home Assistant Event Bus
        self.hass.bus.async_fire(EVENT_BUS_NAME, payload)
        self.hass.bus.async_fire(f"{DOMAIN}_{event_type}", payload)

        # 2. Notify EventEntity instances for this charge point
        for listener in self._event_listeners.get(cp_id, []):
            try:
                listener(event_type, payload)
            except Exception as err:
                _LOGGER.error("Error in event listener for CP %s (%s): %s", cp_id, event_type, err, exc_info=True)

    def _check_and_emit_status_transitions(
        self,
        cp_id: int,
        new_status: str | None,
        error_code: str | None = None,
        error_charging: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Evaluates status transitions and fires corresponding lifecycle events."""
        prev_status = self._previous_statuses.get(cp_id)
        events = evaluate_state_transitions(
            previous_status=prev_status,
            new_status=new_status,
            error_code=error_code,
            error_charging=error_charging,
        )
        if new_status:
            self._previous_statuses[cp_id] = new_status

        for evt in events:
            self._emit_event(
                cp_id,
                evt,
                {
                    "previous_status": prev_status,
                    "new_status": new_status,
                    **(extra or {}),
                },
            )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetches full state across REST endpoints."""
        try:
            user_info = await self.client.async_get_user_info()
        except NeoVacAuthError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except (NeoVacApiError, Exception) as err:
            raise UpdateFailed(f"Error communicating with NeoVac API: {err}") from err

        # Fetch account-level resources (RFIDs, invoices, notification settings)
        rfids: list[dict[str, Any]] = []
        invoices: list[dict[str, Any]] = []
        notification_settings: dict[str, Any] = {}

        results = await asyncio.gather(
            self.client.async_list_rfids(),
            self.client.async_list_invoices(),
            self.client.async_get_notification_settings(),
            return_exceptions=True,
        )
        if isinstance(results[0], Exception):
            _LOGGER.warning("Error fetching RFIDs: %s", results[0])
        elif isinstance(results[0], list):
            rfids = results[0]

        if isinstance(results[1], Exception):
            _LOGGER.warning("Error fetching invoices: %s", results[1])
        elif isinstance(results[1], list):
            invoices = results[1]

        if isinstance(results[2], Exception):
            _LOGGER.warning("Error fetching notification settings: %s", results[2])
        elif isinstance(results[2], dict):
            notification_settings = results[2]

        charge_points: dict[int, dict[str, Any]] = {}
        sites = _get_ci(user_info, "sites", default=[]) or []

        for site in sites:
            site_id = _get_ci(site, "siteId", default="")
            site_name = _get_ci(site, "siteName", "name", default="")
            cps = _get_ci(site, "userChargePoints", default=[]) or []

            for cp in cps:
                raw_cp_id = _get_ci(cp, "chargePointId")
                if raw_cp_id is None:
                    continue
                cp_id = int(raw_cp_id)

                conn_num = int(_get_ci(cp, "connectorNumber", default=1) or 1)
                # Fetch per-station report, analytics, pricing, and guest invitations concurrently
                cp_tasks = await asyncio.gather(
                    self.client.async_get_charge_report(cp_id),
                    self.client.async_get_analytics(cp_id, resolution="Month"),
                    self.client.async_get_pricing(cp_id, conn_num),
                    self.client.async_get_guest_invitations(cp_id),
                    return_exceptions=True,
                )
                if isinstance(cp_tasks[0], Exception):
                    _LOGGER.warning("Error fetching charge report for station %s: %s", cp_id, cp_tasks[0])
                    report = {}
                elif isinstance(cp_tasks[0], dict):
                    report = cp_tasks[0]
                else:
                    report = {}

                if isinstance(cp_tasks[1], Exception):
                    _LOGGER.warning("Error fetching analytics for station %s: %s", cp_id, cp_tasks[1])
                    analytics = {}
                elif isinstance(cp_tasks[1], dict):
                    analytics = cp_tasks[1]
                else:
                    analytics = {}

                if isinstance(cp_tasks[2], Exception):
                    _LOGGER.warning("Error fetching pricing for station %s: %s", cp_id, cp_tasks[2])
                    pricing = {}
                elif isinstance(cp_tasks[2], dict):
                    pricing = cp_tasks[2]
                else:
                    pricing = {}

                if isinstance(cp_tasks[3], Exception):
                    _LOGGER.warning("Error fetching guest invitations for station %s: %s", cp_id, cp_tasks[3])
                    invitations = []
                elif isinstance(cp_tasks[3], list):
                    invitations = cp_tasks[3]
                else:
                    invitations = []

                status = _get_ci(cp, "status") or _get_ci(cp, "connectorZeroStatus")
                self._check_and_emit_status_transitions(cp_id, status)

                existing_cp = (
                    self.data.get("charge_points", {}).get(cp_id, {})
                    if self.data
                    else {}
                )

                # Check if station metadata contains active transaction details
                active_tx = _get_ci(cp, "activeTransaction", "transaction", "currentTransaction")
                if isinstance(active_tx, dict) and not self._live_transactions.get(cp_id):
                    self._live_transactions[cp_id] = active_tx

                charge_points[cp_id] = {
                    "info": {
                        **cp,
                        "siteId": site_id,
                        "siteName": site_name,
                    },
                    "live_transaction": self._live_transactions.get(cp_id, {}),
                    "report": report,
                    "analytics": analytics,
                    "pricing": pricing,
                    "guest_invitations": invitations,
                    "last_event": existing_cp.get("last_event"),
                }

        # Ensure background SignalR WebSocket tasks are running if enabled
        if self.enable_websocket and charge_points and not self._ws_tasks:
            self.async_start_websocket_listeners(list(charge_points.keys()))

        return {
            "account_id": _get_ci(user_info, "accountId", default=""),
            "user_info": user_info,
            "charge_points": charge_points,
            "rfids": rfids,
            "invoices": invoices,
            "notification_settings": notification_settings,
        }

    # -------------------------------------------------------------------------
    # Real-Time SignalR WebSocket Push Management
    # -------------------------------------------------------------------------

    @callback
    def async_start_websocket_listeners(self, charge_point_ids: list[int]) -> None:
        """Starts background SignalR WebSocket tasks for StatusHub and ConnectorHub."""
        if self._ws_tasks:
            return
        self._stop_event.clear()
        for hub in (HUB_EVENTS, HUB_CONNECTOR):
            task = self.entry.async_create_background_task(
                self.hass,
                self._async_hub_loop(hub, charge_point_ids),
                name=f"neovac_signalr_{hub}",
            )
            self._ws_tasks.append(task)

    async def async_stop_websocket_listeners(self) -> None:
        """Stops all running SignalR WebSocket tasks."""
        self._stop_event.set()
        for task in self._ws_tasks:
            task.cancel()
        if self._ws_tasks:
            await asyncio.gather(*self._ws_tasks, return_exceptions=True)
        self._ws_tasks.clear()

    async def _async_hub_loop(
        self, hub_name: str, charge_point_ids: list[int]
    ) -> None:
        """Persistent reconnect loop for a SignalR hub."""
        backoff = 5
        while not self._stop_event.is_set():
            try:
                await self.client.async_stream_signalr_hub(
                    hub_name=hub_name,
                    event_callback=self.async_handle_signalr_event,
                    charge_point_ids=charge_point_ids,
                    stop_event=self._stop_event,
                )
                backoff = 5
            except asyncio.CancelledError:
                break
            except NeoVacAuthError as err:
                _LOGGER.warning(
                    "SignalR auth token expired on hub '%s' (%s), attempting refresh",
                    hub_name,
                    err,
                )
                try:
                    await self.client.async_refresh_access_token()
                except Exception as refresh_err:
                    _LOGGER.error(
                        "SignalR auth refresh failed on hub '%s': %s",
                        hub_name,
                        refresh_err,
                        exc_info=True,
                    )
            except Exception as err:
                _LOGGER.warning(
                    "SignalR hub '%s' disconnected (%s), reconnecting in %ss",
                    hub_name,
                    err,
                    backoff,
                )

            if not self._stop_event.is_set():
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(backoff * 2, 60)

    async def async_handle_signalr_event(
        self, target: str, arguments: list[Any]
    ) -> None:
        """Processes incoming real-time SignalR invocation messages."""
        if not self.data or not arguments:
            return

        try:
            first_arg = arguments[0] if isinstance(arguments[0], dict) else {}
            charge_points = self.data.get("charge_points", {})
            if not charge_points:
                return

            # Resolve target charge_point_id from payload (or default to single station)
            raw_cp_id = _get_ci(first_arg, "chargePointId")
            serial = _get_ci(
                first_arg, "chargePointSerialNumber", "chargePointSerialNo", "serialNumber"
            )
            cp_id: int | None = int(raw_cp_id) if raw_cp_id is not None else None

            if cp_id is None and serial:
                for candidate_id, cp_bundle in charge_points.items():
                    if _get_ci(cp_bundle.get("info", {}), "serialNumber") == serial:
                        cp_id = candidate_id
                        break

            if cp_id is None and len(charge_points) == 1:
                cp_id = next(iter(charge_points.keys()))

            if cp_id is None or cp_id not in charge_points:
                return

            cp_bundle = charge_points[cp_id]
            cp_info = cp_bundle["info"]

            if target == "OnTransactionReport":
                live_tx = {**self._live_transactions.get(cp_id, {}), **first_arg}
                has_end = bool(_get_ci(first_arg, "end", "hasEnd", "transactionEnded", default=False))
                tx_status = _get_ci(first_arg, "status", "ocppStatus")

                if has_end:
                    live_tx["currentPowerKw"] = 0.0
                    self._live_transactions[cp_id] = live_tx
                    cp_bundle["live_transaction"] = live_tx
                    self._check_and_emit_status_transitions(
                        cp_id,
                        tx_status or STATUS_FINISHING,
                        extra={"transaction_id": _get_ci(first_arg, "transactionId")},
                    )
                    self._emit_event(cp_id, EVENT_CHARGING_STOPPED, live_tx)
                    self.async_set_updated_data(self.data)
                    # Refresh historical reports so last session & 30-day totals update
                    self.hass.async_create_task(self.async_request_refresh())
                    return

                if tx_status:
                    cp_info["status"] = tx_status
                    cp_info["connectorZeroStatus"] = tx_status
                elif _get_ci(first_arg, "currentPowerKw", default=0) > 0:
                    cp_info["status"] = STATUS_CHARGING

                self._live_transactions[cp_id] = live_tx
                cp_bundle["live_transaction"] = live_tx

                self._check_and_emit_status_transitions(
                    cp_id,
                    cp_info.get("status"),
                    error_code=_get_ci(first_arg, "errorCode"),
                    error_charging=bool(_get_ci(first_arg, "errorCharging", default=False)),
                    extra=live_tx,
                )
                self._emit_event(cp_id, EVENT_TRANSACTION_REPORT, live_tx)
                self.async_set_updated_data(self.data)

            elif target == "StartTransactionNotification":
                cp_info["status"] = STATUS_CHARGING
                self._check_and_emit_status_transitions(cp_id, STATUS_CHARGING, extra=first_arg)
                self._emit_event(cp_id, EVENT_CHARGING_STARTED, first_arg)
                self.async_set_updated_data(self.data)

            elif target == "StopTransactionNotification":
                if cp_id in self._live_transactions:
                    self._live_transactions[cp_id]["currentPowerKw"] = 0.0
                self._check_and_emit_status_transitions(cp_id, STATUS_FINISHING, extra=first_arg)
                self._emit_event(cp_id, EVENT_CHARGING_STOPPED, first_arg)
                self.async_set_updated_data(self.data)
                self.hass.async_create_task(self.async_request_refresh())

            elif target == "PriceUpdate":
                cp_bundle["pricing"] = {**cp_bundle.get("pricing", {}), **first_arg}
                self._emit_event(cp_id, EVENT_PRICE_UPDATED, first_arg)
                self.async_set_updated_data(self.data)

            elif target in (
                "ReceiveConnectorTransactionUpdate",
                "ReceiveChargePointSiteStatusUpdate",
                "ReceiveChargePointSiteStatusFullUpdate",
                "ConnectorChanged",
                "StatusNotificationReceivedIntegrationEvent",
            ):
                new_status = _get_ci(first_arg, "status", "ocppStatus", "connectorState")
                if new_status:
                    cp_info["status"] = new_status
                    cp_info["connectorZeroStatus"] = new_status
                    if new_status != STATUS_CHARGING and cp_id in self._live_transactions:
                        self._live_transactions[cp_id]["currentPowerKw"] = 0.0
                is_online = _get_ci(first_arg, "isOnline")
                if is_online is not None:
                    cp_info["isOnline"] = bool(is_online)

                self._check_and_emit_status_transitions(
                    cp_id,
                    new_status or cp_info.get("status"),
                    error_code=_get_ci(first_arg, "errorCode"),
                    error_charging=bool(_get_ci(first_arg, "errorCharging", default=False)),
                    extra=first_arg,
                )
                self.async_set_updated_data(self.data)

            elif target in (
                "ConnectorChangedPlugAndCharge",
                "ConnectorChangedNightCharging",
                "ConnectorChangedPermanentCableLock",
                "ChargePointChangedPermanentCableLock",
            ):
                settings = cp_info.setdefault("chargePointSettings", {})
                pnc = _get_ci(first_arg, "plugAndCharge", "plugAndChargeEnabled")
                if pnc is not None:
                    settings["plugAndChargeEnabled"] = bool(pnc)
                night = _get_ci(first_arg, "newNightCharging", "nightChargingEnabled")
                if night is not None:
                    settings["nightChargingEnabled"] = bool(night)
                lock = _get_ci(first_arg, "permanentCableLock", "permanentCableLockEnabled")
                if lock is not None:
                    settings["permanentCableLockEnabled"] = bool(lock)

                self._emit_event(cp_id, EVENT_SETTINGS_CHANGED, first_arg)
                self.async_set_updated_data(self.data)
        except Exception as err:
            _LOGGER.error(
                "Error processing SignalR event '%s' with arguments %s: %s",
                target,
                arguments,
                err,
                exc_info=True,
            )
