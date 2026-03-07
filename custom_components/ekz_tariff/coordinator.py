"""Coordinator for EKZ Tariff."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EkzTariffApi, EkzTariffAuthError
from .const import (
    CONF_BASELINE_TARIFF_NAME,
    CONF_EMS_INSTANCE_ID,
    CONF_PUBLISH_TIME,
    CONF_REDIRECT_URI,
    DEFAULT_BASELINE_TARIFF_NAME,
    DEFAULT_PUBLISH_TIME,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceSlot:
    """A single 15-minute price slot."""

    start: datetime
    electricity_chf_per_kwh: float
    components_chf_per_kwh: dict[str, float]


class EkzTariffCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch EKZ raw tariff data."""

    def __init__(self, hass: HomeAssistant, api: EkzTariffApi, config: dict[str, Any]) -> None:
        self.hass = hass
        self.api = api
        self.config = config
        self.publish_time: str = config.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME)
        self.baseline_tariff_name: str = config.get(CONF_BASELINE_TARIFF_NAME, DEFAULT_BASELINE_TARIFF_NAME)
        self.ems_instance_id: str | None = config.get(CONF_EMS_INSTANCE_ID)
        self.redirect_uri: str | None = config.get(CONF_REDIRECT_URI)
        self._last_fetch_date: date | None = None
        self.link_status: str | None = None
        self.linking_url: str | None = None
        self.last_api_success_utc: datetime | None = None
        super().__init__(hass, _LOGGER, name="EKZ Tariff", update_interval=None)

    async def _async_update_data(self) -> dict[str, Any]:
        today = dt_util.now().date()
        if self._last_fetch_date == today and self.data:
            return self.data

        if not self.ems_instance_id or not self.redirect_uri:
            raise UpdateFailed("EKZ Tariff requires ems_instance_id and redirect_uri.")

        try:
            status = await self.api.fetch_ems_link_status(
                ems_instance_id=self.ems_instance_id,
                redirect_uri=self.redirect_uri,
            )
            self.link_status = self._extract_link_status(status)
            self.linking_url = self._extract_linking_url(status)
        except ConfigEntryAuthFailed:
            raise
        except EkzTariffAuthError as err:
            raise ConfigEntryAuthFailed(f"myEKZ auth failed during emsLinkStatus: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"myEKZ emsLinkStatus failed: {err}") from err

        active_payload: dict[str, Any] = {"prices": [], "publication_timestamp": None}
        active: list[PriceSlot] = []
        if self._is_linked_status(self.link_status):
            try:
                fetched_active_payload = await self.api.fetch_customer_tariffs(ems_instance_id=self.ems_instance_id)
                if isinstance(fetched_active_payload, dict):
                    active_payload = fetched_active_payload
            except ConfigEntryAuthFailed:
                raise
            except EkzTariffAuthError as err:
                raise ConfigEntryAuthFailed(f"myEKZ auth failed during customerTariffs: {err}") from err
            except Exception as err:
                raise UpdateFailed(f"myEKZ customerTariffs failed: {err}") from err

            active = self._parse_prices(active_payload)
            if not active:
                raise UpdateFailed("myEKZ customerTariffs returned no price slots")
        else:
            _LOGGER.info("Skipping customerTariffs because EMS link is not active: %s", self.link_status)

        baseline_payload: dict[str, Any] | None = None
        baseline: list[PriceSlot] = []
        try:
            baseline_payload = await self.api.fetch_public_tariff(self.baseline_tariff_name)
            baseline = self._parse_prices(baseline_payload)
        except Exception as err:
            _LOGGER.warning("Failed to fetch baseline tariff '%s': %s", self.baseline_tariff_name, err)
            baseline_payload = None
            baseline = []

        self.last_api_success_utc = dt_util.utcnow()
        self._last_fetch_date = today
        return {
            "active": active,
            "baseline": baseline,
            "active_publication_timestamp": self._parse_publication_timestamp(active_payload.get("publication_timestamp")),
            "baseline_publication_timestamp": self._parse_publication_timestamp((baseline_payload or {}).get("publication_timestamp")),
            "baseline_tariff_name": self.baseline_tariff_name,
            "link_status": self.link_status,
            "linking_url": self.linking_url,
        }

    def _parse_prices(self, payload: dict[str, Any] | list[dict[str, Any]]) -> list[PriceSlot]:
        raw_prices: Any = payload
        if isinstance(payload, dict):
            raw_prices = payload.get("prices", [])

        slots: list[PriceSlot] = []
        for item in raw_prices:
            if not isinstance(item, dict):
                continue
            start_ts = item.get("start_timestamp")
            if not isinstance(start_ts, str):
                continue
            dt_start = dt_util.parse_datetime(start_ts)
            if dt_start is None:
                continue
            comps = EkzTariffApi.parse_components_chf_per_kwh(item)
            elec = float(comps.get("electricity", 0.0) or 0.0)
            if elec <= 0 and isinstance(comps.get("integrated"), (int, float)):
                elec = float(comps.get("integrated") or 0.0)
            if elec > 0 and "integrated" not in comps:
                comps["integrated"] = elec
            slots.append(
                PriceSlot(
                    start=dt_util.as_utc(dt_start),
                    electricity_chf_per_kwh=elec,
                    components_chf_per_kwh=comps,
                )
            )
        out = {s.start: s for s in sorted(slots, key=lambda s: s.start)}
        return list(out.values())


    @staticmethod
    def _extract_link_status(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("link_status", "linkStatus", "status"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        link = payload.get("link")
        if isinstance(link, dict):
            for key in ("status", "link_status", "linkStatus"):
                value = link.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def _extract_linking_url(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("linking_process_redirect_uri", "linkingUrl", "link_url", "url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        link = payload.get("link")
        if isinstance(link, dict):
            for key in ("url", "linking_process_redirect_uri", "linkingUrl", "link_url"):
                value = link.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def _is_linked_status(value: str | None) -> bool:
        if not value:
            return False
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        return normalized in {
            "linked",
            "link_established",
            "linkestablished",
            "active",
            "ok",
            "connected",
            "success",
        }

    @staticmethod
    def _parse_publication_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            return None
        return dt_util.as_utc(parsed)
