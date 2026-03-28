"""Button platform for EKZ Tariff."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EkzTariffCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EkzTariffCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EkzBaselineRefreshButton(coordinator, entry)])


class EkzBaselineRefreshButton(ButtonEntity):
    """Button to force a baseline tariff re-fetch."""

    _attr_has_entity_name = True
    _attr_name = "Baseline neu laden"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_baseline_refresh"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}

    async def async_press(self) -> None:
        await self.coordinator.async_force_baseline_refresh()
