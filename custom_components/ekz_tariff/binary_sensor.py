"""Binary sensors for EKZ Tariff error states."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

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
        EkzTariffDateValidBinarySensor(coordinator, entry, 0, "Today data valid", "mdi:calendar-today"),
        EkzTariffDateValidBinarySensor(coordinator, entry, 1, "Tomorrow data valid", "mdi:calendar-arrow-right"),
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


class EkzTariffDateValidBinarySensor(CoordinatorEntity[EkzTariffCoordinator], BinarySensorEntity):
    """Binary sensor that reflects date-based data validity (problem = invalid data)."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EkzTariffCoordinator,
        entry: ConfigEntry,
        day_offset: int,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._day_offset = day_offset
        self._attr_name = name
        self._attr_icon = icon
        suffix = "today_data_valid" if day_offset == 0 else "tomorrow_data_valid"
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"

    def _get_target_date(self) -> str:
        return (dt_util.now().date() + timedelta(days=self._day_offset)).isoformat()

    def _get_validity(self) -> dict[str, Any] | None:
        return self.coordinator.date_validity.get(self._get_target_date())

    @property
    def is_on(self) -> bool:
        """Return True if data for this date is INVALID (problem sensor)."""
        val = self._get_validity()
        if val is None:
            # No validation record yet — not an error
            return False
        return not val.get("valid", True)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        val = self._get_validity() or {}
        return {
            "date": self._get_target_date(),
            "valid": val.get("valid"),
            "error": val.get("error"),
            "details": val.get("details"),
            "slot_count": val.get("slot_count"),
            "expected_slots": val.get("expected_slots"),
            "retry_count": val.get("retry_count"),
            "validated_at": val.get("validated_at"),
        }
