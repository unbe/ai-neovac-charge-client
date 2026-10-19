"""Select platform for NeoVac myCharge V (RFID Badge selector for remote start)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_RFID_OPTION, DOMAIN
from .coordinator import NeoVacDataUpdateCoordinator, _get_ci
from .entity import NeoVacEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NeoVac myCharge V select entities."""
    coordinator: NeoVacDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NeoVacRfidSelectEntity] = []

    charge_points = coordinator.data.get("charge_points", {}) if coordinator.data else {}
    for cp_id in charge_points:
        entities.append(NeoVacRfidSelectEntity(coordinator, cp_id))

    async_add_entities(entities)


class NeoVacRfidSelectEntity(NeoVacEntity, SelectEntity):
    """Select entity to choose which RFID badge to use when remote-starting a session."""

    _attr_translation_key = "rfid_badge"
    _attr_name = "Remote Start RFID Badge"
    _attr_icon = "mdi:card-account-details"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: NeoVacDataUpdateCoordinator,
        charge_point_id: int,
    ) -> None:
        super().__init__(coordinator, charge_point_id)
        self._attr_unique_id = f"{self.charge_point_id}_rfid_badge"

    def _build_option_map(self) -> dict[str, str | None]:
        """Maps display option strings to raw RFID hex tags."""
        mapping: dict[str, str | None] = {DEFAULT_RFID_OPTION: None}
        rfids = (
            self.coordinator.data.get("rfids", []) if self.coordinator.data else []
        )
        for r in rfids:
            tag = _get_ci(r, "rfidTag")
            name = _get_ci(r, "rfidName")
            if tag:
                label = f"{name} ({tag})" if name else str(tag)
                mapping[label] = str(tag)
        return mapping

    @property
    def options(self) -> list[str]:
        """Return the list of available RFID badge options."""
        return list(self._build_option_map().keys())

    @property
    def current_option(self) -> str:
        """Return the currently selected option."""
        selected_tag = self.coordinator.selected_rfid_tags.get(self.charge_point_id)
        if not selected_tag:
            return DEFAULT_RFID_OPTION
        for label, tag in self._build_option_map().items():
            if tag == selected_tag:
                return label
        return DEFAULT_RFID_OPTION

    async def async_select_option(self, option: str) -> None:
        """Change the selected RFID badge."""
        mapping = self._build_option_map()
        self.coordinator.selected_rfid_tags[self.charge_point_id] = mapping.get(option)
        self.async_write_ha_state()
