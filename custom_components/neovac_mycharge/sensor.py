"""Sensor platform for NeoVac myCharge V."""

from __future__ import annotations

import logging
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

from .const import (
    ALL_STATION_STATUSES,
    DOMAIN,
    STATUS_CHARGING,
    STATUS_UNKNOWN,
    canonical_status,
)
from .coordinator import NeoVacDataUpdateCoordinator, _get_ci
from .entity import NeoVacEntity

_LOGGER = logging.getLogger(__name__)


def _parse_timestamp(ts: Any) -> datetime | None:
    """Parses an ISO 8601 string or datetime into a UTC datetime."""
    if not ts or isinstance(ts, bool):
        return None
    if isinstance(ts, datetime):
        return dt_util.as_utc(ts)
    if isinstance(ts, str):
        val = ts.strip()
        if not val:
            return None
        try:
            parsed = dt_util.parse_datetime(val)
            if parsed is not None:
                return dt_util.as_utc(parsed)
        except Exception as err:
            _LOGGER.warning("Failed to parse timestamp '%s': %s", ts, err)
    return None


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
            except ValueError as err:
                _LOGGER.warning("Failed to parse days in duration '%s': %s", raw_val, err)
                days = 0
        parts = val.split(":")
        if len(parts) == 3:
            try:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)
            except ValueError as err:
                _LOGGER.warning("Failed to parse HMS in duration '%s': %s", raw_val, err)
                return None
        else:
            _LOGGER.warning("Unexpected duration format '%s'", raw_val)
    return None


def _get_report_sessions(report: Any) -> list[dict[str, Any]]:
    """Extracts the list of individual charging session records from a report payload."""
    if isinstance(report, list):
        return report
    if not isinstance(report, dict):
        return []
    for key in (
        "chargeReport",
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


def _calculate_session_energy(
    coord: NeoVacDataUpdateCoordinator, b: dict[str, Any]
) -> float:
    """Extracts active session accumulated energy in kWh directly from live transaction."""
    live_tx = b.get("live_transaction", {})
    raw_val = _get_ci(
        live_tx,
        "accumulatedEnergyKwH",
        "chargeQuantityInkWh",
        "totalEnergyKwh",
    )
    if raw_val is not None:
        try:
            return round(float(raw_val), 3)
        except (ValueError, TypeError) as err:
            _LOGGER.warning("Failed to parse session energy '%s': %s", raw_val, err)

    return 0.0


def _calculate_session_duration(
    coord: NeoVacDataUpdateCoordinator, b: dict[str, Any]
) -> float:
    """Calculates active session duration in seconds strictly from live transaction."""
    live_tx = b.get("live_transaction", {})

    raw_dur = _get_ci(live_tx, "duration", "totalDuration", "sessionDuration")
    parsed_dur = _parse_duration_seconds(raw_dur)
    if parsed_dur is not None and parsed_dur > 0:
        return parsed_dur

    start_str = _get_ci(
        live_tx,
        "startTime",
        "chargeProcessStartTime",
        "transactionStartTime",
        "startedAt",
        "startDate",
        "start",
    )
    if start_str:
        start_dt = _parse_timestamp(start_str)
        if start_dt:
            has_end = bool(
                _get_ci(live_tx, "end", "hasEnd", "transactionEnded", default=False)
            )
            end_str = _get_ci(
                live_tx,
                "endTime",
                "chargeProcessEndTime",
                "transactionEndTime",
                "endedAt",
                "endDate",
                "end",
            )
            end_dt = (
                _parse_timestamp(end_str)
                if end_str and not isinstance(end_str, bool)
                else None
            )
            if end_dt:
                diff = (end_dt - start_dt).total_seconds()
                return max(0.0, float(round(diff)))
            elif not has_end:
                now = dt_util.utcnow()
                diff = (now - start_dt).total_seconds()
                return max(0.0, float(round(diff)))

    return parsed_dur if parsed_dur is not None else 0.0


def _calculate_session_cost(
    coord: NeoVacDataUpdateCoordinator, b: dict[str, Any]
) -> float:
    """Returns active session cost in CHF strictly from live transaction without dynamic calculation."""
    live_tx = b.get("live_transaction", {})

    raw_cost = _get_ci(live_tx, "chargePriceInChf", "totalPrice", "costChf")
    if raw_cost is not None:
        try:
            val = float(raw_cost)
            if val >= 0:
                return round(val, 2)
        except (ValueError, TypeError):
            _LOGGER.warning("Failed to parse chargePriceInChf '%s' as float", raw_cost)

    return 0.0


def _calculate_report_30d_energy(
    coord: NeoVacDataUpdateCoordinator, b: dict[str, Any]
) -> float:
    """Calculates 30-day energy from aggregate report field or sums session quantities."""
    rep = b.get("report") or {}
    total = _get_ci(
        rep,
        "totalChargingQuantityInkWh",
        "chargeQuantityInkWh",
        "totalEnergyKwh",
    )
    if total is not None:
        try:
            val = float(total)
            if val >= 0:
                return round(val, 2)
        except (ValueError, TypeError):
            pass

    sessions = _get_report_sessions(rep)
    if sessions:
        total_kwh = sum(
            float(
                _get_ci(s, "chargeQuantityInkWh", "accumulatedEnergyKwH", default=0.0)
                or 0.0
            )
            for s in sessions
        )
        return round(total_kwh, 2)

    return 0.0


def _calculate_report_30d_cost(
    coord: NeoVacDataUpdateCoordinator, b: dict[str, Any]
) -> float:
    """Calculates 30-day cost from aggregate report field or sums session costs."""
    rep = b.get("report") or {}
    total = _get_ci(
        rep,
        "totalPrice",
        "chargePriceInChf",
        "totalCostChf",
    )
    if total is not None:
        try:
            val = float(total)
            if val >= 0:
                return round(val, 2)
        except (ValueError, TypeError):
            pass

    sessions = _get_report_sessions(rep)
    if sessions:
        total_chf = sum(
            float(
                _get_ci(s, "chargePriceInChf", "totalPrice", default=0.0)
                or 0.0
            )
            for s in sessions
        )
        return round(total_chf, 2)

    return 0.0


def _resolve_rfid_display(
    tag: str | None,
    rfids: list[dict[str, Any]],
    account_id: str | None = None,
) -> str | None:
    """Formats raw RFID tag for display: 'Name (TAG)' if registered, 'App (ACCOUNT_ID)' if account ID, else raw TAG."""
    if not tag:
        return None
    clean = str(tag).strip()
    if not clean:
        return None

    # Check registered badges
    for r in rfids:
        r_tag = str(_get_ci(r, "rfidTag", default="")).strip()
        if r_tag.upper() == clean.upper():
            name = _get_ci(r, "rfidName", default="")
            return f"{name} ({clean})" if name else clean

    # If backend returned user account ID because session was started via app (no physical badge used)
    if account_id and clean.upper() == str(account_id).strip().upper():
        return f"App ({clean})"

    return clean


@dataclass(frozen=True, kw_only=True)
class NeoVacSensorEntityDescription(SensorEntityDescription):
    """Describes a NeoVac myCharge V sensor entity."""

    value_fn: Callable[[NeoVacDataUpdateCoordinator, dict[str, Any]], Any]
    attr_fn: Callable[
        [NeoVacDataUpdateCoordinator, dict[str, Any]], dict[str, Any]
    ] | None = None
    available_fn: Callable[
        [NeoVacDataUpdateCoordinator, dict[str, Any]], bool
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
            canonical_status(b.get("info", {}).get("status"))
        ),
        attr_fn=lambda coord, b: {
            "neovac_id": _get_ci(b.get("info", {}), "neoVacId"),
            "serial_number": _get_ci(b.get("info", {}), "serialNumber"),
            "position": _get_ci(b.get("info", {}), "position"),
            "site_id": _get_ci(b.get("info", {}), "siteId"),
            "site_name": _get_ci(b.get("info", {}), "siteName"),
            "connector_zero_status": canonical_status(
                _get_ci(b.get("info", {}), "connectorZeroStatus")
            ),
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
            canonical_status(b.get("info", {}).get("status"))
        ),
        attr_fn=lambda coord, b: {
            "connector_id": _get_ci(b.get("info", {}), "connectorNumber", default=1),
            "connector_zero_status": canonical_status(
                _get_ci(b.get("info", {}), "connectorZeroStatus")
            ),
        },
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
            float(b.get("live_transaction", {}).get("currentPowerKw", 0.0))
            if canonical_status(b.get("info", {}).get("status")) == STATUS_CHARGING
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
        value_fn=_calculate_session_energy,
        attr_fn=lambda coord, b: {
            "transaction_id": _get_ci(
                b.get("live_transaction", {}), "transactionId"
            ),
            "rfid_tag": _get_ci(b.get("live_transaction", {}), "rfidTag"),
        },
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
        value_fn=_calculate_session_cost,
        attr_fn=lambda coord, b: {
            "price_per_kwh": _get_ci(b.get("live_transaction", {}), "pricePerkWh"),
        },
    ),
    NeoVacSensorEntityDescription(
        key="session_duration",
        translation_key="session_duration",
        name="Session Duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
        value_fn=_calculate_session_duration,
        attr_fn=lambda coord, b: {
            "start_time": _get_ci(
                b.get("live_transaction", {}),
                "startTime",
                "chargeProcessStartTime",
                "startedAt",
                "start",
            ),
            "end_time": _get_ci(
                b.get("live_transaction", {}),
                "endTime",
                "chargeProcessEndTime",
                "endedAt",
                "end",
            ),
        },
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
            _get_ci(b.get("live_transaction", {}), "rfidTag"),
            (coord.data.get("rfids") or []) if coord.data else [],
            account_id=(
                _get_ci(b.get("live_transaction", {}), "accountId")
                or (coord.data.get("account_id") if coord.data else None)
            ),
        ),
    ),
    NeoVacSensorEntityDescription(
        key="electricity_tariff",
        translation_key="electricity_tariff",
        name="Electricity Tariff",
        native_unit_of_measurement="CHF/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        icon="mdi:currency-chf",
        available_fn=lambda coord, b: (
            b.get("pricing") is not None
            or _get_ci(b.get("live_transaction", {}), "pricePerkWh") is not None
        ),
        value_fn=lambda coord, b: (
            float(
                _get_ci(b.get("live_transaction", {}), "pricePerkWh")
                or _get_ci(
                    b.get("pricing") or {},
                    "defaultTariffPrice",
                    "pricePerkWh",
                )
            )
            if (
                _get_ci(b.get("live_transaction", {}), "pricePerkWh") is not None
                or _get_ci(b.get("pricing") or {}, "defaultTariffPrice", "pricePerkWh")
                is not None
            )
            else None
        ),
        attr_fn=lambda coord, b: {
            "default_tariff_name": _get_ci(
                b.get("pricing") or {}, "defaultTariffName"
            ),
            "tariff_schedule": _get_ci(
                b.get("pricing") or {}, "tariffTimeDetails", default=[]
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
        available_fn=lambda coord, b: b.get("report") is not None,
        value_fn=_calculate_report_30d_energy,
        attr_fn=lambda coord, b: {
            "sessions_count": len(_get_report_sessions(b.get("report") or {})),
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
        available_fn=lambda coord, b: b.get("report") is not None,
        value_fn=_calculate_report_30d_cost,
    ),
    NeoVacSensorEntityDescription(
        key="report_30d_sessions",
        translation_key="report_30d_sessions",
        name="30-Day Sessions",
        native_unit_of_measurement="sessions",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:counter",
        available_fn=lambda coord, b: b.get("report") is not None,
        value_fn=lambda coord, b: len(_get_report_sessions(b.get("report") or {})),
    ),
    NeoVacSensorEntityDescription(
        key="last_session_energy",
        translation_key="last_session_energy",
        name="Last Session Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        icon="mdi:history",
        available_fn=lambda coord, b: b.get("report") is not None,
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    _get_last_session(b.get("report") or {}),
                    "chargeQuantityInkWh",
                )
            )
            if (
                _get_last_session(b.get("report") or {})
                and _get_ci(
                    _get_last_session(b.get("report") or {}),
                    "chargeQuantityInkWh",
                ) is not None
            )
            else None
        ),
        attr_fn=lambda coord, b: {
            "end_time": _get_ci(
                _get_last_session(b.get("report") or {}),
                "chargeProcessEndTime",
                "endTime",
            ),
            "cost_chf": _get_ci(
                _get_last_session(b.get("report") or {}), "chargePriceInChf"
            ),
            "rfid_tag": _get_ci(_get_last_session(b.get("report") or {}), "rfidTag"),
            "user_name": _get_ci(_get_last_session(b.get("report") or {}), "userName"),
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
        available_fn=lambda coord, b: b.get("report") is not None,
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    _get_last_session(b.get("report") or {}),
                    "chargePriceInChf",
                    "totalPrice",
                )
            )
            if (
                _get_last_session(b.get("report") or {})
                and _get_ci(
                    _get_last_session(b.get("report") or {}),
                    "chargePriceInChf",
                    "totalPrice",
                ) is not None
            )
            else None
        ),
    ),
    NeoVacSensorEntityDescription(
        key="last_session_end",
        translation_key="last_session_end",
        name="Last Session End",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check-outline",
        available_fn=lambda coord, b: b.get("report") is not None,
        value_fn=lambda coord, b: (
            dt_util.parse_datetime(
                str(
                    _get_ci(
                        _get_last_session(b.get("report") or {}),
                        "chargeProcessEndTime",
                        "endTime",
                        default="",
                    )
                )
            )
            if _get_last_session(b.get("report") or {})
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
        available_fn=lambda coord, b: b.get("analytics") is not None,
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    b.get("analytics") or {},
                    "totalChargingQuantityInkWh",
                    "chargeQuantityInkWh",
                )
            )
            if (
                b.get("analytics")
                and _get_ci(
                    b.get("analytics") or {},
                    "totalChargingQuantityInkWh",
                    "chargeQuantityInkWh",
                ) is not None
            )
            else None
        ),
        attr_fn=lambda coord, b: {
            "analytics_data": b.get("analytics") or {},
        },
    ),
    NeoVacSensorEntityDescription(
        key="registered_rfids",
        translation_key="registered_rfids",
        name="Registered RFID Badges",
        native_unit_of_measurement="badges",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:credit-card-wireless",
        available_fn=lambda coord, b: coord.data is not None and coord.data.get("rfids") is not None,
        value_fn=lambda coord, b: len(
            coord.data.get("rfids") or [] if coord.data else []
        ),
        attr_fn=lambda coord, b: {
            "badges": [
                {
                    "id": _get_ci(r, "id"),
                    "name": _get_ci(r, "rfidName"),
                    "tag": _get_ci(r, "rfidTag"),
                }
                for r in ((coord.data.get("rfids") or []) if coord.data else [])
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
        available_fn=lambda coord, b: coord.data is not None and coord.data.get("invoices") is not None,
        value_fn=lambda coord, b: (
            float(
                _get_ci(
                    coord.data["invoices"][0],
                    "totalAmount",
                    "amount",
                    "totalPrice",
                )
            )
            if (
                coord.data
                and coord.data.get("invoices")
                and _get_ci(
                    coord.data["invoices"][0],
                    "totalAmount",
                    "amount",
                    "totalPrice",
                ) is not None
            )
            else None
        ),
        attr_fn=lambda coord, b: (
            {
                "invoice_count": len(coord.data.get("invoices") or []),
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
        available_fn=lambda coord, b: b.get("guest_invitations") is not None,
        value_fn=lambda coord, b: len(b.get("guest_invitations") or []),
        attr_fn=lambda coord, b: {
            "invitations": b.get("guest_invitations") or [],
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
    def available(self) -> bool:
        """Return True if entity is available."""
        if not super().available:
            return False
        if self.entity_description.available_fn is not None:
            return self.entity_description.available_fn(self.coordinator, self.cp_bundle)
        return True

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
