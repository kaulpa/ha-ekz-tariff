"""Diagnostics support for EKZ Tariff."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.components.diagnostics import async_redact_data

from .const import DOMAIN

TO_REDACT = {"token", "access_token", "refresh_token", "client_secret", "authorization"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = coordinator.data if coordinator else None
    payload: dict[str, Any] = {
        "entry": dict(entry.data),
        "options": dict(entry.options),
        "coordinator": {
            "link_status": getattr(coordinator, "link_status", None),
            "linking_url": getattr(coordinator, "linking_url", None),
            "last_api_success_utc": getattr(coordinator, "last_api_success_utc", None).isoformat() if getattr(coordinator, "last_api_success_utc", None) else None,
            "active_slots": len((data or {}).get("active", []) if isinstance(data, dict) else []),
            "baseline_slots": len((data or {}).get("baseline", []) if isinstance(data, dict) else []),
            "active_publication_timestamp": (data or {}).get("active_publication_timestamp").isoformat() if isinstance((data or {}).get("active_publication_timestamp"), object) and getattr((data or {}).get("active_publication_timestamp"), 'isoformat', None) else None,
            "baseline_publication_timestamp": (data or {}).get("baseline_publication_timestamp").isoformat() if isinstance((data or {}).get("baseline_publication_timestamp"), object) and getattr((data or {}).get("baseline_publication_timestamp"), 'isoformat', None) else None,
        },
        "title": entry.title,
        "name": entry.data.get(CONF_NAME),
    }
    return async_redact_data(payload, TO_REDACT)
