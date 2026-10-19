"""Text platform for NeoVac myCharge V (Station Display Name)."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import NeoVacApiError
from .const import DOMAIN
from .coordinator import NeoVacDataUpdateCoordinator, _get_ci
from .entity import NeoVacEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NeoVac myCharge V text entities."""
    coordinator: NeoVacDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NeoVacStationNameTextEntity] = []

    charge_points = coordinator.data.get("charge_points", {}) if coordinator.data else {}
    for cp_id in charge_points:
        entities.append(NeoVacStationNameTextEntity(coordinator, cp_id))

    async_add_entities(entities)


class NeoVacStationNameTextEntity(NeoVacEntity, TextEntity):
    """Text entity to read and update the custom station display name."""

    _attr_translation_key = "station_name"
    _attr_name = "Station Name"
    _attr_icon = "mdi:rename-box"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min = 1
    _attr_native_max = 64

    def __init__(
        self,
        coordinator: NeoVacDataUpdateCoordinator,
        charge_point_id: int,
    ) -> None:
        super().__init__(coordinator, charge_point_id)
        self._attr_unique_id = f"{self.charge_point_id}_station_name"

    @property
    def native_value(self) -> str | None:
        """Return the current display name / position of the station."""
        val = _get_ci(self.cp_info, "position", "name")
        return str(val) if val is not None else None

    async def async_set_value(self, value: str) -> None:
        """Set a new display name for the charging station."""
        clean_name = value.strip()
        if not clean_name:
            raise HomeAssistantError("Station name cannot be empty.")
        try:
            await self.coordinator.client.async_rename_station(
                self.charge_point_id, clean_name
            )
            await self.coordinator.async_request_refresh()
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err
