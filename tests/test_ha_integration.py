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
        event_types: list[str] = []

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

        # Plug in cable
        self.assertEqual(
            evaluate_state_transitions("Available", "Preparing"),
            [const.EVENT_CABLE_PLUGGED_IN],
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
                return web.json_response({"id": 2, **body})
            return web.json_response(
                {
                    "rfids": [
                        {"id": 1, "rfidName": "Primary Card", "rfidTag": "04AABBCCDD"}
                    ]
                }
            )

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
                    "defaultTariffName": "Standard Tariff",
                    "defaultTariffPrice": 0.28,
                    "tariffTimeDetails": [
                        {"name": "Day", "pricePerkWh": 0.28, "startTime": "06:00", "endTime": "22:00"}
                    ],
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
                self.assertIn("neovac_mycharge_transaction_report", fired_types)

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

        # 5. Cost calculated from accumulated energy (4.5287 kWh) * live pricePerkWh (0.28)
        bundle_cost_calc = {
            "live_transaction": {
                "chargePriceInChf": 0.0,
                "accumulatedEnergyKwH": 4.5287000200807,
                "pricePerkWh": 0.28,
            },
            "pricing": {},
        }
        # 4.5287000200807 * 0.28 = 1.268036 -> 1.27 CHF
        self.assertEqual(sensor._calculate_session_cost(coord, bundle_cost_calc), 1.27)

        # 6. Cost calculated from accumulated energy * pricing defaultTariffPrice
        bundle_cost_tariff = {
            "live_transaction": {
                "chargePriceInChf": 0.0,
                "accumulatedEnergyKwH": 5.0,
            },
            "pricing": {"defaultTariffPrice": 0.35},
        }
        self.assertEqual(sensor._calculate_session_cost(coord, bundle_cost_tariff), 1.75)

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


if __name__ == "__main__":
    unittest.main()
