"""Button platform for NeoVac myCharge V."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import NeoVacApiError
from .const import DOMAIN
from .coordinator import NeoVacDataUpdateCoordinator
from .entity import NeoVacEntity


@dataclass(frozen=True, kw_only=True)
class NeoVacButtonEntityDescription(ButtonEntityDescription):
    """Describes a NeoVac myCharge V button entity."""

    press_fn: Callable[[NeoVacDataUpdateCoordinator, int], Awaitable[Any]]


BUTTON_DESCRIPTIONS: tuple[NeoVacButtonEntityDescription, ...] = (
    NeoVacButtonEntityDescription(
        key="start_charging",
        translation_key="start_charging",
        name="Start Charging",
        icon="mdi:play-circle-outline",
        press_fn=lambda coord, cp_id: coord.client.async_start_charging(
            cp_id, rfid_tag=coord.selected_rfid_tags.get(cp_id)
        ),
    ),
    NeoVacButtonEntityDescription(
        key="stop_charging",
        translation_key="stop_charging",
        name="Stop Charging",
        icon="mdi:stop-circle-outline",
        press_fn=lambda coord, cp_id: coord.client.async_stop_charging(cp_id),
    ),
    NeoVacButtonEntityDescription(
        key="request_email_report",
        translation_key="request_email_report",
        name="Send Email Report (30 Days)",
        icon="mdi:email-fast-outline",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coord, cp_id: coord.client.async_request_email_report(cp_id),
    ),
    NeoVacButtonEntityDescription(
        key="refresh_data",
        translation_key="refresh_data",
        name="Refresh Station Data",
        icon="mdi:refresh",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda coord, cp_id: coord.async_request_refresh(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NeoVac myCharge V button entities."""
    coordinator: NeoVacDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NeoVacButtonEntity] = []

    charge_points = coordinator.data.get("charge_points", {}) if coordinator.data else {}
    for cp_id in charge_points:
        for description in BUTTON_DESCRIPTIONS:
            entities.append(NeoVacButtonEntity(coordinator, cp_id, description))

    async_add_entities(entities)


class NeoVacButtonEntity(NeoVacEntity, ButtonEntity):
    """Representation of a NeoVac myCharge V button."""

    entity_description: NeoVacButtonEntityDescription

    def __init__(
        self,
        coordinator: NeoVacDataUpdateCoordinator,
        charge_point_id: int,
        description: NeoVacButtonEntityDescription,
    ) -> None:
        super().__init__(coordinator, charge_point_id)
        self.entity_description = description
        self._attr_unique_id = f"{self.charge_point_id}_{description.key}"

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            await self.entity_description.press_fn(
                self.coordinator, self.charge_point_id
            )
            if self.entity_description.key in ("start_charging", "stop_charging"):
                await self.coordinator.async_request_refresh()
        except NeoVacApiError as err:
            raise HomeAssistantError(err.explanation) from err
