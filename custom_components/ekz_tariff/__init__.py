"""EKZ Tariff integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import EkzTariffApi
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
    PLATFORMS,
)
from .coordinator import EkzTariffCoordinator
from .validator import validate_tomorrow_slots

_LOGGER = logging.getLogger(__name__)


def get_coordinator(hass: HomeAssistant, entry_id: str) -> EkzTariffCoordinator:
    """Return the coordinator for a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
    if not isinstance(coordinator, EkzTariffCoordinator):
        raise KeyError(f"EKZ Tariff entry not loaded: {entry_id}")
    return coordinator


def get_provider_data(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Return provider data for Tariff Saver.

    All prices are netto (without VAT).
    """
    coordinator = get_coordinator(hass, entry_id)
    data = coordinator.data or {}
    if not isinstance(data, Mapping):
        data = {}

    active_slots = data.get("active", [])
    baseline_chf = data.get("baseline_chf_per_kwh")

    # Generate baseline slots matching active slot times with fixed price
    baseline_slots = []
    if isinstance(baseline_chf, (int, float)) and isinstance(active_slots, list):
        from .coordinator import PriceSlot
        for slot in active_slots:
            baseline_slots.append(PriceSlot(
                start=slot.start,
                electricity_chf_per_kwh=float(baseline_chf),
                grid_chf_per_kwh=0.0,
                regional_fees_chf_per_kwh=0.0,
                metering_chf_per_kwh=0.0,
                integrated_chf_per_kwh=float(baseline_chf),
                feed_in_chf_per_kwh=0.0,
                components={"integrated": float(baseline_chf), "electricity": float(baseline_chf)},
            ))

    return {
        "entry_id": entry_id,
        "provider": DOMAIN,
        "active_slots": list(active_slots) if isinstance(active_slots, list) else [],
        "baseline_slots": baseline_slots,
        "active_publication_timestamp": data.get("publication_timestamp"),
        "link_status": data.get("link_status"),
        "last_api_success_utc": coordinator.last_api_success_utc,
    }


def get_first_provider_data(hass: HomeAssistant) -> dict[str, Any]:
    """Return provider data for the first loaded EKZ Tariff entry."""
    domain_data = hass.data.get(DOMAIN, {})
    for entry_id, coordinator in domain_data.items():
        if isinstance(coordinator, EkzTariffCoordinator):
            return get_provider_data(hass, entry_id)
    raise KeyError("No loaded EKZ Tariff entry found")


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = value.strip().split(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return 18, 15


def _has_tomorrow_slots(coordinator: EkzTariffCoordinator) -> bool:
    """Check if we have any slots for tomorrow in stored data."""
    tomorrow = (dt_util.now() + timedelta(days=1)).date()
    for ts_key in coordinator._stored_slots:
        parsed = dt_util.parse_datetime(ts_key)
        if parsed and dt_util.as_local(parsed).date() == tomorrow:
            return True
    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    config = dict(entry.data)
    config.update(dict(entry.options))
    config["entry_id"] = entry.entry_id

    session = async_get_clientsession(hass)
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    api = EkzTariffApi(session, oauth_session=oauth_session)

    coordinator = EkzTariffCoordinator(hass, api, config=config)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set the validation callback — runs inside _async_update_data after slots are merged
    coordinator.on_new_tomorrow_data = lambda target_date: _check_and_validate(target_date)

    # Load persisted data from storage (no API call)
    await coordinator.async_load_storage()

    # Read config values
    publish_time = config.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME)
    hour, minute = _parse_hhmm(publish_time)
    max_retries_no_data = int(config.get(CONF_MAX_RETRIES_NO_DATA, DEFAULT_MAX_RETRIES_NO_DATA))
    max_retries_invalid = int(config.get(CONF_MAX_RETRIES_INVALID_DATA, DEFAULT_MAX_RETRIES_INVALID_DATA))
    retry_interval_sec = int(config.get(CONF_RETRY_INTERVAL_MINUTES, DEFAULT_RETRY_INTERVAL_MINUTES)) * 60
    min_slots = int(config.get(CONF_MIN_SLOTS_PER_DAY, DEFAULT_MIN_SLOTS_PER_DAY))
    min_price = float(config.get(CONF_MIN_PRICE_CHF_PER_KWH, DEFAULT_MIN_PRICE_CHF_PER_KWH))
    max_price = float(config.get(CONF_MAX_PRICE_CHF_PER_KWH, DEFAULT_MAX_PRICE_CHF_PER_KWH))

    # Retry state (mutable, not persisted)
    retry_state: dict[str, Any] = {
        "no_data_count": 0,
        "invalid_data_count": 0,
        "pending_cancel": None,  # CALLBACK_TYPE from async_call_later
    }
    retry_state_key = f"{entry.entry_id}_retry_state"
    hass.data[DOMAIN][retry_state_key] = retry_state

    def _cancel_pending_retry() -> None:
        cancel = retry_state.get("pending_cancel")
        if callable(cancel):
            cancel()
            retry_state["pending_cancel"] = None

    async def _check_and_validate(target_date) -> None:
        """Validate fetched data and signal Tariff Saver on success."""
        try:
            await _check_and_validate_inner(target_date)
        except Exception as err:
            coordinator.log_activity("💥", f"Validierung Fehler: {err}")
            _LOGGER.error("EKZ: _check_and_validate failed: %s", err, exc_info=True)

    async def _check_and_validate_inner(target_date) -> None:
        tz = dt_util.get_time_zone(hass.config.time_zone)

        # 1. Check if we have slots at all
        if not _has_tomorrow_slots(coordinator):
            retry_state["no_data_count"] += 1
            count = retry_state["no_data_count"]
            coordinator.log_activity("⚠️", f"Keine Slots für {target_date} (Versuch {count}/{max_retries_no_data})")
            if count >= max_retries_no_data:
                coordinator.no_data_error = True
                coordinator.log_activity("❌", f"Keine Daten nach {count} Versuchen")
                _LOGGER.error("EKZ: No data for %s after %d retries", target_date, count)
                coordinator.async_set_updated_data(coordinator.data)
                return
            # Schedule retry
            retry_state["pending_cancel"] = async_call_later(hass, retry_interval_sec, _retry_callback)
            return

        # 2. Validate slots
        result = validate_tomorrow_slots(
            stored_slots=coordinator._stored_slots,
            target_date=target_date,
            tz=tz,
            min_slots=min_slots,
            min_price=min_price,
            max_price=max_price,
        )
        if not result.valid:
            retry_state["invalid_data_count"] += 1
            count = retry_state["invalid_data_count"]
            coordinator.log_activity(
                "⚠️",
                f"Ungültige Daten für {target_date}: {result.error} — {result.details} (Versuch {count}/{max_retries_invalid})",
            )
            if count >= max_retries_invalid:
                coordinator.invalid_data_error = True
                coordinator.log_activity("❌", f"Ungültige Daten nach {count} Versuchen: {result.details}")
                _LOGGER.error("EKZ: Invalid data for %s after %d retries: %s", target_date, count, result.details)
                coordinator.async_set_updated_data(coordinator.data)
                return
            # Schedule retry
            retry_state["pending_cancel"] = async_call_later(hass, retry_interval_sec, _retry_callback)
            return

        # 3. Compute baseline (if needed for new quarter)
        baseline_ok = await coordinator.async_compute_baseline()
        if not baseline_ok:
            coordinator.baseline_error = True
            coordinator.log_activity("❌", "Baseline-Berechnung fehlgeschlagen")
            _LOGGER.error("EKZ: Baseline computation failed")
            coordinator.async_set_updated_data(coordinator.data)
            return

        # 4. All good — reset errors and signal Tariff Saver
        coordinator.reset_error_flags()
        coordinator.async_set_updated_data(coordinator.data)

        date_str = target_date.strftime("%d.%m.%Y")
        coordinator.log_activity("✅", f"Tarife für {date_str} validiert ({result.slot_count} Slots)")
        _LOGGER.info(
            "EKZ: Tomorrow data validated for %s (%d slots), signaling tariff_saver",
            target_date, result.slot_count,
        )
        hass.bus.async_fire("ekz_tariff_new_data", {
            "date": str(target_date),
            "entry_id": entry.entry_id,
        })

    async def _daily_refresh(now) -> None:
        """Triggered at publish_time — fetch tomorrow's tariffs."""
        _cancel_pending_retry()
        retry_state["no_data_count"] = 0
        retry_state["invalid_data_count"] = 0
        coordinator.reset_error_flags()
        coordinator.async_set_updated_data(coordinator.data)

        coordinator.log_activity("⏰", f"Täglicher Fetch um {publish_time}")
        try:
            await coordinator.async_refresh()
        except Exception:
            pass
        # Validation runs inside _async_update_data via on_new_tomorrow_data callback

    async def _retry_callback(_now) -> None:
        """Retry fetch after failed validation."""
        retry_state["pending_cancel"] = None
        coordinator.log_activity("🔄", "Retry: erneuter Fetch")
        try:
            await coordinator.async_refresh()
        except Exception:
            pass
        # Validation runs inside _async_update_data via on_new_tomorrow_data callback

    async def _proactive_token_refresh(_now) -> None:
        """Keep OAuth token alive."""
        try:
            await oauth_session.async_ensure_token_valid()
            _LOGGER.debug("EKZ proactive token refresh OK")
        except Exception as err:
            _LOGGER.warning("EKZ proactive token refresh failed: %s", err)

    # Schedule timers
    hass.data[DOMAIN][f"{entry.entry_id}_unsub_daily"] = async_track_time_change(
        hass, _daily_refresh, hour=hour, minute=minute, second=0
    )
    hass.data[DOMAIN][f"{entry.entry_id}_unsub_token"] = async_track_time_interval(
        hass, _proactive_token_refresh, timedelta(hours=20)
    )

    # Activity log services
    async def handle_ekz_clear_log(call) -> None:
        coordinator.activity_log = []
        hass.bus.async_fire("ekz_tariff_activity_log_updated")

    async def handle_ekz_delete_log_entry(call) -> None:
        index = int(call.data.get("index", -1))
        if 0 <= index < len(coordinator.activity_log):
            coordinator.activity_log.pop(index)
            hass.bus.async_fire("ekz_tariff_activity_log_updated")

    async def handle_ekz_add_log_entry(call) -> None:
        coordinator.log_activity(str(call.data.get("icon", "ℹ️")), str(call.data.get("msg", "Test")))
        hass.bus.async_fire("ekz_tariff_activity_log_updated")

    hass.services.async_register(DOMAIN, "clear_activity_log", handle_ekz_clear_log)
    hass.services.async_register(DOMAIN, "delete_activity_log_entry", handle_ekz_delete_log_entry)
    hass.services.async_register(DOMAIN, "add_activity_log_entry", handle_ekz_add_log_entry)

    # Settings update service
    _FLOAT_KEYS = {CONF_MIN_PRICE_CHF_PER_KWH, CONF_MAX_PRICE_CHF_PER_KWH}
    _INT_KEYS = {CONF_MIN_SLOTS_PER_DAY, CONF_MAX_RETRIES_NO_DATA, CONF_MAX_RETRIES_INVALID_DATA, CONF_RETRY_INTERVAL_MINUTES}
    _BOOL_KEYS = {CONF_DEBUG_MODE}

    async def _update_setting_service(call) -> None:
        key = str(call.data.get("key", ""))
        value = call.data.get("value")
        if not key:
            return
        if key in _BOOL_KEYS:
            value = bool(value)
        elif key in _FLOAT_KEYS:
            value = float(value or 0)
        elif key in _INT_KEYS:
            value = int(value or 0)
        else:
            value = str(value or "")
        new_options = dict(entry.options)
        new_options[key] = value
        hass.config_entries.async_update_entry(entry, options=new_options)
        # Update coordinator debug_mode immediately
        if key == CONF_DEBUG_MODE:
            coordinator.debug_mode = value
        coordinator.async_set_updated_data(coordinator.data)

    hass.services.async_register(DOMAIN, "update_setting", _update_setting_service)

    # Manual refresh service (triggers full fetch + validation + signal chain)
    async def _force_refresh_service(call) -> None:
        coordinator.log_activity("🔄", "Manueller Refresh gestartet")
        _cancel_pending_retry()
        retry_state["no_data_count"] = 0
        retry_state["invalid_data_count"] = 0
        coordinator.reset_error_flags()
        try:
            await coordinator.async_refresh()
        except Exception as err:
            coordinator.log_activity("💥", f"Refresh Fehler: {err}")
        # Validation runs inside _async_update_data via on_new_tomorrow_data callback

    hass.services.async_register(DOMAIN, "force_refresh", _force_refresh_service)

    # Test service to send dispatcher signal directly
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # One-time cleanup: remove orphaned entities from previous code versions
    _cleanup_orphaned_entities(hass, entry)

    return True


def _cleanup_orphaned_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove orphaned entity registry entries from previous code versions."""
    eid = entry.entry_id
    valid_suffixes = {
        f"{eid}_price_curve",
        f"{eid}_price_now",
        f"{eid}_baseline_price_now",
        f"{eid}_next_price",
        f"{eid}_publication_timestamp",
        f"{eid}_link_status",
        f"{eid}_last_api_success",
        f"{eid}_ekz_activity_log",
        f"{eid}_baseline_refresh",
        # Binary sensors
        f"{eid}_settings",
        f"{eid}_no_data_error",
        f"{eid}_invalid_data_error",
        f"{eid}_auth_error",
        f"{eid}_baseline_error",
    }
    registry = er.async_get(hass)
    to_remove = [
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, eid)
        if entity.unique_id not in valid_suffixes
    ]
    for entity_id in to_remove:
        _LOGGER.info("EKZ: Removing orphaned entity %s", entity_id)
        registry.async_remove(entity_id)
    if to_remove:
        _LOGGER.info("EKZ: Cleaned up %d orphaned entities", len(to_remove))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    domain = hass.data.get(DOMAIN, {})
    for suffix in ("_unsub_daily", "_unsub_token"):
        unsub = domain.pop(f"{entry.entry_id}{suffix}", None)
        if callable(unsub):
            unsub()
    # Cancel pending retry
    retry_state = domain.pop(f"{entry.entry_id}_retry_state", None)
    if isinstance(retry_state, dict):
        cancel = retry_state.get("pending_cancel")
        if callable(cancel):
            cancel()
    # Remove services so they get re-registered with fresh closures on reload
    for svc in ("force_refresh", "update_setting", "clear_activity_log", "delete_activity_log_entry", "add_activity_log_entry"):
        if hass.services.has_service(DOMAIN, svc):
            hass.services.async_remove(DOMAIN, svc)
    if unload_ok:
        domain.pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
