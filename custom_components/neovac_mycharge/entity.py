"""Base entity for the NeoVac myCharge V integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import API_BASE, DOMAIN, MANUFACTURER, MODEL
from .coordinator import NeoVacDataUpdateCoordinator, _get_ci


class NeoVacEntity(CoordinatorEntity[NeoVacDataUpdateCoordinator]):
    """Base entity representing a NeoVac myCharge V charging station component."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NeoVacDataUpdateCoordinator,
        charge_point_id: int,
    ) -> None:
        super().__init__(coordinator)
        self.charge_point_id = int(charge_point_id)

    @property
    def cp_bundle(self) -> dict[str, Any]:
        """Returns the full coordinator data bundle for this charge point."""
        if not self.coordinator.data:
            return {}
        return (
            self.coordinator.data.get("charge_points", {}).get(self.charge_point_id, {})
        )

    @property
    def cp_info(self) -> dict[str, Any]:
        """Returns the station info dictionary for this charge point."""
        return self.cp_bundle.get("info", {})

    @property
    def live_tx(self) -> dict[str, Any]:
        """Returns the active live transaction report dictionary."""
        return self.cp_bundle.get("live_transaction", {})

    @property
    def device_info(self) -> DeviceInfo:
        """Returns Home Assistant DeviceInfo for this charging station."""
        info = self.cp_info
        position = _get_ci(info, "position") or f"Charge Point {self.charge_point_id}"
        neovac_id = _get_ci(info, "neoVacId")
        serial = _get_ci(info, "serialNumber")
        site_name = _get_ci(info, "siteName")

        name = f"{position} ({neovac_id})" if neovac_id else position

        return DeviceInfo(
            identifiers={(DOMAIN, str(self.charge_point_id))},
            name=name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            serial_number=serial,
            suggested_area=site_name or None,
            configuration_url=API_BASE,
        )
