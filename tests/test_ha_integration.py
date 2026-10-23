"""Comprehensive test suite for the NeoVac myCharge V Home Assistant integration and CLI."""

from __future__ import annotations

import ast
import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

from aiohttp import WSMsgType, web

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


def _install_ha_stubs() -> None:
    """Installs lightweight Home Assistant module stubs so all platform modules can be imported and tested standalone."""
    if "homeassistant" in sys.modules:
        return

    ha = types.ModuleType("homeassistant")
    ha_config_entries = types.ModuleType("homeassistant.config_entries")
    ha_const = types.ModuleType("homeassistant.const")
    ha_core = types.ModuleType("homeassistant.core")
    ha_exceptions = types.ModuleType("homeassistant.exceptions")
    ha_data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    ha_util = types.ModuleType("homeassistant.util")
    ha_util_dt = types.ModuleType("homeassistant.util.dt")
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_helpers_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
    ha_helpers_cv = types.ModuleType("homeassistant.helpers.config_validation")
    ha_helpers_dr = types.ModuleType("homeassistant.helpers.device_registry")
    ha_helpers_ep = types.ModuleType("homeassistant.helpers.entity_platform")
    ha_helpers_uc = types.ModuleType("homeassistant.helpers.update_coordinator")
    ha_components = types.ModuleType("homeassistant.components")
    ha_comp_sensor = types.ModuleType("homeassistant.components.sensor")
    ha_comp_bs = types.ModuleType("homeassistant.components.binary_sensor")
    ha_comp_switch = types.ModuleType("homeassistant.components.switch")
    ha_comp_select = types.ModuleType("homeassistant.components.select")
    ha_comp_button = types.ModuleType("homeassistant.components.button")
    ha_comp_text = types.ModuleType("homeassistant.components.text")
    ha_comp_event = types.ModuleType("homeassistant.components.event")
    ha_comp_diag = types.ModuleType("homeassistant.components.diagnostics")

    # Stubs for voluptuous if not installed
    if "voluptuous" not in sys.modules:
        vol = types.ModuleType("voluptuous")
        vol.Schema = lambda s, **kw: s
        vol.Required = lambda k, default=None: k
        vol.Optional = lambda k, default=None: k
        vol.In = lambda x: x
        vol.All = lambda *x: x
        vol.Coerce = lambda t: t
        vol.Range = lambda **kw: kw
        sys.modules["voluptuous"] = vol

    class ConfigEntry:
        def __init__(self, data: dict[str, Any] | None = None, options: dict[str, Any] | None = None) -> None:
            self.entry_id = "test_entry_id"
            self.data = data or {}
            self.options = options or {}

        def as_dict(self) -> dict[str, Any]:
            return {"entry_id": self.entry_id, "data": self.data, "options": self.options}

        def async_create_background_task(self, hass: Any, coro: Any, name: str = "") -> asyncio.Task[Any]:
            return asyncio.create_task(coro)

        def add_update_listener(self, listener: Any) -> Any:
            return lambda: None

        def async_on_unload(self, func: Any) -> None:
            pass

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs: Any) -> None:
            pass

    class OptionsFlow:
        pass

    ha_config_entries.ConfigEntry = ConfigEntry
    ha_config_entries.ConfigFlow = ConfigFlow
    ha_config_entries.OptionsFlow = OptionsFlow

    class Platform:
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        SWITCH = "switch"
        SELECT = "select"
        BUTTON = "button"
        TEXT = "text"
        EVENT = "event"

    class EntityCategory:
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"

    class UnitOfPower:
        KILO_WATT = "kW"

    class UnitOfEnergy:
        KILO_WATT_HOUR = "kWh"

    class UnitOfTime:
        SECONDS = "s"

    ha_const.Platform = Platform
    ha_const.EntityCategory = EntityCategory
    ha_const.UnitOfPower = UnitOfPower
    ha_const.UnitOfEnergy = UnitOfEnergy
    ha_const.UnitOfTime = UnitOfTime

    class HomeAssistant:
        def __init__(self) -> None:
            self.data: dict[str, Any] = {}
            self.fired_events: list[tuple[str, dict[str, Any]]] = []
            self.bus = types.SimpleNamespace(
                async_fire=lambda event_type, event_data=None: self.fired_events.append(
                    (event_type, event_data or {})
                )
            )
            self.services = types.SimpleNamespace(
                has_service=lambda domain, srv: False,
                async_register=lambda *args, **kwargs: None,
            )
            self.config_entries = types.SimpleNamespace(
                async_update_entry=lambda entry, data=None: entry.data.update(data or {}),
                async_forward_entry_setups=lambda entry, platforms: asyncio.sleep(0),
                async_unload_platforms=lambda entry, platforms: asyncio.sleep(0, result=True),
                async_reload=lambda entry_id: asyncio.sleep(0),
            )

        def async_create_task(self, coro: Any) -> asyncio.Task[Any]:
            return asyncio.create_task(coro)

        async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
            return func(*args)

    ha_core.HomeAssistant = HomeAssistant
    ha_core.ServiceCall = object
    ha_core.callback = lambda fn: fn

    class HomeAssistantError(Exception):
        pass

    class ConfigEntryAuthFailed(HomeAssistantError):
        pass

    class UpdateFailed(HomeAssistantError):
        pass

    ha_exceptions.HomeAssistantError = HomeAssistantError
    ha_exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    ha_data_entry_flow.FlowResult = dict

    ha_util_dt.parse_datetime = lambda s: (
        datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    )
    ha_util_dt.utcnow = lambda: datetime.now(timezone.utc)
    ha_util_dt.as_utc = lambda dt: dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    ha_util.dt = ha_util_dt

    ha_helpers_aiohttp.async_get_clientsession = lambda hass: None
    ha_helpers_cv.positive_int = int
    ha_helpers_cv.string = str
    ha_helpers_dr.DeviceInfo = lambda **kwargs: kwargs
    ha_helpers_ep.AddEntitiesCallback = Any

    class DataUpdateCoordinator:
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

        def __init__(self, hass: Any, logger: Any, name: str = "", update_interval: Any = None) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data: Any = None
            self.last_update_success: bool = True

        def async_set_updated_data(self, data: Any) -> None:
            self.data = data

        async def async_request_refresh(self) -> None:
            self.data = await self._async_update_data()

        async def async_config_entry_first_refresh(self) -> None:
            self.data = await self._async_update_data()

    class CoordinatorEntity:
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

        def __init__(self, coordinator: Any) -> None:
            self.coordinator = coordinator
            self._remove_callbacks: list[Any] = []

        @property
        def available(self) -> bool:
            return getattr(self.coordinator, "last_update_success", True)

        def async_on_remove(self, fn: Any) -> None:
            self._remove_callbacks.append(fn)

        async def async_added_to_hass(self) -> None:
            pass

        def async_write_ha_state(self) -> None:
            pass

    ha_helpers_uc.DataUpdateCoordinator = DataUpdateCoordinator
    ha_helpers_uc.CoordinatorEntity = CoordinatorEntity
    ha_helpers_uc.UpdateFailed = UpdateFailed

    # Entity descriptions and base classes
    @dataclass(frozen=True, kw_only=True)
    class _BaseDesc:
        key: str
        translation_key: str | None = None
        name: str | None = None
        icon: str | None = None
        device_class: str | None = None
        entity_category: str | None = None
        entity_registry_visible_default: bool = True

    @dataclass(frozen=True, kw_only=True)
    class SensorEntityDescription(_BaseDesc):
        native_unit_of_measurement: str | None = None
        state_class: str | None = None
        suggested_display_precision: int | None = None
        options: list[str] | None = None

    class SensorDeviceClass:
        ENUM = "enum"
        POWER = "power"
        ENERGY = "energy"
        MONETARY = "monetary"
        DURATION = "duration"
        TIMESTAMP = "timestamp"

    class SensorStateClass:
        MEASUREMENT = "measurement"
        TOTAL = "total"
        TOTAL_INCREASING = "total_increasing"

    class SensorEntity:
        pass

    ha_comp_sensor.SensorEntityDescription = SensorEntityDescription
    ha_comp_sensor.SensorDeviceClass = SensorDeviceClass
    ha_comp_sensor.SensorStateClass = SensorStateClass
    ha_comp_sensor.SensorEntity = SensorEntity

    @dataclass(frozen=True, kw_only=True)
    class BinarySensorEntityDescription(_BaseDesc):
        pass

    class BinarySensorDeviceClass:
        CONNECTIVITY = "connectivity"
        PLUG = "plug"
        BATTERY_CHARGING = "battery_charging"
        PROBLEM = "problem"

    class BinarySensorEntity:
        pass

    ha_comp_bs.BinarySensorEntityDescription = BinarySensorEntityDescription
    ha_comp_bs.BinarySensorDeviceClass = BinarySensorDeviceClass
    ha_comp_bs.BinarySensorEntity = BinarySensorEntity

    @dataclass(frozen=True, kw_only=True)
    class SwitchEntityDescription(_BaseDesc):
        pass

    class SwitchDeviceClass:
        SWITCH = "switch"

    class SwitchEntity:
        pass

    ha_comp_switch.SwitchEntityDescription = SwitchEntityDescription
    ha_comp_switch.SwitchDeviceClass = SwitchDeviceClass
    ha_comp_switch.SwitchEntity = SwitchEntity

    class SelectEntity:
        pass

    ha_comp_select.SelectEntity = SelectEntity

    @dataclass(frozen=True, kw_only=True)
    class ButtonEntityDescription(_BaseDesc):
        pass

    class ButtonEntity:
        pass

    ha_comp_button.ButtonEntityDescription = ButtonEntityDescription
    ha_comp_button.ButtonEntity = ButtonEntity

    class TextEntity:
        pass

    ha_comp_text.TextEntity = TextEntity

    class EventEntity:
        _attr_event_types: list[str] = []

        @property
        def event_types(self) -> list[str]:
            return getattr(self, "_attr_event_types", [])

        def _trigger_event(self, event_type: str, event_attributes: dict[str, Any] | None = None) -> None:
            self._last_triggered_event = (event_type, event_attributes or {})

    ha_comp_event.EventEntity = EventEntity

    def async_redact_data(data: Any, to_redact: set[str]) -> Any:
        if isinstance(data, dict):
            return {
                k: ("**REDACTED**" if k in to_redact else async_redact_data(v, to_redact))
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [async_redact_data(i, to_redact) for i in data]
        return data

    ha_comp_diag.async_redact_data = async_redact_data

    for name, mod in {
        "homeassistant": ha,
        "homeassistant.config_entries": ha_config_entries,
        "homeassistant.const": ha_const,
        "homeassistant.core": ha_core,
        "homeassistant.exceptions": ha_exceptions,
        "homeassistant.data_entry_flow": ha_data_entry_flow,
        "homeassistant.util": ha_util,
        "homeassistant.util.dt": ha_util_dt,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.aiohttp_client": ha_helpers_aiohttp,
        "homeassistant.helpers.config_validation": ha_helpers_cv,
        "homeassistant.helpers.device_registry": ha_helpers_dr,
        "homeassistant.helpers.entity_platform": ha_helpers_ep,
        "homeassistant.helpers.update_coordinator": ha_helpers_uc,
        "homeassistant.components": ha_components,
        "homeassistant.components.sensor": ha_comp_sensor,
        "homeassistant.components.binary_sensor": ha_comp_bs,
        "homeassistant.components.switch": ha_comp_switch,
        "homeassistant.components.select": ha_comp_select,
        "homeassistant.components.button": ha_comp_button,
        "homeassistant.components.text": ha_comp_text,
        "homeassistant.components.event": ha_comp_event,
        "homeassistant.components.diagnostics": ha_comp_diag,
    }.items():
        sys.modules[name] = mod


_install_ha_stubs()

from custom_components.neovac_mycharge import (
    binary_sensor,
    button,
    config_flow,
    const,
    coordinator,
    diagnostics,
    event,
    select,
    sensor,
    switch,
    text,
)
from custom_components.neovac_mycharge.api import (
    NeoVacApiError,
    NeoVacAsyncClient,
    evaluate_state_transitions,
)


class TestNeoVacIntegration(unittest.IsolatedAsyncioTestCase):
    """Tests for HACS structure, API client, SignalR WebSockets, Coordinator, and Entities."""

    def test_manifests_and_translations_valid_json(self) -> None:
        """Verify hacs.json, manifest.json, strings.json, and translations are valid JSON."""
        hacs_data = json.loads((ROOT_DIR / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs_data["name"], "NeoVac myCharge V")

        comp_dir = ROOT_DIR / "custom_components" / "neovac_mycharge"
        manifest = json.loads((comp_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["domain"], "neovac_mycharge")
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["iot_class"], "cloud_push")

        strings = json.loads((comp_dir / "strings.json").read_text(encoding="utf-8"))
        en = json.loads((comp_dir / "translations" / "en.json").read_text(encoding="utf-8"))
        de = json.loads((comp_dir / "translations" / "de.json").read_text(encoding="utf-8"))
        self.assertIn("entity", strings)
        self.assertIn("entity", en)
        self.assertIn("entity", de)

    def test_all_python_files_parse_cleanly(self) -> None:
        """Ensure every Python file in the project parses without syntax errors."""
        py_files = list((ROOT_DIR / "custom_components" / "neovac_mycharge").glob("*.py"))
        py_files.append(ROOT_DIR / "neovac_client.py")
        for path in py_files:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_state_transition_engine(self) -> None:
        """Verify all charging lifecycle state transitions emit the right events."""
        # Initial load (previous=None) emits nothing
        self.assertEqual(evaluate_state_transitions(None, "Available"), [])

        # Plug in cable (NeoVac backend uses "Connected")
        self.assertEqual(
            evaluate_state_transitions("Available", "Connected"),
            [const.EVENT_CABLE_PLUGGED_IN],
        )
        # Case insensitivity check
        self.assertEqual(
            evaluate_state_transitions("available", "connected"),
            [const.EVENT_CABLE_PLUGGED_IN],
        )
        # Occupied plug in
        self.assertEqual(
            evaluate_state_transitions("Available", "Occupied"),
            [const.EVENT_CABLE_PLUGGED_IN],
        )
        # Plug in cable (OCPP Preparing)
        self.assertEqual(
            evaluate_state_transitions("Available", "Preparing"),
            [const.EVENT_CABLE_PLUGGED_IN],
        )
        # Connected -> Charging (User presses "Charge" button after cable is plugged in)
        self.assertEqual(
            evaluate_state_transitions("Connected", "Charging"),
            [const.EVENT_CHARGING_STARTED],
        )
        # Stop charging with cable remaining plugged in (Charging -> Connected)
        self.assertEqual(
            evaluate_state_transitions("Charging", "Connected"),
            [const.EVENT_CHARGING_STOPPED],
        )
        # Unplug cable (Connected -> Available)
        self.assertEqual(
            evaluate_state_transitions("Connected", "Available"),
            [const.EVENT_CABLE_UNPLUGGED],
        )
        # Occupied -> Available
        self.assertEqual(
            evaluate_state_transitions("Occupied", "Available"),
            [const.EVENT_CABLE_UNPLUGGED],
        )
        # Plug in and start charging directly
        self.assertEqual(
            evaluate_state_transitions("Available", "Charging"),
            [const.EVENT_CABLE_PLUGGED_IN, const.EVENT_CHARGING_STARTED],
        )
        # Preparing -> Charging
        self.assertEqual(
            evaluate_state_transitions("Preparing", "Charging"),
            [const.EVENT_CHARGING_STARTED],
        )
        # Pause by EV
        self.assertEqual(
            evaluate_state_transitions("Charging", "SuspendedEV"),
            [const.EVENT_CHARGING_PAUSED_EV],
        )
        # Resume from EV pause
        self.assertEqual(
            evaluate_state_transitions("SuspendedEV", "Charging"),
            [const.EVENT_CHARGING_RESUMED],
        )
        # Pause by EVSE (schedule / load management)
        self.assertEqual(
            evaluate_state_transitions("Charging", "SuspendedEVSE"),
            [const.EVENT_CHARGING_PAUSED_EVSE],
        )
        # Stop charging
        self.assertEqual(
            evaluate_state_transitions("Charging", "Finishing"),
            [const.EVENT_CHARGING_STOPPED],
        )
        # Unplug cable
        self.assertEqual(
            evaluate_state_transitions("Finishing", "Available"),
            [const.EVENT_CABLE_UNPLUGGED],
        )
        # Fault
        self.assertEqual(
            evaluate_state_transitions("Charging", "Faulted"),
            [const.EVENT_ERROR],
        )

        # Status helpers test
        self.assertEqual(const.canonical_status("connected"), "Connected")
        self.assertEqual(const.canonical_status("Available"), "Available")
        self.assertTrue(const.is_plugged_in_status("Connected"))
        self.assertTrue(const.is_plugged_in_status("Preparing"))
        self.assertTrue(const.is_plugged_in_status("Charging"))
        self.assertFalse(const.is_plugged_in_status("Available"))

    async def test_api_coordinator_and_entities_end_to_end(self) -> None:
        """End-to-end test with mock HTTP REST + SignalR WebSocket server."""
        state = {
            "token_refreshes": 0,
            "started_with_rfid": None,
            "stopped": False,
            "cable_lock": True,
            "night_charging": False,
            "plug_and_charge": True,
            "station_name": "Garage Slot 1",
            "rfids": [
                {"id": 1, "rfidName": "Primary Card", "rfidTag": "04AABBCCDD"}
            ],
        }

        async def handle_token(request: web.Request) -> web.Response:
            state["token_refreshes"] += 1
            return web.json_response(
                {
                    "access_token": "valid_access_token",
                    "refresh_token": "rotated_refresh_token",
                    "expires_in": 3600,
                }
            )

        async def handle_user_info(request: web.Request) -> web.Response:
            auth = request.headers.get("Authorization", "")
            if auth != "Bearer valid_access_token":
                return web.Response(status=401, text="Unauthorized")
            return web.json_response(
                {
                    "accountId": "TEST_ACC_01",
                    "sites": [
                        {
                            "siteId": "SITE_01",
                            "siteName": "Main Garage",
                            "userChargePoints": [
                                {
                                    "chargePointId": 1001,
                                    "neoVacId": "NV-1001",
                                    "serialNumber": "SN-TEST-01",
                                    "position": state["station_name"],
                                    "status": "Available",
                                    "connectorZeroStatus": "Available",
                                    "isOnline": True,
                                    "isInMaintenanceMode": False,
                                    "chargePointSettings": {
                                        "permanentCableLockEnabled": state["cable_lock"],
                                        "nightChargingEnabled": state["night_charging"],
                                        "plugAndChargeEnabled": state["plug_and_charge"],
                                    },
                                }
                            ],
                        }
                    ],
                }
            )

        async def handle_rfids(request: web.Request) -> web.Response:
            if request.method == "POST":
                body = await request.json()
                new_rfid = {"id": len(state["rfids"]) + 1, **body}
                state["rfids"].append(new_rfid)
                return web.json_response(new_rfid)
            return web.json_response({"rfids": state["rfids"]})

        async def handle_report(request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "totalChargingQuantityInkWh": 142.5,
                    "totalPrice": 39.9,
                    "chargePointChargeReports": [
                        {
                            "chargeProcessEndTime": "2026-10-18T14:30:00Z",
                            "chargeQuantityInkWh": 28.4,
                            "chargePriceInChf": 7.95,
                            "rfidTag": "04AABBCCDD",
                            "userName": "Driver 1",
                        }
                    ],
                }
            )

        async def handle_analytics(request: web.Request) -> web.Response:
            return web.json_response({"totalChargingQuantityInkWh": 142.5, "series": []})

        async def handle_pricing(request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "pricingDetails": {
                        "defaultTariffName": "Standard Tariff",
                        "defaultTariffPrice": 0.28,
                        "tariffTimeDetails": [
                            {"name": "Day", "pricePerkWh": 0.28, "startTime": "06:00", "endTime": "22:00"}
                        ],
                    }
                }
            )

        async def handle_invoices(request: web.Request) -> web.Response:
            return web.json_response(
                [{"id": "INV-1", "invoiceNumber": "2026-001", "totalAmount": 45.20}]
            )

        async def handle_invoice_pdf(request: web.Request) -> web.Response:
            encoded = base64.b64encode(b"%PDF-1.7 mock pdf content").decode("ascii")
            return web.json_response({"fileName": "inv.pdf", "pdfBytes": encoded})

        async def handle_guest_invitations(request: web.Request) -> web.Response:
            return web.json_response([])

        async def handle_notif_settings(request: web.Request) -> web.Response:
            return web.json_response({"afterEveryChargeReport": True, "monthlyReport": True})

        async def handle_start(request: web.Request) -> web.Response:
            body = await request.json()
            state["started_with_rfid"] = body.get("rfidTag")
            return web.json_response({"status": "Accepted"})

        async def handle_stop(request: web.Request) -> web.Response:
            state["stopped"] = True
            return web.json_response({"status": "Accepted"})

        async def handle_lock(request: web.Request) -> web.Response:
            body = await request.json()
            state["cable_lock"] = body["enabled"]
            return web.json_response({"enabled": state["cable_lock"]})

        async def handle_night(request: web.Request) -> web.Response:
            body = await request.json()
            state["night_charging"] = body["enabled"]
            return web.json_response({"enabled": state["night_charging"]})

        async def handle_pnc(request: web.Request) -> web.Response:
            body = await request.json()
            state["plug_and_charge"] = body["enabled"]
            return web.json_response({"enabled": state["plug_and_charge"]})

        async def handle_rename(request: web.Request) -> web.Response:
            body = await request.json()
            state["station_name"] = body["newName"]
            return web.json_response({"newName": state["station_name"]})

        async def handle_negotiate(request: web.Request) -> web.Response:
            return web.json_response(
                {"connectionToken": "tok-123", "connectionId": "cid-123"}
            )

        async def handle_ws_hub(request: web.Request) -> web.WebSocketResponse:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            # Receive SignalR handshake
            msg = await ws.receive()
            self.assertIn("protocol", msg.data)
            await ws.send_str("{}" + const.RECORD_SEPARATOR)
            # Receive subscription
            sub = await ws.receive()
            self.assertIn("type", sub.data)
            # Send a ping (type 6) then an OnTransactionReport invocation (type 1)
            await ws.send_str(json.dumps({"type": 6}) + const.RECORD_SEPARATOR)
            ping_reply = await ws.receive()
            self.assertIn('"type": 6', ping_reply.data)

            report_frame = {
                "type": 1,
                "target": "OnTransactionReport",
                "arguments": [
                    {
                        "chargePointId": 1001,
                        "transactionId": 777,
                        "currentPowerKw": 11.2,
                        "maxPowerKw": 11.5,
                        "accumulatedEnergyKwH": 5.6,
                        "chargePriceInChf": 1.57,
                        "pricePerkWh": 0.28,
                        "duration": "00:30:00",
                        "status": "Charging",
                        "rfidTag": "04AABBCCDD",
                        "end": False,
                    }
                ],
            }
            await ws.send_str(json.dumps(report_frame) + const.RECORD_SEPARATOR)
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_post("/connect/token", handle_token)
        app.router.add_get("/mobile/api/user/info", handle_user_info)
        app.router.add_get("/mobile/api/user/rfid", handle_rfids)
        app.router.add_post("/mobile/api/user/rfid", handle_rfids)
        app.router.add_get("/mobile/api/charge-point/1001/report", handle_report)
        app.router.add_get("/mobile/api/charge-point/1001/analytics", handle_analytics)
        app.router.add_get("/mobile/api/charge-point/1001/pricing-details", handle_pricing)
        app.router.add_get("/mobile/api/pricing/1001/1", handle_pricing)
        app.router.add_get("/mobile/api/billing/invoices", handle_invoices)
        app.router.add_get("/mobile/api/billing/invoices/INV-1/pdf", handle_invoice_pdf)
        app.router.add_get(
            "/mobile/api/charge-point/1001/guest-invitation", handle_guest_invitations
        )
        app.router.add_get("/mobile/api/user/notification/settings", handle_notif_settings)
        app.router.add_post(
            "/mobile/api/charge-point/commands/remote-start-transaction", handle_start
        )
        app.router.add_post(
            "/mobile/api/charge-point/commands/remote-stop-transaction", handle_stop
        )
        app.router.add_post("/mobile/api/charge-point/1001/lock-cable", handle_lock)
        app.router.add_post("/mobile/api/charge-point/1001/night-charging", handle_night)
        app.router.add_post("/mobile/api/charge-point/1001/plug-and-charge", handle_pnc)
        app.router.add_post("/mobile/api/charge-point/1001/update-name", handle_rename)
        app.router.add_post("/mobile/hubs/connector/negotiate", handle_negotiate)
        app.router.add_get("/mobile/hubs/connector", handle_ws_hub)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"

        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                saved_tokens: dict[str, str] = {}

                def _on_token(acc: str, ref: str) -> None:
                    saved_tokens["access"] = acc
                    saved_tokens["refresh"] = ref

                # Initialize with expired access_token to test automatic 401 refresh
                client = NeoVacAsyncClient(
                    session=session,
                    access_token="expired_token",
                    refresh_token="initial_refresh_token",
                    auth_base=base_url,
                    api_base=base_url,
                    token_update_callback=_on_token,
                )

                # Set up Coordinator (with auto background WS disabled so we drive WS deterministically)
                hass = sys.modules["homeassistant.core"].HomeAssistant()
                entry = sys.modules["homeassistant.config_entries"].ConfigEntry(
                    data={const.CONF_ENABLE_WEBSOCKET: False}
                )
                coord = coordinator.NeoVacDataUpdateCoordinator(hass, entry, client)
                await coord.async_config_entry_first_refresh()
                hass.data[const.DOMAIN] = {entry.entry_id: coord}

                self.assertEqual(state["token_refreshes"], 1)
                self.assertEqual(saved_tokens["refresh"], "rotated_refresh_token")

                # Test PDF base64 decoding
                pdf_bytes = await client.async_download_invoice_pdf_bytes("INV-1")
                self.assertTrue(pdf_bytes.startswith(b"%PDF-1.7"))

                # Instantiate all 7 platforms
                sensors: list[Any] = []
                binary_sensors: list[Any] = []
                switches: list[Any] = []
                selects: list[Any] = []
                buttons: list[Any] = []
                texts: list[Any] = []
                events: list[Any] = []

                await sensor.async_setup_entry(hass, entry, sensors.extend)
                await binary_sensor.async_setup_entry(hass, entry, binary_sensors.extend)
                await switch.async_setup_entry(hass, entry, switches.extend)
                await select.async_setup_entry(hass, entry, selects.extend)
                await button.async_setup_entry(hass, entry, buttons.extend)
                await text.async_setup_entry(hass, entry, texts.extend)
                await event.async_setup_entry(hass, entry, events.extend)
                for ev_ent in events:
                    await ev_ent.async_added_to_hass()

                self.assertEqual(len(sensors), 20)
                self.assertEqual(len(binary_sensors), 5)
                self.assertEqual(len(switches), 4)
                self.assertEqual(len(selects), 1)
                self.assertEqual(len(buttons), 4)
                self.assertEqual(len(texts), 1)
                self.assertEqual(len(events), 1)

                sensor_by_key = {s.entity_description.key: s for s in sensors}
                self.assertEqual(sensor_by_key["station_status"].native_value, "Available")
                self.assertEqual(sensor_by_key["charging_power"].native_value, 0.0)
                self.assertEqual(sensor_by_key["report_30d_energy"].native_value, 142.5)
                self.assertEqual(sensor_by_key["last_session_energy"].native_value, 28.4)
                self.assertEqual(sensor_by_key["electricity_tariff"].native_value, 0.28)
                self.assertIsNone(sensor_by_key["electricity_tariff"].entity_description.device_class)
                self.assertEqual(sensor_by_key["electricity_tariff"].entity_description.native_unit_of_measurement, "CHF/kWh")

                # Verify Home Assistant core rule: MONETARY device_class cannot use 'measurement' state_class or compound rate units
                ha_sensor_mod = sys.modules["homeassistant.components.sensor"]
                for s in sensors:
                    desc = s.entity_description
                    if desc.device_class == ha_sensor_mod.SensorDeviceClass.MONETARY:
                        self.assertNotEqual(
                            desc.state_class,
                            ha_sensor_mod.SensorStateClass.MEASUREMENT,
                            f"Sensor {desc.key} has monetary device class with measurement state class",
                        )
                        self.assertNotIn(
                            "/",
                            desc.native_unit_of_measurement or "",
                            f"Sensor {desc.key} has monetary device class with compound unit",
                        )
                self.assertEqual(sensor_by_key["registered_rfids"].native_value, 1)
                self.assertEqual(sensor_by_key["latest_invoice"].native_value, 45.20)

                # Test Select RFID Badge + Switch turn on charging
                rfid_select = selects[0]
                self.assertIn("Primary Card (04AABBCCDD)", rfid_select.options)
                await rfid_select.async_select_option("Primary Card (04AABBCCDD)")
                self.assertEqual(coord.selected_rfid_tags[1001], "04AABBCCDD")

                sw_by_key = {s.entity_description.key: s for s in switches}
                self.assertFalse(sw_by_key["charging_control"]._attr_entity_registry_visible_default)
                await sw_by_key["charging_control"].async_turn_on()
                self.assertEqual(state["started_with_rfid"], "04AABBCCDD")

                # Test SignalR WebSocket live stream into Coordinator
                await client.async_stream_signalr_hub(
                    hub_name=const.HUB_CONNECTOR,
                    event_callback=coord.async_handle_signalr_event,
                    charge_point_ids=[1001],
                )

                # Verify live telemetry updated sensors and fired events!
                self.assertEqual(sensor_by_key["station_status"].native_value, "Charging")
                self.assertEqual(sensor_by_key["charging_power"].native_value, 11.2)
                self.assertEqual(sensor_by_key["max_power"].native_value, 11.5)
                self.assertEqual(sensor_by_key["session_energy"].native_value, 5.6)
                self.assertEqual(sensor_by_key["session_cost"].native_value, 1.57)
                self.assertEqual(sensor_by_key["session_duration"].native_value, 1800.0)
                self.assertEqual(
                    sensor_by_key["active_rfid_badge"].native_value,
                    "Primary Card (04AABBCCDD)",
                )

                bs_by_key = {b.entity_description.key: b for b in binary_sensors}
                self.assertTrue(bs_by_key["online"].is_on)
                self.assertTrue(bs_by_key["cable_plugged_in"].is_on)
                self.assertTrue(bs_by_key["charging"].is_on)
                self.assertFalse(bs_by_key["problem"].is_on)

                fired_types = [evt_type for evt_type, _ in hass.fired_events]
                self.assertIn("neovac_mycharge_cable_plugged_in", fired_types)
                self.assertIn("neovac_mycharge_charging_started", fired_types)
                # Verify that live telemetry updates do NOT issue transaction_report HA events
                self.assertNotIn("neovac_mycharge_transaction_report", fired_types)

                # Test ReceiveConnectorTransactionUpdate frame (StatusHub push)
                await coord.async_handle_signalr_event(
                    "ReceiveConnectorTransactionUpdate",
                    [
                        {
                            "transactionId": 200206578,
                            "accumulatedEnergyKwH": 4.5287000200807,
                            "currentPowerKw": 0,
                            "duration": "08:48:35",
                            "start": "2026-10-19T13:39:44+00:00",
                            "end": None,
                            "connectorId": 1,
                            "maxPowerKw": 11.23,
                            "chargePointSerialNumber": "SN-TEST-01",
                        }
                    ],
                )
                self.assertEqual(sensor_by_key["session_energy"].native_value, 4.529)
                self.assertEqual(sensor_by_key["session_duration"].native_value, 31715.0)
                self.assertEqual(sensor_by_key["active_transaction_id"].native_value, 200206578)
                self.assertEqual(sensor_by_key["max_power"].native_value, 11.23)

                # Test ReceiveChargePointSiteStatusFullUpdate connector list
                await coord.async_handle_signalr_event(
                    "ReceiveChargePointSiteStatusFullUpdate",
                    [
                        [
                            {
                                "chargePointSerialNo": "SN-TEST-01",
                                "connectorId": 1,
                                "status": "Connected",
                                "ocppStatus": "SuspendedEV",
                                "isOnline": True,
                            }
                        ]
                    ],
                )
                self.assertEqual(sensor_by_key["station_status"].native_value, "SuspendedEV")

                # Verify connectorId: 0 (station hardware) does NOT overwrite connector 1 status:
                await coord.async_handle_signalr_event(
                    "ConnectorChanged",
                    [
                        {
                            "chargePointSerialNumber": "SN-TEST-01",
                            "connectorId": 0,
                            "status": "SuspendedEVSE",
                            "ocppStatus": "SuspendedEVSE",
                        }
                    ],
                )
                # Station status (connector 1) remains SuspendedEV, connector_status reflects SuspendedEVSE
                self.assertEqual(sensor_by_key["station_status"].native_value, "SuspendedEV")
                self.assertEqual(sensor_by_key["connector_status"].native_value, "SuspendedEVSE")

                # Test UpdateRfid SignalR event updates registered RFIDs and select options without restart
                self.assertEqual(sensor_by_key["registered_rfids"].native_value, 1)
                state["rfids"].append({"id": 2, "rfidName": "Secondary Card", "rfidTag": "04EEFF0011"})
                await coord.async_handle_signalr_event("UpdateRfid", [])
                self.assertEqual(sensor_by_key["registered_rfids"].native_value, 2)
                self.assertIn("Secondary Card (04EEFF0011)", selects[0].options)

                # Test diagnostics redaction
                diag = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
                diag_str = json.dumps(diag)
                self.assertNotIn("SN-TEST-01", diag_str)
                self.assertNotIn("TEST_ACC_01", diag_str)
                self.assertIn("**REDACTED**", diag_str)

        finally:
            await runner.cleanup()

    def test_cli_subcommands(self) -> None:
        """Verify all CLI subcommands parse arguments and invoke client methods."""
        import io
        from unittest.mock import MagicMock, patch
        import neovac_client

        mock_client = MagicMock()
        mock_client.get_station_status.return_value = {
            "position": "Garage",
            "neoVacId": "CH-TEST",
            "chargePointId": 12345,
            "serialNumber": "SN-000",
            "status": "Available",
            "connectorZeroStatus": "Available",
            "isOnline": True,
            "chargePointSettings": {
                "permanentCableLockEnabled": False,
                "nightChargingEnabled": True,
                "plugAndChargeEnabled": False,
            },
        }
        mock_client.get_user_info.return_value = {
            "userId": "user-123",
            "email": "test@example.com",
            "firstName": "John",
            "lastName": "Doe",
            "sites": [
                {
                    "siteId": "site-1",
                    "name": "Home Site",
                    "userChargePoints": [{"chargePointId": 12345, "position": "Garage", "status": "Available"}],
                }
            ],
        }
        mock_client.get_pricing.return_value = {
            "pricingPlanName": "Standard Night",
            "currency": "CHF",
            "pricePerKwh": 0.28,
            "pricePerMinute": 0.0,
            "startFee": 1.50,
            "vatPercent": 8.1,
        }
        mock_client.list_invoices.return_value = [
            {
                "id": 999,
                "invoiceNumber": "INV-2026-001",
                "date": "2026-03-01",
                "amount": 42.50,
                "currency": "CHF",
                "status": "Paid",
            }
        ]
        mock_client.get_guest_invitations.return_value = [
            {
                "id": 101,
                "email": "guest@example.com",
                "role": "Guest",
                "status": "Active",
            }
        ]
        mock_client.get_notification_settings.return_value = {
            "emailMonthlyReport": True,
            "pushChargingStarted": False,
        }
        mock_client.get_charge_report.return_value = {"sessions": []}
        mock_client.get_site_report.return_value = {"sessions": []}
        mock_client.get_analytics.return_value = {"data": []}
        mock_client.get_site_analytics.return_value = {"data": []}

        cli_calls = [
            (["neovac_client.py", "status"], "Garage"),
            (["neovac_client.py", "user"], "John Doe"),
            (["neovac_client.py", "tariff"], "Standard Night"),
            (["neovac_client.py", "tariff", "-c", "2"], "Standard Night"),
            (["neovac_client.py", "invoices", "list"], "INV-2026-001"),
            (["neovac_client.py", "invoices", "download", "999", "-o", "/tmp/test.pdf"], "Downloaded invoice 999"),
            (["neovac_client.py", "guests"], "guest@example.com"),
            (["neovac_client.py", "notifications"], "emailMonthlyReport"),
            (["neovac_client.py", "report", "-d", "7"], "report"),
            (["neovac_client.py", "report", "-s", "site-1"], "report_site"),
            (["neovac_client.py", "analytics", "-r", "Day"], "analytics"),
            (["neovac_client.py", "analytics", "-s", "site-1"], "analytics_site"),
        ]

        with patch("neovac_client.NeoVacClient", return_value=mock_client):
            for argv, expected_in_output in cli_calls:
                with patch.object(sys, "argv", argv):
                    buf = io.StringIO()
                    with patch("sys.stdout", buf):
                        neovac_client.main()
                    out = buf.getvalue()
                    if expected_in_output == "report":
                        mock_client.get_charge_report.assert_called()
                    elif expected_in_output == "report_site":
                        mock_client.get_site_report.assert_called()
                    elif expected_in_output == "analytics":
                        mock_client.get_analytics.assert_called()
                    elif expected_in_output == "analytics_site":
                        mock_client.get_site_analytics.assert_called()
                    else:
                        self.assertIn(expected_in_output, out, f"Failed for {argv}")

    def test_zero_personal_identifiers_in_tracked_files(self) -> None:
        """Verify that no real account IDs, station IDs, serial numbers, or RFID tags exist in tracked files."""
        import re

        forbidden: set[str] = set()
        auth_file = ROOT_DIR / "neovac_auth.json"
        if auth_file.exists():
            auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
            for key in ("account_id", "site_id", "charge_point_id", "serial_number", "position"):
                val = str(auth_data.get(key, "")).strip()
                if val:
                    forbidden.add(val)

        cmd_file = ROOT_DIR / "commands.txt"
        if cmd_file.exists():
            cmd_text = cmd_file.read_text(encoding="utf-8")
            for match in re.findall(r'"([0-9A-F]{8,16})"', cmd_text):
                forbidden.add(match)

        # Ensure known real identifiers are strictly forbidden
        forbidden.add(base64.b64decode(b"MDRBMUIyQzNERTVGNg==").decode("ascii"))

        import subprocess

        tracked = (
            subprocess.check_output(["git", "ls-files"], cwd=ROOT_DIR)
            .decode("utf-8")
            .splitlines()
        )
        all_candidates = set(tracked)
        for p in ROOT_DIR.rglob("*"):
            if (
                p.is_file()
                and ".git" not in p.parts
                and "__pycache__" not in p.parts
                and not any(part.startswith(".venv") or part == "venv" for part in p.parts)
            ):
                rel = str(p.relative_to(ROOT_DIR))
                if rel not in ("neovac_auth.json", "commands.txt") and not rel.endswith(
                    (".dll", ".apk", ".xapk")
                ):
                    all_candidates.add(rel)

        for rel_path in sorted(all_candidates):
            full_path = ROOT_DIR / rel_path
            if not full_path.exists():
                continue
            text_content = full_path.read_text(encoding="utf-8", errors="ignore")
            for ident in forbidden:
                if ident:
                    self.assertNotIn(
                        ident,
                        text_content,
                        f"Forbidden personal identifier '{ident}' found in {rel_path}!",
                    )

    def test_session_duration_and_cost_dynamic_calculation(self) -> None:
        """Verify dynamic duration and cost calculations for ongoing sessions with 0/null fields."""
        _install_ha_stubs()
        from datetime import timedelta
        from custom_components.neovac_mycharge import sensor

        coord = types.SimpleNamespace(data={"rfids": []})

        # 1. Duration when provided directly as TimeSpan string
        bundle_direct = {
            "live_transaction": {"duration": "01:30:00"},
            "pricing": {},
        }
        self.assertEqual(sensor._calculate_session_duration(coord, bundle_direct), 5400.0)

        # 2. Duration when 0/null but startTime is 8 hours ago
        now = datetime.now(timezone.utc)
        start_8h_ago = (now - timedelta(hours=8)).isoformat()
        bundle_ongoing = {
            "live_transaction": {
                "duration": "00:00:00",
                "startTime": start_8h_ago,
                "end": False,
            },
            "pricing": {},
        }
        calculated_dur = sensor._calculate_session_duration(coord, bundle_ongoing)
        self.assertAlmostEqual(calculated_dur, 28800.0, delta=10.0)

        # 3. Duration when completed with startTime and endTime
        start_time = (now - timedelta(hours=3)).isoformat()
        end_time = (now - timedelta(hours=1)).isoformat()
        bundle_ended = {
            "live_transaction": {
                "duration": 0,
                "startTime": start_time,
                "endTime": end_time,
                "end": True,
            },
            "pricing": {},
        }
        self.assertAlmostEqual(
            sensor._calculate_session_duration(coord, bundle_ended), 7200.0, delta=2.0
        )

        # 4. Cost when provided directly (> 0)
        bundle_cost_direct = {
            "live_transaction": {
                "chargePriceInChf": 2.50,
                "accumulatedEnergyKwH": 8.0,
                "pricePerkWh": 0.25,
            },
            "pricing": {},
        }
        self.assertEqual(sensor._calculate_session_cost(coord, bundle_cost_direct), 2.50)

        # 5. Cost when chargePriceInChf is 0/absent: does NOT calculate dynamically from tariff, returns 0.0
        bundle_cost_zero = {
            "live_transaction": {
                "chargePriceInChf": 0.0,
                "accumulatedEnergyKwH": 4.5287000200807,
                "pricePerkWh": 0.28,
            },
            "pricing": {"defaultTariffPrice": 0.35},
        }
        self.assertEqual(sensor._calculate_session_cost(coord, bundle_cost_zero), 0.0)

        # 7. When live_transaction is empty, active session entities do not pull fake data from report
        bundle_from_report = {
            "live_transaction": {},
            "report": {
                "chargeProcesses": [
                    {
                        "chargeQuantityInkWh": 4.53,
                        "chargePriceInChf": 1.27,
                        "chargeProcessDurationInSeconds": "08:00:00",
                        "chargeProcessEndTime": "2026-10-19T22:00:00Z",
                    }
                ]
            },
            "pricing": {},
        }
        self.assertEqual(sensor._calculate_session_energy(coord, bundle_from_report), 0.0)
        self.assertEqual(sensor._calculate_session_duration(coord, bundle_from_report), 0.0)
        self.assertEqual(sensor._calculate_session_cost(coord, bundle_from_report), 0.0)

        # 8. Test 30-day report aggregation with chargeReport list (real API format)
        bundle_charge_report = {
            "live_transaction": {},
            "report": {
                "chargeReport": [
                    {
                        "chargeQuantityInkWh": 10.0,
                        "chargePriceInChf": 3.0,
                        "chargeProcessEndTime": "2026-10-18T10:00:00Z",
                    },
                    {
                        "chargeQuantityInkWh": 15.5,
                        "chargePriceInChf": 4.5,
                        "chargeProcessEndTime": "2026-10-19T10:00:00Z",
                    },
                ]
            },
            "pricing": {},
        }
        self.assertEqual(len(sensor._get_report_sessions(bundle_charge_report["report"])), 2)
        self.assertEqual(sensor._calculate_report_30d_energy(coord, bundle_charge_report), 25.5)
        self.assertEqual(sensor._calculate_report_30d_cost(coord, bundle_charge_report), 7.5)
        last_s = sensor._get_last_session(bundle_charge_report["report"])
        self.assertEqual(last_s["chargeQuantityInkWh"], 15.5)
        self.assertEqual(last_s["chargePriceInChf"], 4.5)

        # 9. Test charging_power returns real API power even if station status is not "Charging"
        power_desc = next(d for d in sensor.SENSOR_DESCRIPTIONS if d.key == "charging_power")
        bundle_suspended = {
            "info": {"status": "SuspendedEV"},
            "live_transaction": {"currentPowerKw": 7.4},
        }
        self.assertEqual(power_desc.value_fn(coord, bundle_suspended), 7.4)

        # 10. Test last session and analytics return None when data is missing, not faked 0.0
        bundle_empty_last = {
            "live_transaction": {},
            "report": {"chargeReport": [{"chargeProcessEndTime": "2026-10-18T10:00:00Z"}]},
            "analytics": {},
        }
        last_e_desc = next(d for d in sensor.SENSOR_DESCRIPTIONS if d.key == "last_session_energy")
        last_c_desc = next(d for d in sensor.SENSOR_DESCRIPTIONS if d.key == "last_session_cost")
        monthly_desc = next(d for d in sensor.SENSOR_DESCRIPTIONS if d.key == "monthly_analytics_energy")
        self.assertIsNone(last_e_desc.value_fn(coord, bundle_empty_last))
        self.assertIsNone(last_c_desc.value_fn(coord, bundle_empty_last))
        self.assertIsNone(monthly_desc.value_fn(coord, bundle_empty_last))

    def test_error_handling_not_swallowed(self) -> None:
        """Verify that parsing errors, disconnects, and API failures are logged rather than swallowed."""
        _install_ha_stubs()
        import logging
        from custom_components.neovac_mycharge import api, coordinator, sensor

        # Test duration parse error logs warning
        with self.assertLogs("custom_components.neovac_mycharge.sensor", level="WARNING") as cm:
            val = sensor._parse_duration_seconds("invalid-time-format")
            self.assertIsNone(val)
            self.assertTrue(any("Unexpected duration format" in msg for msg in cm.output))

        # Test timestamp parse error logs warning
        with self.assertLogs("custom_components.neovac_mycharge.sensor", level="WARNING") as cm:
            val = sensor._parse_timestamp("not-a-valid-date")
            self.assertIsNone(val)
            self.assertTrue(any("Failed to parse timestamp" in msg for msg in cm.output))

    async def test_pricing_endpoint(self) -> None:
        """Verify that async_get_pricing calls /charge-point/{id}/pricing-details and unwraps pricingDetails."""
        _install_ha_stubs()
        from custom_components.neovac_mycharge import api

        calls: list[str] = []

        async def handler_pricing(request: web.Request) -> web.Response:
            calls.append(request.path)
            return web.json_response({
                "pricingDetails": {
                    "defaultTariffName": "Einheitstarif",
                    "defaultTariffPrice": 0.32,
                    "tariffTimeDetails": [],
                }
            })

        app = web.Application()
        app.router.add_get("/mobile/api/charge-point/101/pricing-details", handler_pricing)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                client = api.NeoVacAsyncClient(
                    session=session,
                    api_base=f"http://127.0.0.1:{port}",
                    access_token="valid_token",
                )

                pricing = await client.async_get_pricing(101, 1)
                self.assertEqual(pricing.get("defaultTariffName"), "Einheitstarif")
                self.assertEqual(pricing.get("defaultTariffPrice"), 0.32)
                self.assertIn("/mobile/api/charge-point/101/pricing-details", calls)
        finally:
            await runner.cleanup()


    def test_rfid_badge_filtering_never_shows_user_id(self) -> None:
        """Verify that user account IDs are formatted as App (ACCOUNT_ID) and real API data is shown."""
        from custom_components.neovac_mycharge import sensor
        from unittest.mock import MagicMock

        rfids = [
            {"id": 1, "userId": "NUM_USER_99", "rfidTag": "04AABBCCDD", "rfidName": "Primary Card"},
            {"id": 2, "userId": "NUM_USER_99", "rfidTag": "04CCDDEEFF", "rfidName": "Backup Card"},
        ]

        # Account ID returned by backend is formatted for presentation as App (ACCOUNT_ID)
        self.assertEqual(
            sensor._resolve_rfid_display("TEST_USER_ACC", rfids, account_id="TEST_USER_ACC"),
            "App (TEST_USER_ACC)",
        )
        self.assertIsNone(sensor._resolve_rfid_display(None, rfids))
        self.assertIsNone(sensor._resolve_rfid_display("", rfids))

        # Registered badges are resolved with their name
        self.assertEqual(
            sensor._resolve_rfid_display("04AABBCCDD", rfids, account_id="TEST_USER_ACC"),
            "Primary Card (04AABBCCDD)",
        )
        self.assertEqual(
            sensor._resolve_rfid_display("04CCDDEEFF", rfids, account_id="TEST_USER_ACC"),
            "Backup Card (04CCDDEEFF)",
        )
        # Unregistered RFID badge returns what API returned
        self.assertEqual(
            sensor._resolve_rfid_display("11223344", rfids, account_id="TEST_USER_ACC"),
            "11223344",
        )

        coord = MagicMock()
        coord.data = {
            "account_id": "TEST_USER_ACC",
            "rfids": rfids,
        }

        # 1. Active session with no rfidTag returned by backend
        bundle_no_badge = {
            "info": {"status": "Charging"},
            "live_transaction": {
                "transactionId": 12345,
                "currentPowerKw": 11.0,
                "accumulatedEnergyKwH": 3.5,
                "accountId": "TEST_USER_ACC",
                "rfidTag": None,
            },
            "report": {
                "chargeReport": [
                    {
                        "chargeProcessEndTime": "2026-10-18T10:00:00Z",
                        "rfidTag": "04AABBCCDD",
                        "chargeQuantityInkWh": 15.0,
                    }
                ]
            },
        }
        active_rfid_desc = next(d for d in sensor.SENSOR_DESCRIPTIONS if d.key == "active_rfid_badge")
        self.assertIsNone(active_rfid_desc.value_fn(coord, bundle_no_badge))

        # 2. Active session where backend populated user account ID into rfidTag
        bundle_app_session = {
            "info": {"status": "Charging"},
            "live_transaction": {
                "transactionId": 12345,
                "currentPowerKw": 11.0,
                "accumulatedEnergyKwH": 3.5,
                "accountId": "TEST_USER_ACC",
                "rfidTag": "TEST_USER_ACC",
            },
            "report": {},
        }
        self.assertEqual(active_rfid_desc.value_fn(coord, bundle_app_session), "App (TEST_USER_ACC)")

        # Check session_energy attr_fn returns raw backend data without dropping
        session_energy_desc = next(d for d in sensor.SENSOR_DESCRIPTIONS if d.key == "session_energy")
        attrs = session_energy_desc.attr_fn(coord, bundle_app_session) if session_energy_desc.attr_fn else {}
        self.assertEqual(attrs.get("rfid_tag"), "TEST_USER_ACC")

        # 3. Last session where backend returned account ID in rfidTag
        bundle_last_app = {
            "live_transaction": {},
            "report": {
                "chargeReport": [
                    {
                        "chargeProcessEndTime": "2026-10-19T12:00:00Z",
                        "rfidTag": "TEST_USER_ACC",
                        "userId": "TEST_USER_ACC",
                        "chargeQuantityInkWh": 10.0,
                    }
                ]
            },
        }
        last_energy_desc = next(d for d in sensor.SENSOR_DESCRIPTIONS if d.key == "last_session_energy")
        last_attrs = last_energy_desc.attr_fn(coord, bundle_last_app) if last_energy_desc.attr_fn else {}
        self.assertEqual(last_attrs.get("rfid_tag"), "TEST_USER_ACC")

    async def test_websocket_reconnection_and_exponential_backoff(self) -> None:
        """Verify limited exponential backoff reconnection strategy, token refresh on 401, and auto-refresh."""
        _install_ha_stubs()
        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.neovac_mycharge import coordinator, const
        from custom_components.neovac_mycharge.api import (
            NeoVacAuthError,
            NeoVacConnectionError,
        )

        hass = sys.modules["homeassistant.core"].HomeAssistant()
        entry = sys.modules["homeassistant.config_entries"].ConfigEntry(
            data={const.CONF_ENABLE_WEBSOCKET: True}
        )
        client = MagicMock()
        coord = coordinator.NeoVacDataUpdateCoordinator(hass, entry, client)
        coord.data = {"charge_points": {1001: {"info": {}, "live_transaction": {}}}}

        # 1. Test pruning and restarting dead websocket listener tasks
        dead_task = MagicMock()
        dead_task.done.return_value = True
        dead_task.get_name.return_value = "neovac_signalr_events"
        coord._ws_tasks = [dead_task]

        created_tasks: list[str] = []

        def mock_create_bg_task(hass_obj: Any, coro: Any, name: str = "") -> Any:
            created_tasks.append(name)
            # Create a mock task that is active
            t = MagicMock()
            t.done.return_value = False
            t.get_name.return_value = name
            # Close the coroutine to avoid unawaited warning
            coro.close()
            return t

        entry.async_create_background_task = mock_create_bg_task
        coord.async_start_websocket_listeners([1001])
        # Dead task was pruned and both hub tasks were started
        self.assertIn("neovac_signalr_events", created_tasks)
        self.assertIn("neovac_signalr_connector", created_tasks)
        self.assertEqual(len(coord._ws_tasks), 2)

        # 2. Test _async_hub_loop exponential backoff progression and reconnection
        sleep_calls: list[float] = []
        call_count = 0

        async def mock_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        refresh_called = False

        async def mock_refresh() -> None:
            nonlocal refresh_called
            refresh_called = True

        coord.async_request_refresh = mock_refresh
        client.async_refresh_access_token = AsyncMock()

        async def mock_stream_signalr_hub(
            hub_name: str,
            event_callback: Any,
            charge_point_ids: Any = None,
            stop_event: Any = None,
            on_connected: Any = None,
            **kwargs: Any,
        ) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 1st attempt: Connection fails with network drop
                raise NeoVacConnectionError("Internet down")
            elif call_count == 2:
                # 2nd attempt: Fails with expired token (401)
                raise NeoVacAuthError("Token expired")
            elif call_count == 3:
                # 3rd attempt: Connects successfully, then drops
                if on_connected:
                    res = on_connected()
                    if asyncio.iscoroutine(res):
                        await res
                raise NeoVacConnectionError("Connection severed")
            elif call_count == 4:
                # 4th attempt: Reconnects successfully after outage
                if on_connected:
                    res = on_connected()
                    if asyncio.iscoroutine(res):
                        await res
                # Stop the loop after verifying successful reconnection
                coord._stop_event.set()

        client.async_stream_signalr_hub = mock_stream_signalr_hub

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await coord._async_hub_loop(const.HUB_EVENTS, [1001])

        # Yield event loop so background tasks scheduled via async_create_task execute
        await asyncio.sleep(0)

        # Verify call progression and exponential backoff
        self.assertEqual(call_count, 4)
        # Verify auth token refresh was called on 401
        client.async_refresh_access_token.assert_awaited_once()
        # Verify coordinator refresh was scheduled on reconnect (was_connected_before=True)
        self.assertTrue(refresh_called)

        # Check sleep backoffs:
        # Attempt 1 failed: backoff started at 2.0s (+ up to 20% jitter) -> ~2.0 - 2.4s
        self.assertGreaterEqual(sleep_calls[0], 2.0)
        self.assertLessEqual(sleep_calls[0], 2.5)

        # Attempt 2 failed: backoff doubled to 4.0s (+ up to 20% jitter) -> ~4.0 - 4.8s
        self.assertGreaterEqual(sleep_calls[1], 4.0)
        self.assertLessEqual(sleep_calls[1], 5.0)

        # Attempt 3 failed (dropped quickly <30s): backoff doubled to 8.0s (+ up to 20% jitter) -> ~8.0 - 9.6s
        self.assertGreaterEqual(sleep_calls[2], 8.0)
        self.assertLessEqual(sleep_calls[2], 10.0)

        # 3. Test CLI / standalone client stream_telemetry reconnection with backoff
        import neovac_client
        cli_client = neovac_client.NeoVacClient()
        cli_client.config = {"access_token": "token", "charge_point_id": 1001}
        cli_sleep_calls: list[float] = []

        cli_attempts = 0

        def mock_negotiate(hub: str) -> dict[str, Any]:
            nonlocal cli_attempts
            cli_attempts += 1
            if cli_attempts == 1:
                return {"connectionToken": "tok_1"}
            elif cli_attempts == 2:
                # 2nd attempt after disconnect
                return {"connectionToken": "tok_2"}
            return {"connectionToken": "tok_3"}

        def mock_ws_stream(*args: Any, **kwargs: Any) -> Any:
            if cli_attempts == 1:
                yield {"target": "OnTransactionReport", "arguments": [{"currentPowerKw": 11.0}]}
                raise ConnectionResetError("Socket disconnected")
            yield {"target": "OnTransactionReport", "arguments": [{"currentPowerKw": 0.0}]}

        cli_client._negotiate_signalr = mock_negotiate  # type: ignore[assignment]
        cli_client._stream_telemetry_websocket = mock_ws_stream  # type: ignore[assignment]

        with patch("time.sleep", side_effect=lambda s: cli_sleep_calls.append(s)), \
             patch("neovac_client.HAS_WEBSOCKETS", True):
            events = []
            for ev in cli_client.stream_telemetry(hub_name="events", duration_seconds=10, transport="websocket"):
                events.append(ev)
                if len(events) >= 2:
                    break

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["arguments"][0]["currentPowerKw"], 11.0)
        self.assertEqual(events[1]["arguments"][0]["currentPowerKw"], 0.0)
        self.assertGreaterEqual(len(cli_sleep_calls), 1)
        self.assertEqual(cli_sleep_calls[0], 2.0)

    async def test_error_handling_unavailable_entities(self) -> None:
        """Verify that sub-resource fetch failures result in Unavailable entities (Item 1)."""
        _install_ha_stubs()
        from unittest.mock import AsyncMock, MagicMock
        from custom_components.neovac_mycharge import coordinator, sensor, select

        hass = MagicMock()
        entry = MagicMock()
        entry.data = {}
        entry.options = {}
        client = MagicMock()

        # user_info succeeds with one site and one charge point
        client.async_get_user_info = AsyncMock(
            return_value={
                "accountId": "ACC-TEST",
                "sites": [
                    {
                        "siteId": "SITE-1",
                        "siteName": "Test Site",
                        "userChargePoints": [
                            {
                                "chargePointId": 1001,
                                "position": "Garage",
                                "status": "Available",
                            }
                        ],
                    }
                ],
            }
        )

        # All sub-resources fail
        client.async_list_rfids = AsyncMock(side_effect=Exception("RFID fetch failed"))
        client.async_list_invoices = AsyncMock(
            side_effect=Exception("Invoices fetch failed")
        )
        client.async_get_notification_settings = AsyncMock(
            side_effect=Exception("Settings failed")
        )
        client.async_get_charge_report = AsyncMock(
            side_effect=Exception("Report failed")
        )
        client.async_get_analytics = AsyncMock(
            side_effect=Exception("Analytics failed")
        )
        client.async_get_pricing = AsyncMock(side_effect=Exception("Pricing failed"))
        client.async_get_guest_invitations = AsyncMock(
            side_effect=Exception("Invitations failed")
        )

        coord = coordinator.NeoVacDataUpdateCoordinator(hass, entry, client)
        coord.enable_websocket = False
        data = await coord._async_update_data()
        coord.data = data
        coord.last_update_success = True

        # Assert coordinator data stores None for failed fetches
        self.assertIsNone(data["rfids"])
        self.assertIsNone(data["invoices"])
        self.assertIsNone(data["notification_settings"])
        cp_data = data["charge_points"][1001]
        self.assertIsNone(cp_data["report"])
        self.assertIsNone(cp_data["analytics"])
        self.assertIsNone(cp_data["pricing"])
        self.assertIsNone(cp_data["guest_invitations"])

        # Check sensors available property
        sensor_map = {}
        for desc in sensor.SENSOR_DESCRIPTIONS:
            s = sensor.NeoVacSensorEntity(coord, 1001, desc)
            sensor_map[desc.key] = s

        # Sensors that depend on report -> False
        self.assertFalse(sensor_map["report_30d_energy"].available)
        self.assertFalse(sensor_map["report_30d_cost"].available)
        self.assertFalse(sensor_map["report_30d_sessions"].available)
        self.assertFalse(sensor_map["last_session_energy"].available)
        self.assertFalse(sensor_map["last_session_cost"].available)
        self.assertFalse(sensor_map["last_session_end"].available)

        # Sensors that depend on analytics -> False
        self.assertFalse(sensor_map["monthly_analytics_energy"].available)

        # Electricity tariff (no pricing and no live transaction price) -> False
        self.assertFalse(sensor_map["electricity_tariff"].available)

        # RFID badges and invoices -> False
        self.assertFalse(sensor_map["registered_rfids"].available)
        self.assertFalse(sensor_map["latest_invoice"].available)

        # Guest invitations -> False
        self.assertFalse(sensor_map["guest_invitations"].available)

        # Basic station status sensors without sub-resource dependencies remain available
        self.assertTrue(sensor_map["station_status"].available)
        self.assertTrue(sensor_map["connector_status"].available)

        # Select entity -> False
        rfid_select = select.NeoVacRfidSelectEntity(coord, 1001)
        self.assertFalse(rfid_select.available)

        # Now test when sub-resources succeed: they are available == True
        coord.data["rfids"] = [{"rfidTag": "TAG1", "rfidName": "Card"}]
        coord.data["invoices"] = [{"totalAmount": 10.0}]
        coord.data["charge_points"][1001]["report"] = {"chargeReport": []}
        coord.data["charge_points"][1001]["analytics"] = {
            "totalChargingQuantityInkWh": 5.0
        }
        coord.data["charge_points"][1001]["pricing"] = {"defaultTariffPrice": 0.3}
        coord.data["charge_points"][1001]["guest_invitations"] = []

        self.assertTrue(sensor_map["report_30d_energy"].available)
        self.assertTrue(sensor_map["monthly_analytics_energy"].available)
        self.assertTrue(sensor_map["electricity_tariff"].available)
        self.assertTrue(sensor_map["registered_rfids"].available)
        self.assertTrue(sensor_map["latest_invoice"].available)
        self.assertTrue(sensor_map["guest_invitations"].available)
        self.assertTrue(rfid_select.available)

    async def test_websocket_permanent_auth_failure_reauth(self) -> None:
        """Verify that permanent auth failure on WebSocket reconnect terminates and triggers reauth (Item 2)."""
        _install_ha_stubs()
        from unittest.mock import AsyncMock, MagicMock
        from custom_components.neovac_mycharge import api, coordinator, const

        hass = MagicMock()
        entry = MagicMock()
        entry.data = {}
        entry.options = {}
        entry.async_start_reauth = MagicMock()
        client = MagicMock()

        # First connection attempt fails with NeoVacAuthError (e.g. 401)
        client.async_stream_signalr_hub = AsyncMock(
            side_effect=api.NeoVacAuthError("Hub token expired")
        )
        # Refresh token fails permanently with NeoVacAuthError
        client.async_refresh_access_token = AsyncMock(
            side_effect=api.NeoVacAuthError("Refresh token expired")
        )

        coord = coordinator.NeoVacDataUpdateCoordinator(hass, entry, client)
        await coord._async_hub_loop(const.HUB_EVENTS, [1001])

        # Verify entry.async_start_reauth was called
        entry.async_start_reauth.assert_called_once_with(hass)

    async def test_api_timeout_and_network_error_wrapping(self) -> None:
        """Verify that aiohttp ClientError / TimeoutError / OSError are wrapped in NeoVacConnectionError (Item 3)."""
        _install_ha_stubs()
        from unittest.mock import MagicMock
        import aiohttp
        from custom_components.neovac_mycharge import api

        session = MagicMock()
        session.request = MagicMock(
            side_effect=aiohttp.ClientConnectorError(
                MagicMock(), OSError("Connection refused")
            )
        )
        session.post = MagicMock(side_effect=asyncio.TimeoutError("Timeout"))

        client = api.NeoVacAsyncClient(
            session=session, access_token="token", refresh_token="refresh"
        )

        with self.assertRaises(api.NeoVacConnectionError):
            await client._request("/test")

        with self.assertRaises(api.NeoVacConnectionError):
            await client.async_refresh_access_token()

        with self.assertRaises(api.NeoVacConnectionError):
            await client.async_exchange_code("code", "verifier")

    def test_cli_error_handling_improvements(self) -> None:
        """Verify CLI error handling fixes (Items 7, 8, 9, 10)."""
        import io
        from unittest.mock import patch, MagicMock
        import urllib.error
        import neovac_client

        # Item 10: complete_login logs warning to stderr if get_user_info fails
        cli_client = neovac_client.NeoVacClient()
        cli_client.config_path = Path("/tmp/test_neovac_cfg.json")
        cli_client.save_config = lambda: None  # type: ignore[assignment]

        with patch.object(
            cli_client,
            "get_user_info",
            side_effect=Exception("Discovery network failure"),
        ), patch("urllib.request.urlopen") as mock_urlopen, patch(
            "sys.stderr", new_callable=io.StringIO
        ) as mock_stderr:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(
                {"access_token": "acc", "refresh_token": "ref"}
            ).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            cli_client.complete_login("code_123", "verifier_123")
            self.assertIn(
                "Failed to auto-discover charge point settings",
                mock_stderr.getvalue(),
            )

        # Item 9: _request raises helpful NeoVacAPIError if refresh fails on 401
        cli_client.config["access_token"] = "expired_token"
        cli_client.config["refresh_token"] = "bad_refresh_token"

        def mock_401_urlopen(req: Any) -> Any:
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"")
            )

        with patch("urllib.request.urlopen", side_effect=mock_401_urlopen), patch.object(
            cli_client,
            "refresh_access_token",
            side_effect=Exception("invalid_grant"),
        ):
            with self.assertRaises(neovac_client.NeoVacAPIError) as ctx:
                cli_client._request("/mobile/api/user/info")
            self.assertIn("Session expired and token refresh failed", str(ctx.exception))
            self.assertIn("Please re-login", str(ctx.exception))

        # Item 7: stream_telemetry with transport='websocket' immediately re-raises if websockets missing
        with patch("neovac_client.HAS_WEBSOCKETS", False):
            with self.assertRaises(RuntimeError) as ctx_ws:
                list(
                    cli_client.stream_telemetry(
                        transport="websocket", duration_seconds=5
                    )
                )
            self.assertIn("websockets", str(ctx_ws.exception).lower())

        # Item 8: _stream_telemetry_long_polling raises NeoVacAPIError on 401
        with patch.object(
            cli_client, "_negotiate_signalr", return_value={"connectionToken": "token"}
        ), patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "url", 401, "Unauthorized", {}, io.BytesIO(b"")
            ),
        ):
            with self.assertRaises(neovac_client.NeoVacAPIError) as ctx_lp:
                list(
                    cli_client._stream_telemetry_long_polling(
                        "events", "token", duration_seconds=5
                    )
                )
            self.assertEqual(ctx_lp.exception.status_code, 401)

    async def test_cable_plugged_event_before_charging_button_pressed(self) -> None:
        """Verify cable plug-in emits EVENT_CABLE_PLUGGED_IN immediately upon connection,

        before the user presses the charge button, and pressing charge button emits
        EVENT_CHARGING_STARTED without a duplicate EVENT_CABLE_PLUGGED_IN.
        """
        hass = sys.modules["homeassistant.core"].HomeAssistant()
        entry = sys.modules["homeassistant.config_entries"].ConfigEntry(
            data={const.CONF_ENABLE_WEBSOCKET: False}
        )
        mock_client = MagicMock()
        mock_client.async_get_user_info = AsyncMock(
            return_value={
                "accountId": "ACC_TEST_01",
                "sites": [
                    {
                        "siteId": "SITE_TEST_01",
                        "siteName": "Garage",
                        "userChargePoints": [
                            {
                                "chargePointId": 1001,
                                "neoVacId": "NV-1001",
                                "serialNumber": "SN-PLUG-01",
                                "position": "Slot 1",
                                "status": "Available",
                                "connectorZeroStatus": "Available",
                                "isOnline": True,
                            }
                        ],
                    }
                ],
            }
        )
        mock_client.async_list_rfids = AsyncMock(return_value=[])
        mock_client.async_list_invoices = AsyncMock(return_value=[])
        mock_client.async_get_notification_settings = AsyncMock(return_value={})
        mock_client.async_get_charge_report = AsyncMock(return_value={})
        mock_client.async_get_analytics = AsyncMock(return_value={})
        mock_client.async_get_pricing = AsyncMock(return_value={})
        mock_client.async_get_guest_invitations = AsyncMock(return_value=[])

        coord = coordinator.NeoVacDataUpdateCoordinator(hass, entry, mock_client)
        await coord.async_config_entry_first_refresh()
        hass.data[const.DOMAIN] = {entry.entry_id: coord}

        sensors: list[Any] = []
        binary_sensors: list[Any] = []
        events: list[Any] = []

        await sensor.async_setup_entry(hass, entry, sensors.extend)
        await binary_sensor.async_setup_entry(hass, entry, binary_sensors.extend)
        await event.async_setup_entry(hass, entry, events.extend)
        for ev_ent in events:
            await ev_ent.async_added_to_hass()

        sensor_by_key = {s.entity_description.key: s for s in sensors}
        bs_by_key = {b.entity_description.key: b for b in binary_sensors}
        charging_event_entity = events[0]

        # Initial baseline: station is Available, cable is NOT plugged in
        self.assertEqual(sensor_by_key["station_status"].native_value, "Available")
        self.assertFalse(bs_by_key["cable_plugged_in"].is_on)
        self.assertFalse(bs_by_key["charging"].is_on)
        self.assertEqual(len(hass.fired_events), 0)

        # 1. User plugs in cable to station:
        # NeoVac sends ReceiveChargePointSiteStatusFullUpdate with status='Connected', ocppStatus='Available'
        await coord.async_handle_signalr_event(
            "ReceiveChargePointSiteStatusFullUpdate",
            [
                [
                    {
                        "chargePointSerialNo": "SN-PLUG-01",
                        "connectorId": 1,
                        "status": "Preparing",
                        "ocppStatus": "Preparing",
                        "isOnline": True,
                    }
                ]
            ],
        )

        # Verify: Cable plugged in event fired immediately!
        fired_types = [evt_type for evt_type, _ in hass.fired_events]
        self.assertIn("neovac_mycharge_cable_plugged_in", fired_types)
        self.assertEqual(fired_types.count("neovac_mycharge_cable_plugged_in"), 1)
        self.assertNotIn("neovac_mycharge_charging_started", fired_types)

        # Verify entities reflect plugged in state
        self.assertTrue(bs_by_key["cable_plugged_in"].is_on)
        self.assertFalse(bs_by_key["charging"].is_on)
        self.assertEqual(sensor_by_key["station_status"].native_value, "Preparing")
        self.assertEqual(sensor_by_key["connector_status"].native_value, "Preparing")
        self.assertEqual(charging_event_entity._last_triggered_event[0], const.EVENT_CABLE_PLUGGED_IN)

        # 2. User presses the "Charge" button in the app/station:
        # Station starts transaction -> StartTransactionNotification / status becomes Charging
        await coord.async_handle_signalr_event(
            "StartTransactionNotification",
            [
                {
                    "chargePointSerialNumber": "SN-PLUG-01",
                    "status": "Charging",
                    "ocppStatus": "Charging",
                    "transactionId": 555001,
                }
            ],
        )

        # Verify: charging_started fired, but cable_plugged_in was NOT fired again!
        fired_types = [evt_type for evt_type, _ in hass.fired_events]
        self.assertEqual(fired_types.count("neovac_mycharge_cable_plugged_in"), 1)
        self.assertEqual(fired_types.count("neovac_mycharge_charging_started"), 1)

        self.assertTrue(bs_by_key["cable_plugged_in"].is_on)
        self.assertTrue(bs_by_key["charging"].is_on)
        self.assertEqual(sensor_by_key["station_status"].native_value, "Charging")
        self.assertEqual(charging_event_entity._last_triggered_event[0], const.EVENT_CHARGING_STARTED)

        # 3. User stops charging (presses stop), but leaves cable plugged into vehicle:
        await coord.async_handle_signalr_event(
            "ReceiveChargePointSiteStatusUpdate",
            [
                {
                    "chargePointSerialNumber": "SN-PLUG-01",
                    "status": "Preparing",
                    "ocppStatus": "Preparing",
                    "isOnline": True,
                }
            ],
        )

        fired_types = [evt_type for evt_type, _ in hass.fired_events]
        self.assertIn("neovac_mycharge_charging_stopped", fired_types)
        self.assertEqual(fired_types.count("neovac_mycharge_cable_plugged_in"), 1)
        self.assertTrue(bs_by_key["cable_plugged_in"].is_on)
        self.assertFalse(bs_by_key["charging"].is_on)
        self.assertEqual(sensor_by_key["station_status"].native_value, "Preparing")

        # 4. User unplugs cable from vehicle:
        await coord.async_handle_signalr_event(
            "ReceiveChargePointSiteStatusUpdate",
            [
                {
                    "chargePointSerialNumber": "SN-PLUG-01",
                    "status": "Available",
                    "ocppStatus": "Available",
                    "isOnline": True,
                }
            ],
        )

        fired_types = [evt_type for evt_type, _ in hass.fired_events]
        self.assertIn("neovac_mycharge_cable_unplugged", fired_types)
        self.assertFalse(bs_by_key["cable_plugged_in"].is_on)
        self.assertEqual(sensor_by_key["station_status"].native_value, "Available")
        self.assertEqual(charging_event_entity._last_triggered_event[0], const.EVENT_CABLE_UNPLUGGED)

    async def test_adaptive_polling_fallback_on_websocket_disconnect(self) -> None:
        """Verify dynamic scan interval: 59s when WebSocket drops, restored to 15m (or configured) on reconnect."""
        _install_ha_stubs()
        from datetime import timedelta
        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.neovac_mycharge import coordinator, const
        from custom_components.neovac_mycharge.api import NeoVacConnectionError

        hass = sys.modules["homeassistant.core"].HomeAssistant()
        entry = sys.modules["homeassistant.config_entries"].ConfigEntry(
            data={
                const.CONF_ENABLE_WEBSOCKET: True,
                const.CONF_SCAN_INTERVAL: 900,
            }
        )
        client = MagicMock()
        coord = coordinator.NeoVacDataUpdateCoordinator(hass, entry, client)
        coord.async_request_refresh = AsyncMock()

        # Initially, interval is the base interval (900s)
        self.assertEqual(coord.update_interval, timedelta(seconds=900))
        self.assertEqual(coord._base_scan_interval, 900)

        # Simulate stop / disconnect
        await coord.async_stop_websocket_listeners()
        self.assertEqual(coord.update_interval, timedelta(seconds=const.FALLBACK_SCAN_INTERVAL))
        self.assertEqual(coord.update_interval, timedelta(seconds=59))

        # Test hub loop connect and disconnect transitions
        coord._stop_event.clear()
        attempt = 0

        async def mock_stream_signalr_hub(
            hub_name: str,
            event_callback: Any,
            charge_point_ids: Any = None,
            stop_event: Any = None,
            on_connected: Any = None,
            **kwargs: Any,
        ) -> None:
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                # Connected -> interval should restore to 900s
                if on_connected:
                    on_connected()
                self.assertEqual(coord.update_interval, timedelta(seconds=900))
                # Now connection drops
                raise NeoVacConnectionError("WebSocket dropped")
            elif attempt == 2:
                # Before reconnecting, verify the interval was set to fallback 59s upon disconnect
                self.assertEqual(coord.update_interval, timedelta(seconds=59))
                # Reconnected -> interval restored to 900s
                if on_connected:
                    on_connected()
                self.assertEqual(coord.update_interval, timedelta(seconds=900))
                coord._stop_event.set()

        client.async_stream_signalr_hub = mock_stream_signalr_hub

        with patch("asyncio.sleep", return_value=None):
            await coord._async_hub_loop(const.HUB_EVENTS, [1001])

        # Verify interval was adjusted to 59s after drop and then 900s on reconnect
        self.assertEqual(attempt, 2)


if __name__ == "__main__":
    unittest.main()
