"""Switch platform for NeoVac myCharge V."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import NeoVacApiError
from .const import DOMAIN, STATUS_CHARGING
from .coordinator import NeoVacDataUpdateCoordinator, _get_ci
from .entity import NeoVacEntity


@dataclass(frozen=True, kw_only=True)
class NeoVacSwitchEntityDescription(SwitchEntityDescription):
    """Describes a NeoVac myCharge V switch entity."""

    is_on_fn: Callable[[dict[str, Any]], bool]
    turn_on_fn: Callable[[NeoVacDataUpdateCoordinator, int], Awaitable[Any]]
    turn_off_fn: Callable[[NeoVacDataUpdateCoordinator, int], Awaitable[Any]]


def _get_settings(bundle: dict[str, Any]) -> dict[str, Any]:
    info = bundle.get("info", {})
    settings = _get_ci(info, "chargePointSettings", default={})
    return settings if isinstance(settings, dict) else {}


SWITCH_DESCRIPTIONS: tuple[NeoVacSwitchEntityDescription, ...] = (
    NeoVacSwitchEntityDescription(
        key="charging_control",
        translation_key="charging_control",
        name="Charging",
        device_class=SwitchDeviceClass.SWITCH,
        icon="mdi:ev-station",
        is_on_fn=lambda b: (
            _get_ci(b.get("info", {}), "status", "connectorZeroStatus")
            == STATUS_CHARGING
        ),
        turn_on_fn=lambda coord, cp_id: coord.client.async_start_charging(
            cp_id, rfid_tag=coord.selected_rfid_tags.get(cp_id)
        ),
        turn_off_fn=lambda coord, cp_id: coord.client.async_stop_charging(cp_id),
    ),
    NeoVacSwitchEntityDescription(
        key="permanent_cable_lock",
        translation_key="permanent_cable_lock",
        name="Permanent Cable Lock",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:lock",
        is_on_fn=lambda b: bool(
            _get_ci(_get_settings(b), "permanentCableLockEnabled", default=False)
        ),
        turn_on_fn=lambda coord, cp_id: coord.client.async_set_permanent_cable_lock(
            cp_id, True
        ),
        turn_off_fn=lambda coord, cp_id: coord.client.async_set_permanent_cable_lock(
            cp_id, False
        ),
    ),
    NeoVacSwitchEntityDescription(
        key="night_charging",
        translation_key="night_charging",
        name="Night Charging Schedule",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:weather-night",
        is_on_fn=lambda b: bool(
            _get_ci(_get_settings(b), "nightChargingEnabled", default=False)
        ),
        turn_on_fn=lambda coord, cp_id: coord.client.async_set_night_charging(
            cp_id, True
        ),
        turn_off_fn=lambda coord, cp_id: coord.client.async_set_night_charging(
            cp_id, False
        ),
    ),
    NeoVacSwitchEntityDescription(
        key="plug_and_charge",
        translation_key="plug_and_charge",
        name="Plug & Charge",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:power-plug",
        is_on_fn=lambda b: bool(
            _get_ci(_get_settings(b), "plugAndChargeEnabled", default=False)
        ),
        turn_on_fn=lambda coord, cp_id: coord.client.async_set_plug_and_charge(
            cp_id, True
        ),
        turn_off_fn=lambda coord, cp_id: coord.client.async_set_plug_and_charge(
            cp_id, False
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NeoVac myCharge V switch entities."""
    coordinator: NeoVacDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NeoVacSwitchEntity] = []

    charge_points = coordinator.data.get("charge_points", {}) if coordinator.data else {}
    for cp_id in charge_points:
        for description in SWITCH_DESCRIPTIONS:
            entities.append(NeoVacSwitchEntity(coordinator, cp_id, description))

    async_add_entities(entities)


class NeoVacSwitchEntity(NeoVacEntity, SwitchEntity):
    """Representation of a NeoVac myCharge V switch."""

    entity_description: NeoVacSwitchEntityDescription

    def __init__(
        self,
        coordinator: NeoVacDataUpdateCoordinator,
        charge_point_id: int,
        description: NeoVacSwitchEntityDescription,
    ) -> None:
        super().__init__(coordinator, charge_point_id)
        self.entity_description = description
        self._attr_unique_id = f"{self.charge_point_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        """Return True if the switch is on."""
        return self.entity_description.is_on_fn(self.cp_bundle)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            await self.entity_description.turn_on_fn(
                self.coordinator, self.charge_point_id
            )
            await self.coordinator.async_request_refresh()
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            await self.entity_description.turn_off_fn(
                self.coordinator, self.charge_point_id
            )
            await self.coordinator.async_request_refresh()
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err
