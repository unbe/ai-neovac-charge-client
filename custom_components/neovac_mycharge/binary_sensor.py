"""Binary sensor platform for NeoVac myCharge V."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    PLUGGED_IN_STATUSES,
    STATUS_CHARGING,
    STATUS_FAULTED,
    canonical_status,
    is_plugged_in_status,
)
from .coordinator import NeoVacDataUpdateCoordinator, _get_ci
from .entity import NeoVacEntity


@dataclass(frozen=True, kw_only=True)
class NeoVacBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a NeoVac myCharge V binary sensor."""

    is_on_fn: Callable[[dict[str, Any]], bool]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


BINARY_SENSOR_DESCRIPTIONS: tuple[NeoVacBinarySensorEntityDescription, ...] = (
    NeoVacBinarySensorEntityDescription(
        key="online",
        translation_key="online",
        name="Online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=lambda b: bool(_get_ci(b.get("info", {}), "isOnline", default=False)),
    ),
    NeoVacBinarySensorEntityDescription(
        key="cable_plugged_in",
        translation_key="cable_plugged_in",
        name="Cable Plugged In",
        device_class=BinarySensorDeviceClass.PLUG,
        is_on_fn=lambda b: (
            is_plugged_in_status(
                canonical_status(b.get("info", {}).get("status"))
            )
        ),
    ),
    NeoVacBinarySensorEntityDescription(
        key="charging",
        translation_key="charging",
        name="Charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda b: (
            canonical_status(b.get("info", {}).get("status"))
            == STATUS_CHARGING
        ),
    ),
    NeoVacBinarySensorEntityDescription(
        key="problem",
        translation_key="problem",
        name="Problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda b: (
            _get_ci(b.get("info", {}), "status") == STATUS_FAULTED
            or bool(_get_ci(b.get("live_transaction", {}), "errorCharging", default=False))
            or (
                _get_ci(b.get("live_transaction", {}), "errorCode") not in (
                    None,
                    "",
                    "0",
                    "None",
                    "NoError",
                )
            )
        ),
        attr_fn=lambda b: {
            "error_code": _get_ci(b.get("live_transaction", {}), "errorCode"),
            "vendor_error_code": _get_ci(
                b.get("live_transaction", {}), "vendorErrorCode"
            ),
            "error_charging": bool(
                _get_ci(b.get("live_transaction", {}), "errorCharging", default=False)
            ),
        },
    ),
    NeoVacBinarySensorEntityDescription(
        key="maintenance_mode",
        translation_key="maintenance_mode",
        name="Maintenance Mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wrench-clock",
        is_on_fn=lambda b: bool(
            _get_ci(b.get("info", {}), "isInMaintenanceMode", default=False)
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NeoVac myCharge V binary sensor entities."""
    coordinator: NeoVacDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NeoVacBinarySensorEntity] = []

    charge_points = coordinator.data.get("charge_points", {}) if coordinator.data else {}
    for cp_id in charge_points:
        for description in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(NeoVacBinarySensorEntity(coordinator, cp_id, description))

    async_add_entities(entities)


class NeoVacBinarySensorEntity(NeoVacEntity, BinarySensorEntity):
    """Representation of a NeoVac myCharge V binary sensor."""

    entity_description: NeoVacBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: NeoVacDataUpdateCoordinator,
        charge_point_id: int,
        description: NeoVacBinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, charge_point_id)
        self.entity_description = description
        self._attr_unique_id = f"{self.charge_point_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        """Return True if the binary sensor is on."""
        return self.entity_description.is_on_fn(self.cp_bundle)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        if self.entity_description.attr_fn:
            return self.entity_description.attr_fn(self.cp_bundle)
        return None
