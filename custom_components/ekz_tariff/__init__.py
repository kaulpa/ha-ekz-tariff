"""EKZ Tariff integration."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import EkzTariffApi
from .const import DOMAIN, PLATFORMS, CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME
from .coordinator import EkzTariffCoordinator

RETRY_INTERVAL = timedelta(minutes=30)


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = value.strip().split(":")
        h = int(hh)
        m = int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return 18, 15


def _has_valid_prices(coordinator: EkzTariffCoordinator) -> bool:
    data = coordinator.data or {}
    active = data.get("active") if isinstance(data, dict) else None
    return isinstance(active, list) and bool(active)


def _next_local_midnight(now_local: datetime) -> datetime:
    tomorrow = (now_local + timedelta(days=1)).date()
    return dt_util.as_local(dt_util.as_utc(datetime.combine(tomorrow, time(0, 0), tzinfo=now_local.tzinfo)))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    config = dict(entry.data)
    config.update(dict(entry.options))

    session = async_get_clientsession(hass)
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    api = EkzTariffApi(session, oauth_session=oauth_session)

    coordinator = EkzTariffCoordinator(hass, api, config=config)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    publish_time = config.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME)
    hour, minute = _parse_hhmm(publish_time)

    retry_state_key = f"{entry.entry_id}_retry_until"
    hass.data[DOMAIN][retry_state_key] = None

    async def _force_refresh() -> None:
        coordinator._last_fetch_date = None
        await coordinator.async_request_refresh()

    async def _daily_refresh(now) -> None:
        await _force_refresh()
        if not _has_valid_prices(coordinator):
            hass.data[DOMAIN][retry_state_key] = _next_local_midnight(dt_util.now())
        else:
            hass.data[DOMAIN][retry_state_key] = None

    async def _retry_tick(now) -> None:
        until = hass.data[DOMAIN].get(retry_state_key)
        if not isinstance(until, datetime):
            return
        now_local = dt_util.now()
        if now_local >= until:
            hass.data[DOMAIN][retry_state_key] = None
            return
        if not _has_valid_prices(coordinator):
            await _force_refresh()
        else:
            hass.data[DOMAIN][retry_state_key] = None

    hass.data[DOMAIN][entry.entry_id + "_unsub_daily"] = async_track_time_change(
        hass, _daily_refresh, hour=hour, minute=minute, second=0
    )
    hass.data[DOMAIN][entry.entry_id + "_unsub_retry"] = async_track_time_interval(
        hass, _retry_tick, RETRY_INTERVAL
    )

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    unsub = hass.data.get(DOMAIN, {}).pop(entry.entry_id + "_unsub_daily", None)
    if unsub:
        unsub()
    unsub = hass.data.get(DOMAIN, {}).pop(entry.entry_id + "_unsub_retry", None)
    if unsub:
        unsub()

    hass.data.get(DOMAIN, {}).pop(f"{entry.entry_id}_retry_until", None)
    if unload_ok and DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
