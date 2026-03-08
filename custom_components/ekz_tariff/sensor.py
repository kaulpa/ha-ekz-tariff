"""Sensor platform for EKZ Tariff."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import EkzTariffCoordinator, PriceSlot

# IMPORTANT:
# EKZ exposes an "integrated" component, but the real all-in tariff used in the
# EKZ tariff sheet is electricity + grid + regional_fees.
# Therefore "price_allin_now" must NOT sum "integrated" together with the other
# components, otherwise the price is counted twice.
ALLIN_COMPONENTS: tuple[str, ...] = (
    "electricity",
    "grid",
    "regional_fees",
)
COMPONENT_KEYS: tuple[str, ...] = (
    "electricity",
    "grid",
    "regional_fees",
    "metering",
    "integrated",
)
BASELINE_COMPONENT_KEYS: tuple[str, ...] = (
    "electricity",
)


def _device_info(entry: ConfigEntry) -> dict[str, Any]:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.title,
        "manufacturer": "EKZ",
        "model": "Tariff API",
    }


def _slots(coordinator: EkzTariffCoordinator, kind: str) -> list[PriceSlot]:
    data = coordinator.data or {}
    values = data.get(kind, []) if isinstance(data, dict) else []
    return values if isinstance(values, list) else []


def _current_slot(slots: list[PriceSlot]) -> PriceSlot | None:
    if not slots:
        return None
    slots = sorted(slots, key=lambda s: s.start)
    now = dt_util.utcnow()
    current: PriceSlot | None = None
    for slot in slots:
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


def _curve_attrs(slots: list[PriceSlot], publication_timestamp: datetime | None) -> dict[str, Any]:
    if not slots:
        return {"slot_count": 0, "publication_timestamp": publication_timestamp.isoformat() if publication_timestamp else None}
    prices = [float(s.electricity_chf_per_kwh) for s in slots if s.electricity_chf_per_kwh is not None]
    return {
        "slot_count": len(slots),
        "publication_timestamp": publication_timestamp.isoformat() if publication_timestamp else None,
        "first_slot_start_utc": slots[0].start.isoformat(),
        "last_slot_start_utc": slots[-1].start.isoformat(),
        "min_price": round(min(prices), 6) if prices else None,
        "max_price": round(max(prices), 6) if prices else None,
    }


def _tomorrow_slots(slots: list[PriceSlot]) -> list[PriceSlot]:
    local_tz = dt_util.DEFAULT_TIME_ZONE
    tomorrow = dt_util.now().date() + timedelta(days=1)
    return [slot for slot in slots if slot.start.astimezone(local_tz).date() == tomorrow]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: EkzTariffCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        EkzTariffPriceCurveSensor(coordinator, entry, "active", "price_curve", "Price curve"),
        EkzTariffPriceCurveSensor(coordinator, entry, "baseline", "baseline_price_curve", "Baseline price curve"),
        EkzTariffPriceNowSensor(coordinator, entry, "active", "price_now", "Price now"),
        EkzTariffPriceNowSensor(coordinator, entry, "baseline", "baseline_price_now", "Baseline price now"),
        EkzTariffNextPriceSensor(coordinator, entry, "active", "price_next", "Next price"),
        EkzTariffNextPriceSensor(coordinator, entry, "baseline", "baseline_price_next", "Baseline next price"),
        EkzTariffPriceAllInNowSensor(coordinator, entry, "active", "price_allin_now", "Price all-in now"),
        EkzTariffTomorrowAvailableSensor(coordinator, entry),
        EkzTariffTomorrowSlotCountSensor(coordinator, entry),
        EkzTariffPublicationTimestampSensor(coordinator, entry, False),
        EkzTariffPublicationTimestampSensor(coordinator, entry, True),
        EkzTariffLinkStatusSensor(coordinator, entry),
        EkzTariffLinkingUrlSensor(coordinator, entry),
        EkzTariffLastApiSuccessSensor(coordinator, entry),
    ]

    for component in COMPONENT_KEYS:
        entities.append(EkzTariffPriceComponentNowSensor(coordinator, entry, component, False))

    for component in BASELINE_COMPONENT_KEYS:
        entities.append(EkzTariffPriceComponentNowSensor(coordinator, entry, component, True))

    async_add_entities(entities, update_before_add=True)


class EkzTariffPriceCurveSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry, kind: str, suffix: str, name: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int | None:
        slots = _slots(self.coordinator, self._kind)
        return len(slots) if slots else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        publication_timestamp = data.get(
            "baseline_publication_timestamp" if self._kind == "baseline" else "active_publication_timestamp"
        )
        tariff_name = data.get("baseline_tariff_name") if self._kind == "baseline" else "myEKZ"
        attrs = _curve_attrs(_slots(self.coordinator, self._kind), publication_timestamp)
        attrs["tariff_name"] = tariff_name
        return attrs


class EkzTariffPriceNowSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:currency-chf"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry, kind: str, suffix: str, name: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float | None:
        slot = _current_slot(_slots(self.coordinator, self._kind))
        return float(slot.electricity_chf_per_kwh) if slot else None


class EkzTariffNextPriceSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry, kind: str, suffix: str, name: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float | None:
        slot = _next_slot(_slots(self.coordinator, self._kind))
        return float(slot.electricity_chf_per_kwh) if slot else None


class EkzTariffPriceAllInNowSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:cash"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry, kind: str, suffix: str, name: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float | None:
        slot = _current_slot(_slots(self.coordinator, self._kind))
        if not slot:
            return None
        comps = slot.components_chf_per_kwh or {}
        total = sum(float(comps.get(c, 0.0) or 0.0) for c in ALLIN_COMPONENTS)
        return round(total, 6) if total else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slot = _current_slot(_slots(self.coordinator, self._kind))
        if not slot:
            return {}
        comps = slot.components_chf_per_kwh or {}
        summed = sum(float(comps.get(c, 0.0) or 0.0) for c in ALLIN_COMPONENTS)
        api_integrated = comps.get("integrated")
        return {
            "slot_start_utc": slot.start.isoformat(),
            "sum_components": round(summed, 6),
            "api_integrated": float(api_integrated) if isinstance(api_integrated, (int, float)) else None,
            "components_used": list(ALLIN_COMPONENTS),
            "calculation_note": "all-in is calculated from electricity + grid + regional_fees",
        }


class EkzTariffTomorrowAvailableSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-check"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "Tomorrow available"
        self._attr_unique_id = f"{entry.entry_id}_tomorrow_available"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> bool:
        return len(_tomorrow_slots(_slots(self.coordinator, "active"))) > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tomorrow_slots = _tomorrow_slots(_slots(self.coordinator, "active"))
        return {
            "tomorrow_slot_count": len(tomorrow_slots),
            "first_tomorrow_slot_utc": tomorrow_slots[0].start.isoformat() if tomorrow_slots else None,
        }


class EkzTariffTomorrowSlotCountSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "Tomorrow slot count"
        self._attr_unique_id = f"{entry.entry_id}_tomorrow_slot_count"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int:
        return len(_tomorrow_slots(_slots(self.coordinator, "active")))


class EkzTariffPriceComponentNowSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:currency-chf"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry, component: str, baseline: bool) -> None:
        super().__init__(coordinator)
        self._component = component
        self._kind = "baseline" if baseline else "active"
        prefix = "Baseline " if baseline else ""
        unique_prefix = "baseline_" if baseline else ""
        self._attr_name = f"{prefix}price now {component}"
        self._attr_unique_id = f"{entry.entry_id}_{unique_prefix}price_now_{component}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_unit_of_measurement(self) -> str:
        return "CHF/month" if self._component == "metering" else "CHF/kWh"

    @property
    def native_value(self) -> float | None:
        slot = _current_slot(_slots(self.coordinator, self._kind))
        if not slot:
            return None
        if self._component == "metering":
            value = (slot.components_chf_per_month or {}).get(self._component)
        else:
            value = (slot.components_chf_per_kwh or {}).get(self._component)
        return float(value) if isinstance(value, (int, float)) else None


class EkzTariffPublicationTimestampSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry, baseline: bool) -> None:
        super().__init__(coordinator)
        self._baseline = baseline
        prefix = "Baseline " if baseline else ""
        suffix = "baseline_publication_timestamp" if baseline else "publication_timestamp"
        self._attr_name = f"{prefix}publication timestamp"
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data or {}
        key = "baseline_publication_timestamp" if self._baseline else "active_publication_timestamp"
        value = data.get(key)
        return value if isinstance(value, datetime) else None


class EkzTariffLinkStatusSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:link"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "Link status"
        self._attr_unique_id = f"{entry.entry_id}_link_status"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        value = data.get("link_status")
        return str(value) if value is not None else None


class EkzTariffLinkingUrlSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:link-variant"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "Linking URL"
        self._attr_unique_id = f"{entry.entry_id}_linking_url"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        value = data.get("linking_url")
        return "available" if value else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        value = data.get("linking_url")
        return {"url": value} if value else {}


class EkzTariffLastApiSuccessSensor(CoordinatorEntity[EkzTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-check-outline"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "Last API success"
        self._attr_unique_id = f"{entry.entry_id}_last_api_success"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data or {}
        value = data.get("last_api_success")
        return value if isinstance(value, datetime) else None
