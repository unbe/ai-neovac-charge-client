"""Sensor platform for NeoVac myCharge V."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import ALL_STATION_STATUSES, DOMAIN, STATUS_CHARGING, STATUS_UNKNOWN
from .coordinator import NeoVacDataUpdateCoordinator, _get_ci
from .entity import NeoVacEntity


def _parse_duration_seconds(raw_val: Any) -> float | None:
    """Converts a C# TimeSpan string ('HH:MM:SS' or 'd.HH:MM:SS') or numeric seconds to seconds."""
    if raw_val is None:
        return None
    if isinstance(raw_val, (int, float)):
        return float(raw_val)
    if isinstance(raw_val, str):
        val = raw_val.strip()
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            pass
        days = 0
        if "." in val and ":" in val and val.index(".") < val.index(":"):
            day_part, val = val.split(".", 1)
            try:
                days = int(day_part)
            except ValueError:
                days = 0
        parts = val.split(":")
        if len(parts) == 3:
            try:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)
            except ValueError:
                return None
    return None


def _get_report_sessions(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Extracts the list of individual charging session records from a report payload."""
    for key in (
        "chargePointChargeReports",
        "chargeProcesses",
        "processes",
        "sessions",
        "items",
        "reports",
    ):
        val = _get_ci(report, key)
        if isinstance(val, list):
            return val
    return []


def _get_last_session(report: dict[str, Any]) -> dict[str, Any]:
    """Returns the most recent completed charging session from the report."""
    sessions = _get_report_sessions(report)
    if not sessions:
        return {}
    # Sort by chargeProcessEndTime if present
    sorted_sessions = sorted(
        sessions,
        key=lambda s: str(_get_ci(s, "chargeProcessEndTime", "endTime", default="")),
        reverse=True,
    )
    return sorted_sessions[0] if sorted_sessions else {}


def _resolve_rfid_display(
    tag: str | None, rfids: list[dict[str, Any]]
) -> str | None:
    """Maps a raw RFID hex tag to 'Name (TAG)' if registered."""
    if not tag:
        return None
    clean = tag.strip().upper()
    for r in rfids:
        r_tag = str(_get_ci(r, "rfidTag", default="")).strip().upper()
        if r_tag == clean:
            name = _get_ci(r, "rfidName", default="")
            return f"{name} ({clean})" if name else clean
    return clean


@dataclass(frozen=True, kw_only=True)
class NeoVacSensorEntityDescription(SensorEntityDescription):
    """Describes a NeoVac myCharge V sensor entity."""

    value_fn: Callable[[NeoVacDataUpdateCoordinator, dict[str, Any]], Any]
    attr_fn: Callable[
        [NeoVacDataUpdateCoordinator, dict[str, Any]], dict[str, Any]
    ] | None = None


SENSOR_DESCRIPTIONS: tuple[NeoVacSensorEntityDescription, ...] = (
    NeoVacSensorEntityDescription(
        key="station_status",
        translation_key="station_status",
        name="Station Status",
        device_class=SensorDeviceClass.ENUM,
        options=ALL_STATION_STATUSES,
        icon="mdi:ev-station",
        value_fn=lambda coord, b: (
            _get_ci(b.get("info", {}), "status")
            if _get_ci(b.get("info", {}), "status") in ALL_STATION_STATUSES
            else STATUS_UNKNOWN
        ),
        attr_fn=lambda coord, b: {
            "neovac_id": _get_ci(b.get("info", {}), "neoVacId"),
            "serial_number": _get_ci(b.get("info", {}), "serialNumber"),
            "position": _get_ci(b.get("info", {}), "position"),
            "site_id": _get_ci(b.get("info", {}), "siteId"),
            "site_name": _get_ci(b.get("info", {}), "siteName"),
        },
    ),
    NeoVacSensorEntityDescription(
        key="connector_status",
        translation_key="connector_status",
        name="Connector Status",
        device_class=SensorDeviceClass.ENUM,
        options=ALL_STATION_STATUSES,
        icon="mdi:ev-plug-type2",
        value_fn=lambda coord, b: (
            _get_ci(b.get("info", {}), "connectorZeroStatus", "status")
            if _get_ci(b.get("info", {}), "connectorZeroStatus", "status")
            in ALL_STATION_STATUSES
            else STATUS_UNKNOWN
        ),
    ),
    NeoVacSensorEntityDescription(
        key="charging_power",
        translation_key="charging_power",
        name="Charging Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:flash",
        value_fn=lambda coord, b: (
            float(_get_ci(b.get("live_transaction", {}), "currentPowerKw", default=0.0))
            if _get_ci(b.get("info", {}), "status") == STATUS_CHARGING
            else 0.0
        ),
    ),
    NeoVacSensorEntityDescription(
        key="max_power",
        translation_key="max_power",
        name="Max Offered Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:speedometer",
        value_fn=lambda coord, b: (
            float(_get_ci(b.get("live_transaction", {}), "maxPowerKw"))
            if _get_ci(b.get("live_transaction", {}), "maxPowerKw") is not None
            else None
        ),
    ),
    NeoVacSensorEntityDescription(
        key="session_energy",
        translation_key="session_energy",
        name="Session Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:battery-charging-high",
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    b.get("live_transaction", {}),
                    "accumulatedEnergyKwH",
                    "chargeQuantityInkWh",
                    default=0.0,
                )
            )
        ),
    ),
    NeoVacSensorEntityDescription(
        key="session_cost",
        translation_key="session_cost",
        name="Session Cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="CHF",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:cash-multiple",
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    b.get("live_transaction", {}),
                    "chargePriceInChf",
                    "totalPrice",
                    default=0.0,
                )
            )
        ),
    ),
    NeoVacSensorEntityDescription(
        key="session_duration",
        translation_key="session_duration",
        name="Session Duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
        value_fn=lambda coord, b: _parse_duration_seconds(
            _get_ci(b.get("live_transaction", {}), "duration", default=0)
        ),
    ),
    NeoVacSensorEntityDescription(
        key="active_transaction_id",
        translation_key="active_transaction_id",
        name="Active Transaction ID",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:identifier",
        value_fn=lambda coord, b: _get_ci(
            b.get("live_transaction", {}), "transactionId"
        ),
    ),
    NeoVacSensorEntityDescription(
        key="active_rfid_badge",
        translation_key="active_rfid_badge",
        name="Active RFID Badge",
        icon="mdi:card-account-details-outline",
        value_fn=lambda coord, b: _resolve_rfid_display(
            _get_ci(b.get("live_transaction", {}), "rfidTag")
            or _get_ci(_get_last_session(b.get("report", {})), "rfidTag"),
            coord.data.get("rfids", []) if coord.data else [],
        ),
    ),
    NeoVacSensorEntityDescription(
        key="electricity_tariff",
        translation_key="electricity_tariff",
        name="Electricity Tariff",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="CHF/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        icon="mdi:currency-chf",
        value_fn=lambda coord, b: (
            float(
                _get_ci(b.get("live_transaction", {}), "pricePerkWh")
                or _get_ci(
                    b.get("pricing", {}),
                    "defaultTariffPrice",
                    "pricePerkWh",
                    default=0.0,
                )
            )
            if (
                _get_ci(b.get("live_transaction", {}), "pricePerkWh") is not None
                or _get_ci(b.get("pricing", {}), "defaultTariffPrice", "pricePerkWh")
                is not None
            )
            else None
        ),
        attr_fn=lambda coord, b: {
            "default_tariff_name": _get_ci(
                b.get("pricing", {}), "defaultTariffName"
            ),
            "tariff_schedule": _get_ci(
                b.get("pricing", {}), "tariffTimeDetails", default=[]
            ),
        },
    ),
    NeoVacSensorEntityDescription(
        key="report_30d_energy",
        translation_key="report_30d_energy",
        name="30-Day Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:lightning-bolt-circle",
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    b.get("report", {}),
                    "totalChargingQuantityInkWh",
                    "chargeQuantityInkWh",
                    "totalEnergyKwh",
                    default=0.0,
                )
            )
        ),
        attr_fn=lambda coord, b: {
            "sessions_count": len(_get_report_sessions(b.get("report", {}))),
        },
    ),
    NeoVacSensorEntityDescription(
        key="report_30d_cost",
        translation_key="report_30d_cost",
        name="30-Day Cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="CHF",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:cash-check",
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    b.get("report", {}),
                    "totalPrice",
                    "chargePriceInChf",
                    "totalCostChf",
                    default=0.0,
                )
            )
        ),
    ),
    NeoVacSensorEntityDescription(
        key="report_30d_sessions",
        translation_key="report_30d_sessions",
        name="30-Day Sessions",
        native_unit_of_measurement="sessions",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:counter",
        value_fn=lambda coord, b: len(_get_report_sessions(b.get("report", {}))),
    ),
    NeoVacSensorEntityDescription(
        key="last_session_energy",
        translation_key="last_session_energy",
        name="Last Session Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:history",
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    _get_last_session(b.get("report", {})),
                    "chargeQuantityInkWh",
                    default=0.0,
                )
            )
            if _get_last_session(b.get("report", {}))
            else None
        ),
        attr_fn=lambda coord, b: {
            "end_time": _get_ci(
                _get_last_session(b.get("report", {})),
                "chargeProcessEndTime",
                "endTime",
            ),
            "cost_chf": _get_ci(
                _get_last_session(b.get("report", {})), "chargePriceInChf"
            ),
            "rfid_tag": _get_ci(_get_last_session(b.get("report", {})), "rfidTag"),
            "user_name": _get_ci(_get_last_session(b.get("report", {})), "userName"),
        },
    ),
    NeoVacSensorEntityDescription(
        key="last_session_cost",
        translation_key="last_session_cost",
        name="Last Session Cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="CHF",
        suggested_display_precision=2,
        icon="mdi:receipt-text-clock",
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    _get_last_session(b.get("report", {})),
                    "chargePriceInChf",
                    default=0.0,
                )
            )
            if _get_last_session(b.get("report", {}))
            else None
        ),
    ),
    NeoVacSensorEntityDescription(
        key="last_session_end",
        translation_key="last_session_end",
        name="Last Session End",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check-outline",
        value_fn=lambda coord, b: (
            dt_util.parse_datetime(
                str(
                    _get_ci(
                        _get_last_session(b.get("report", {})),
                        "chargeProcessEndTime",
                        "endTime",
                        default="",
                    )
                )
            )
            if _get_last_session(b.get("report", {}))
            else None
        ),
    ),
    NeoVacSensorEntityDescription(
        key="monthly_analytics_energy",
        translation_key="monthly_analytics_energy",
        name="Monthly Analytics Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:chart-bar",
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    b.get("analytics", {}),
                    "totalChargingQuantityInkWh",
                    "chargeQuantityInkWh",
                    default=0.0,
                )
            )
            if b.get("analytics")
            else 0.0
        ),
        attr_fn=lambda coord, b: {
            "analytics_data": b.get("analytics", {}),
        },
    ),
    NeoVacSensorEntityDescription(
        key="registered_rfids",
        translation_key="registered_rfids",
        name="Registered RFID Badges",
        native_unit_of_measurement="badges",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:credit-card-wireless",
        value_fn=lambda coord, b: len(
            coord.data.get("rfids", []) if coord.data else []
        ),
        attr_fn=lambda coord, b: {
            "badges": [
                {
                    "id": _get_ci(r, "id"),
                    "name": _get_ci(r, "rfidName"),
                    "tag": _get_ci(r, "rfidTag"),
                }
                for r in (coord.data.get("rfids", []) if coord.data else [])
            ]
        },
    ),
    NeoVacSensorEntityDescription(
        key="latest_invoice",
        translation_key="latest_invoice",
        name="Latest Invoice",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="CHF",
        suggested_display_precision=2,
        icon="mdi:file-document-outline",
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    coord.data["invoices"][0],
                    "totalAmount",
                    "amount",
                    "totalPrice",
                    default=0.0,
                )
            )
            if coord.data and coord.data.get("invoices")
            else None
        ),
        attr_fn=lambda coord, b: (
            {
                "invoice_count": len(coord.data.get("invoices", [])),
                "latest_invoice": coord.data["invoices"][0],
            }
            if coord.data and coord.data.get("invoices")
            else {"invoice_count": 0}
        ),
    ),
    NeoVacSensorEntityDescription(
        key="guest_invitations",
        translation_key="guest_invitations",
        name="Guest Invitations",
        native_unit_of_measurement="invitations",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:account-multiple-plus",
        value_fn=lambda coord, b: len(b.get("guest_invitations", [])),
        attr_fn=lambda coord, b: {
            "invitations": b.get("guest_invitations", []),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NeoVac myCharge V sensor entities."""
    coordinator: NeoVacDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NeoVacSensorEntity] = []

    charge_points = coordinator.data.get("charge_points", {}) if coordinator.data else {}
    for cp_id in charge_points:
        for description in SENSOR_DESCRIPTIONS:
            entities.append(NeoVacSensorEntity(coordinator, cp_id, description))

    async_add_entities(entities)


class NeoVacSensorEntity(NeoVacEntity, SensorEntity):
    """Representation of a NeoVac myCharge V sensor."""

    entity_description: NeoVacSensorEntityDescription

    def __init__(
        self,
        coordinator: NeoVacDataUpdateCoordinator,
        charge_point_id: int,
        description: NeoVacSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, charge_point_id)
        self.entity_description = description
        self._attr_unique_id = f"{self.charge_point_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator, self.cp_bundle)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        if self.entity_description.attr_fn:
            return self.entity_description.attr_fn(self.coordinator, self.cp_bundle)
        return None
