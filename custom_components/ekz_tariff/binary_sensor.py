"""Binary sensors for EKZ Tariff error states."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EkzTariffCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EKZ Tariff binary sensors."""
    coordinator: EkzTariffCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EkzTariffErrorBinarySensor(coordinator, entry, "no_data_error", "No data error", "mdi:database-off"),
        EkzTariffErrorBinarySensor(coordinator, entry, "invalid_data_error", "Invalid data error", "mdi:alert-circle"),
        EkzTariffErrorBinarySensor(coordinator, entry, "auth_error", "Auth error", "mdi:key-alert"),
        EkzTariffErrorBinarySensor(coordinator, entry, "baseline_error", "Baseline error", "mdi:chart-line-variant"),
    ], update_before_add=False)


class EkzTariffErrorBinarySensor(CoordinatorEntity[EkzTariffCoordinator], BinarySensorEntity):
    """Binary sensor that reflects an error flag on the coordinator."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EkzTariffCoordinator,
        entry: ConfigEntry,
        flag_name: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._flag_name = flag_name
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{flag_name}"

    @property
    def is_on(self) -> bool:
        """Return True if the error flag is set."""
        return getattr(self.coordinator, self._flag_name, False)
