"""Event platform for NeoVac myCharge V charging and station events."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALL_CHARGING_EVENT_TYPES, DOMAIN
from .coordinator import NeoVacDataUpdateCoordinator
from .entity import NeoVacEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NeoVac myCharge V event entities."""
    coordinator: NeoVacDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NeoVacChargingEventEntity] = []

    charge_points = coordinator.data.get("charge_points", {}) if coordinator.data else {}
    for cp_id in charge_points:
        entities.append(NeoVacChargingEventEntity(coordinator, cp_id))

    async_add_entities(entities)


class NeoVacChargingEventEntity(NeoVacEntity, EventEntity):
    """Event entity emitting charging lifecycle and station configuration events."""

    _attr_translation_key = "charging_event"
    _attr_name = "Charging Event"
    _attr_icon = "mdi:calendar-Alert"
    _attr_event_types = ALL_CHARGING_EVENT_TYPES

    def __init__(
        self,
        coordinator: NeoVacDataUpdateCoordinator,
        charge_point_id: int,
    ) -> None:
        super().__init__(coordinator, charge_point_id)
        self._attr_unique_id = f"{self.charge_point_id}_charging_event"

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator events when added to Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.register_event_listener(
                self.charge_point_id, self._async_handle_event
            )
        )

    @callback
    def _async_handle_event(
        self, event_type: str, event_data: dict[str, Any]
    ) -> None:
        """Trigger the Home Assistant EventEntity state update."""
        if event_type in self.event_types:
            self._trigger_event(event_type, event_data)
            self.async_write_ha_state()
