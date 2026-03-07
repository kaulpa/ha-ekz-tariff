"""Diagnostics support for EKZ Tariff."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {"token", "access_token", "refresh_token", "client_secret", "authorization"}


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _short(value: Any, keep: int = 6) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value) <= keep * 2:
        return value
    return f"{value[:keep]}…{value[-keep:]}"


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = coordinator.data if coordinator else None
    active = data.get("active", []) if isinstance(data, dict) else []
    baseline = data.get("baseline", []) if isinstance(data, dict) else []

    payload: dict[str, Any] = {
        "title": entry.title,
        "name": entry.data.get(CONF_NAME),
        "entry": dict(entry.data),
        "options": dict(entry.options),
        "provider_summary": {
            "provider": DOMAIN,
            "has_oauth_token": bool(entry.data.get("token", {}).get("access_token")) if isinstance(entry.data.get("token"), dict) else False,
            "baseline_tariff_name": (entry.options or {}).get("baseline_tariff_name") or entry.data.get("baseline_tariff_name"),
            "publish_time": (entry.options or {}).get("publish_time") or entry.data.get("publish_time"),
            "ems_instance_id_short": _short(entry.data.get("ems_instance_id")),
        },
        "coordinator": {
            "link_status": getattr(coordinator, "link_status", None),
            "has_linking_url": bool(getattr(coordinator, "linking_url", None)),
            "last_api_success_utc": _iso(getattr(coordinator, "last_api_success_utc", None)),
            "active_slots": len(active) if isinstance(active, list) else 0,
            "baseline_slots": len(baseline) if isinstance(baseline, list) else 0,
            "active_publication_timestamp": _iso((data or {}).get("active_publication_timestamp")) if isinstance(data, dict) else None,
            "baseline_publication_timestamp": _iso((data or {}).get("baseline_publication_timestamp")) if isinstance(data, dict) else None,
            "baseline_tariff_name": (data or {}).get("baseline_tariff_name") if isinstance(data, dict) else None,
            "current_actual_components": sorted((getattr(coordinator, "current_active", None) or {}).get("components_chf_per_kwh", {}).keys()) if coordinator else [],
            "current_baseline_components": sorted((getattr(coordinator, "current_baseline", None) or {}).get("components_chf_per_kwh", {}).keys()) if coordinator else [],
        },
    }
    return async_redact_data(payload, TO_REDACT)
