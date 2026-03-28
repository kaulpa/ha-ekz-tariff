"""Sensor platform for EKZ Tariff."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEBUG_MODE,
    CONF_MAX_PRICE_CHF_PER_KWH,
    CONF_MAX_RETRIES_INVALID_DATA,
    CONF_MAX_RETRIES_NO_DATA,
    CONF_MIN_PRICE_CHF_PER_KWH,
    CONF_MIN_SLOTS_PER_DAY,
    CONF_PUBLISH_TIME,
    CONF_RETRY_INTERVAL_MINUTES,
    DEFAULT_MAX_PRICE_CHF_PER_KWH,
    DEFAULT_MAX_RETRIES_INVALID_DATA,
    DEFAULT_MAX_RETRIES_NO_DATA,
    DEFAULT_MIN_PRICE_CHF_PER_KWH,
    DEFAULT_MIN_SLOTS_PER_DAY,
    DEFAULT_PUBLISH_TIME,
    DEFAULT_RETRY_INTERVAL_MINUTES,
    DOMAIN,
)
from .coordinator import EkzTariffCoordinator, PriceSlot

_SETTINGS_KEYS = (
    CONF_PUBLISH_TIME,
    CONF_MIN_SLOTS_PER_DAY,
    CONF_MIN_PRICE_CHF_PER_KWH,
    CONF_MAX_PRICE_CHF_PER_KWH,
    CONF_MAX_RETRIES_NO_DATA,
    CONF_MAX_RETRIES_INVALID_DATA,
    CONF_RETRY_INTERVAL_MINUTES,
    CONF_DEBUG_MODE,
)

_SETTINGS_DEFAULTS = {
    CONF_PUBLISH_TIME: DEFAULT_PUBLISH_TIME,
    CONF_MIN_SLOTS_PER_DAY: DEFAULT_MIN_SLOTS_PER_DAY,
    CONF_MIN_PRICE_CHF_PER_KWH: DEFAULT_MIN_PRICE_CHF_PER_KWH,
    CONF_MAX_PRICE_CHF_PER_KWH: DEFAULT_MAX_PRICE_CHF_PER_KWH,
    CONF_MAX_RETRIES_NO_DATA: DEFAULT_MAX_RETRIES_NO_DATA,
    CONF_MAX_RETRIES_INVALID_DATA: DEFAULT_MAX_RETRIES_INVALID_DATA,
    CONF_RETRY_INTERVAL_MINUTES: DEFAULT_RETRY_INTERVAL_MINUTES,
    CONF_DEBUG_MODE: False,
}


def _device_info(entry: ConfigEntry) -> dict[str, Any]:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.title,
        "manufacturer": "EKZ",
        "model": "Tariff API",
    }


def _active_slots(coordinator: EkzTariffCoordinator) -> list[PriceSlot]:
    data = coordinator.data or {}
    slots = data.get("active", []) if isinstance(data, dict) else []
    return slots if isinstance(slots, list) else []


def _current_slot(slots: list[PriceSlot]) -> PriceSlot | None:
    if not slots:
        return None
    now = dt_util.utcnow()
    current: PriceSlot | None = None
    for slot in sorted(slots, key=lambda s: s.start):
        if slot.start <= now:
            current = slot
        else:
            break
    return current or slots[0]


def _next_slot(slots: list[PriceSlot]) -> PriceSlot | None:
    if not slots:
        return None
    now = dt_util.utcnow()
    for slot in sorted(slots, key=lambda s: s.start):
        if slot.start > now:
            return slot
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: EkzTariffCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        EkzPriceCurveSensor(coordinator, entry),
        EkzPriceNowSensor(coordinator, entry),
        EkzBaselinePriceNowSensor(coordinator, entry),
        EkzNextPriceSensor(coordinator, entry),
        EkzPublicationTimestampSensor(coordinator, entry),
        EkzLinkStatusSensor(coordinator, entry),
        EkzLastApiSuccessSensor(coordinator, entry),
        EkzActivityLogSensor(coordinator, entry),
        EkzSettingsSensor(coordinator, entry),
    ]
    async_add_entities(entities, update_before_add=False)


class EkzPriceCurveSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    """Price curve with all slots as attributes."""

    _attr_has_entity_name = True
    _attr_name = "Price curve"
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_curve"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int | None:
        slots = _active_slots(self.coordinator)
        return len(slots) if slots else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slots = _active_slots(self.coordinator)
        data = self.coordinator.data or {}
        pub_ts = data.get("publication_timestamp")
        if not slots:
            return {"slot_count": 0}
        integrated = [s.integrated_chf_per_kwh for s in slots]
        return {
            "slot_count": len(slots),
            "publication_timestamp": pub_ts.isoformat() if isinstance(pub_ts, datetime) else None,
            "first_slot_start_utc": slots[0].start.isoformat(),
            "last_slot_start_utc": slots[-1].start.isoformat(),
            "min_price": round(min(integrated), 6) if integrated else None,
            "max_price": round(max(integrated), 6) if integrated else None,
            "slots": [
                {
                    "start": s.start.isoformat(),
                    "integrated": round(s.integrated_chf_per_kwh, 6),
                    "electricity": round(s.electricity_chf_per_kwh, 6),
                    "grid": round(s.grid_chf_per_kwh, 6),
                    "regional_fees": round(s.regional_fees_chf_per_kwh, 6),
                }
                for s in slots
            ],
        }


class EkzPriceNowSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    """Current all-in price (integrated)."""

    _attr_has_entity_name = True
    _attr_name = "Price now"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:cash"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_now"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float | None:
        slot = _current_slot(_active_slots(self.coordinator))
        return round(slot.integrated_chf_per_kwh, 6) if slot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slot = _current_slot(_active_slots(self.coordinator))
        if not slot:
            return {}
        return {
            "slot_start_utc": slot.start.isoformat(),
            "electricity": round(slot.electricity_chf_per_kwh, 6),
            "grid": round(slot.grid_chf_per_kwh, 6),
            "regional_fees": round(slot.regional_fees_chf_per_kwh, 6),
            "feed_in": round(slot.feed_in_chf_per_kwh, 6),
        }


class EkzBaselinePriceNowSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    """Baseline price for current quarter."""

    _attr_has_entity_name = True
    _attr_name = "Baseline price now"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:cash-sync"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_baseline_price_now"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        value = data.get("baseline_chf_per_kwh")
        return round(float(value), 6) if isinstance(value, (int, float)) else None


class EkzNextPriceSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    """Next slot all-in price."""

    _attr_has_entity_name = True
    _attr_name = "Next price"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_next_price"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float | None:
        slot = _next_slot(_active_slots(self.coordinator))
        return round(slot.integrated_chf_per_kwh, 6) if slot else None


class EkzPublicationTimestampSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    """Publication timestamp of the tariff data."""

    _attr_has_entity_name = True
    _attr_name = "Publication timestamp"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_publication_timestamp"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data or {}
        value = data.get("publication_timestamp")
        return value if isinstance(value, datetime) else None


class EkzLinkStatusSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    """EMS link status."""

    _attr_has_entity_name = True
    _attr_name = "Link status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:link"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_link_status"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        value = data.get("link_status")
        return str(value) if value is not None else None


class EkzLastApiSuccessSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    """Last successful API call timestamp."""

    _attr_has_entity_name = True
    _attr_name = "Last API success"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-check-outline"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_last_api_success"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data or {}
        value = data.get("last_api_success")
        return value if isinstance(value, datetime) else None


class EkzActivityLogSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    """Activity log sensor."""

    _attr_has_entity_name = True
    _attr_name = "Activity Log"
    _attr_icon = "mdi:history"
    _attr_should_poll = True

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ekz_activity_log"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str:
        if not self.coordinator.activity_log:
            return "Keine Aktivität"
        latest = self.coordinator.activity_log[0]
        return f"{latest.get('icon', '')} {latest.get('msg', '')}"[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "entries": self.coordinator.activity_log,
            "count": len(self.coordinator.activity_log),
        }


class EkzSettingsSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    """Exposes current EKZ Tariff settings for the settings card."""

    _attr_has_entity_name = True
    _attr_name = "Settings"
    _attr_icon = "mdi:cog"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_settings"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str:
        return "configured"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        opts = dict(self._entry.data)
        opts.update(dict(self._entry.options))
        settings: dict[str, Any] = {}
        for key in _SETTINGS_KEYS:
            val = opts.get(key, _SETTINGS_DEFAULTS.get(key))
            settings[key] = val
        settings["baseline_chf_per_kwh"] = self.coordinator.baseline_chf_per_kwh
        settings["baseline_quarter"] = self.coordinator.baseline_quarter
        return {"settings": settings, "entry_id": self._entry.entry_id}
