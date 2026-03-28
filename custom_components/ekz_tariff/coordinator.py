"""Coordinator for EKZ Tariff."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time as _time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EkzTariffApi, EkzTariffAuthError
from .const import CONF_DEBUG_MODE, CONF_EMS_INSTANCE_ID, CONF_REDIRECT_URI

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "ekz_tariff"

# Components that make up the baseline price (netto)
_BASELINE_COMPONENTS: tuple[str, ...] = ("electricity", "grid", "regional_fees")


@dataclass(frozen=True)
class PriceSlot:
    """A single 15-minute price slot with all components."""

    start: datetime
    electricity_chf_per_kwh: float
    grid_chf_per_kwh: float
    regional_fees_chf_per_kwh: float
    metering_chf_per_kwh: float
    integrated_chf_per_kwh: float
    feed_in_chf_per_kwh: float
    components: dict[str, float] = field(default_factory=dict)

    @property
    def components_chf_per_kwh(self) -> dict[str, float]:
        """Backward-compatible alias for Tariff Saver."""
        return self.components


class EkzTariffCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch EKZ tariff data once daily and persist in storage."""

    def __init__(self, hass: HomeAssistant, api: EkzTariffApi, config: dict[str, Any]) -> None:
        self.hass = hass
        self.api = api
        self.config = config
        self.ems_instance_id: str | None = config.get(CONF_EMS_INSTANCE_ID)
        self.redirect_uri: str | None = config.get(CONF_REDIRECT_URI)
        self.last_api_success_utc: datetime | None = None
        self.link_status: str | None = None
        self.activity_log: list[dict] = []

        # Error flags (read by binary sensors)
        self.no_data_error: bool = False
        self.invalid_data_error: bool = False
        self.auth_error: bool = False
        self.baseline_error: bool = False

        # Baseline (netto, computed from API)
        self.baseline_chf_per_kwh: float | None = None
        self.baseline_quarter: str | None = None

        # Debug mode
        self.debug_mode: bool = bool(config.get(CONF_DEBUG_MODE, False))

        # Callback after successful fetch + merge (set by __init__.py)
        self.on_new_tomorrow_data = None  # async callable(tomorrow_date)

        self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{config.get('entry_id', 'default')}")
        self._stored_slots: dict[str, dict[str, float]] = {}
        self._publication_timestamp: datetime | None = None
        self._last_fetch_utc: datetime | None = None

        super().__init__(hass, _LOGGER, name="EKZ Tariff", update_interval=None)

    def log_activity(self, icon: str, message: str) -> None:
        self.activity_log.insert(0, {"time": dt_util.now().isoformat(), "icon": icon, "msg": message})
        self.activity_log = self.activity_log[:30]

    def reset_error_flags(self) -> None:
        """Reset all error flags (called at start of daily refresh)."""
        self.no_data_error = False
        self.invalid_data_error = False
        self.auth_error = False
        self.baseline_error = False

    # -- Storage --

    async def async_load_storage(self) -> None:
        """Load persisted slots from .storage on startup."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        self._stored_slots = stored.get("slots", {})
        pub_ts = stored.get("publication_timestamp")
        if isinstance(pub_ts, str):
            self._publication_timestamp = dt_util.parse_datetime(pub_ts)
        fetch_ts = stored.get("last_fetch_utc")
        if isinstance(fetch_ts, str):
            self._last_fetch_utc = dt_util.parse_datetime(fetch_ts)
            self.last_api_success_utc = self._last_fetch_utc
        self.link_status = stored.get("link_status")
        # Restore baseline from storage
        baseline = stored.get("baseline_chf_per_kwh")
        if isinstance(baseline, (int, float)):
            self.baseline_chf_per_kwh = float(baseline)
        self.baseline_quarter = stored.get("baseline_quarter")
        _LOGGER.info("EKZ: Loaded %d slots from storage (baseline: %s for %s)",
                      len(self._stored_slots), self.baseline_chf_per_kwh, self.baseline_quarter)
        self.log_activity("💾", f"{len(self._stored_slots)} Slots aus Storage geladen")
        # Build coordinator data from stored slots
        if self._stored_slots:
            self.async_set_updated_data(self._build_data())

    async def _async_save_storage(self) -> None:
        """Persist current slots to .storage."""
        await self._store.async_save({
            "slots": self._stored_slots,
            "publication_timestamp": self._publication_timestamp.isoformat() if self._publication_timestamp else None,
            "last_fetch_utc": self._last_fetch_utc.isoformat() if self._last_fetch_utc else None,
            "link_status": self.link_status,
            "baseline_chf_per_kwh": self.baseline_chf_per_kwh,
            "baseline_quarter": self.baseline_quarter,
        })

    # -- Fetch --

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch tomorrow's tariffs from customerTariffs (OAuth)."""
        if not self.ems_instance_id:
            raise UpdateFailed("EKZ Tariff: ems_instance_id not configured")

        # Check EMS link status
        try:
            status_payload = await self.api.fetch_ems_link_status(
                ems_instance_id=self.ems_instance_id,
                redirect_uri=self.redirect_uri or "",
            )
            self.link_status = self._extract_link_status(status_payload)
        except EkzTariffAuthError as err:
            self.auth_error = True
            raise ConfigEntryAuthFailed(f"EKZ auth failed: {err}") from err
        except Exception as err:
            _LOGGER.warning("EKZ link status check failed: %s", err)

        # Fetch tomorrow's customer tariffs
        now_local = dt_util.now()
        tomorrow = (now_local + timedelta(days=1)).date()
        tom_start = dt_util.as_utc(datetime.combine(tomorrow, _time(0, 0), tzinfo=now_local.tzinfo))
        tom_end = tom_start + timedelta(days=1)

        try:
            payload = await self.api.fetch_customer_tariffs(
                ems_instance_id=self.ems_instance_id,
                tariff_type="electricity_dynamic",
                start_timestamp=tom_start.isoformat(),
                end_timestamp=tom_end.isoformat(),
            )
        except EkzTariffAuthError as err:
            self.auth_error = True
            raise ConfigEntryAuthFailed(f"EKZ auth failed: {err}") from err
        except Exception as err:
            self.log_activity("❌", f"Fetch fehlgeschlagen: {err}")
            raise UpdateFailed(f"EKZ customerTariffs failed: {err}") from err

        # Debug: write raw API response to file
        if self.debug_mode:
            self._write_debug_log("customerTariffs_response", {
                "request_start": tom_start.isoformat(),
                "request_end": tom_end.isoformat(),
                "request_tomorrow": str(tomorrow),
                "payload": payload,
            })

        # Parse slots
        new_slots = self._parse_customer_slots(payload)

        # Debug: write parsed slots
        if self.debug_mode:
            slot_dates = {}
            for ts_key in sorted(new_slots.keys()):
                parsed_dt = dt_util.parse_datetime(ts_key)
                if parsed_dt:
                    d = str(dt_util.as_local(parsed_dt).date())
                    slot_dates[d] = slot_dates.get(d, 0) + 1
            self._write_debug_log("parsed_slots", {
                "total_new_slots": len(new_slots),
                "slots_per_date": slot_dates,
                "first_slot": sorted(new_slots.keys())[0] if new_slots else None,
                "last_slot": sorted(new_slots.keys())[-1] if new_slots else None,
            })

        if not new_slots:
            self.log_activity("⚠️", "Keine Tomorrow-Slots erhalten")
            _LOGGER.info("EKZ: No tomorrow slots received, keeping existing data")
            return self._build_data()

        # Extract publication timestamp
        pub_ts = payload.get("publication_timestamp") if isinstance(payload, dict) else None
        if isinstance(pub_ts, str):
            parsed = dt_util.parse_datetime(pub_ts)
            if parsed:
                self._publication_timestamp = dt_util.as_utc(parsed)

        # Merge into stored slots (deduplicate by start_timestamp)
        for ts_key, components in new_slots.items():
            self._stored_slots[ts_key] = components

        # Clean up old slots (>2 days)
        self._cleanup_old_slots()

        # Save
        self._last_fetch_utc = dt_util.utcnow()
        self.last_api_success_utc = self._last_fetch_utc
        await self._async_save_storage()

        self.log_activity("📡", f"{len(new_slots)} Tomorrow-Slots abgerufen (total: {len(self._stored_slots)})")
        _LOGGER.info("EKZ: Fetched %d tomorrow slots, total stored: %d", len(new_slots), len(self._stored_slots))

        # Validate and signal — runs synchronously after data is merged
        if self.on_new_tomorrow_data is not None:
            await self.on_new_tomorrow_data(tomorrow)

        return self._build_data()

    # -- Baseline --

    async def async_compute_baseline(self) -> bool:
        """Compute baseline price from Public API (electricity) + customerTariffs (grid, regional_fees).

        Baseline = electricity_standard (public) + grid + regional_fees (from stored customer slots).
        All values netto (without VAT).

        Returns True if baseline was successfully computed, False otherwise.
        """
        now_local = dt_util.now()
        current_quarter = f"{now_local.year}-Q{(now_local.month - 1) // 3 + 1}"

        # Skip if already computed for this quarter
        if self.baseline_quarter == current_quarter and self.baseline_chf_per_kwh is not None:
            return True

        # 1. Fetch electricity_standard from public API (no auth needed)
        try:
            public_payload = await self.api.fetch_public_tariff("electricity_standard")
        except Exception as err:
            _LOGGER.error("EKZ: Failed to fetch public electricity_standard: %s", err)
            self.log_activity("❌", f"Baseline: Public API fehlgeschlagen: {err}")
            return False

        public_prices = public_payload.get("prices", [])
        if not public_prices:
            _LOGGER.error("EKZ: Public electricity_standard returned no prices")
            self.log_activity("❌", "Baseline: Keine Preise von Public API")
            return False

        # Extract electricity CHF/kWh from first slot
        electricity_netto: float | None = None
        first_price = public_prices[0]
        electricity_netto = self._extract_chf_per_kwh(first_price.get("electricity"))
        if electricity_netto is None or electricity_netto <= 0:
            _LOGGER.error("EKZ: No valid electricity price in public response")
            self.log_activity("❌", "Baseline: Ungültiger Strompreis von Public API")
            return False

        # 2. Get grid + regional_fees from stored customerTariffs slots
        grid_netto: float | None = None
        regional_fees_netto: float | None = None

        for _ts_key, components in self._stored_slots.items():
            g = components.get("grid")
            r = components.get("regional_fees")
            if isinstance(g, (int, float)) and g > 0:
                grid_netto = float(g)
            if isinstance(r, (int, float)):
                regional_fees_netto = float(r)
            if grid_netto is not None and regional_fees_netto is not None:
                break

        if grid_netto is None:
            _LOGGER.error("EKZ: No grid price found in stored customerTariffs slots")
            self.log_activity("❌", "Baseline: Kein Netzpreis in customerTariffs")
            return False

        if regional_fees_netto is None:
            regional_fees_netto = 0.0

        # 3. Compute baseline (netto)
        baseline = electricity_netto + grid_netto + regional_fees_netto
        self.baseline_chf_per_kwh = round(baseline, 6)
        self.baseline_quarter = current_quarter
        await self._async_save_storage()

        _LOGGER.info(
            "EKZ: Baseline computed for %s: electricity=%.4f + grid=%.4f + regional_fees=%.4f = %.4f CHF/kWh (netto)",
            current_quarter, electricity_netto, grid_netto, regional_fees_netto, self.baseline_chf_per_kwh,
        )
        self.log_activity("📊", f"Baseline {current_quarter}: {self.baseline_chf_per_kwh:.4f} CHF/kWh (netto)")
        return True

    # -- Parse --

    def _parse_customer_slots(self, payload: Any) -> dict[str, dict[str, float]]:
        """Parse customerTariffs response into {start_ts: {component: value}} dict."""
        if isinstance(payload, dict):
            raw_prices = payload.get("prices", [])
        elif isinstance(payload, list):
            raw_prices = payload
        else:
            return {}

        result: dict[str, dict[str, float]] = {}
        for item in raw_prices:
            if not isinstance(item, dict):
                continue
            start_ts = item.get("start_timestamp")
            if not isinstance(start_ts, str):
                continue
            dt_start = dt_util.parse_datetime(start_ts)
            if dt_start is None:
                continue

            components: dict[str, float] = {}
            for key in ("electricity", "grid", "regional_fees", "metering", "integrated", "feed_in", "refund_storage"):
                value = self._extract_chf_per_kwh(item.get(key))
                if value is not None:
                    components[key] = value

            if components:
                utc_key = dt_util.as_utc(dt_start).isoformat()
                result[utc_key] = components

        return result

    @staticmethod
    def _extract_chf_per_kwh(val: Any) -> float | None:
        """Extract CHF/kWh value from a component field."""
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, list):
            for entry in val:
                if isinstance(entry, dict) and entry.get("unit") in ("CHF_kWh", "CHF/kWh"):
                    v = entry.get("value")
                    if isinstance(v, (int, float)):
                        return float(v)
        if isinstance(val, dict):
            if val.get("unit") in ("CHF_kWh", "CHF/kWh"):
                v = val.get("value")
                if isinstance(v, (int, float)):
                    return float(v)
        return None

    # -- Build data --

    def _build_data(self) -> dict[str, Any]:
        """Build coordinator data from stored slots. All prices are netto."""
        active: list[PriceSlot] = []
        for ts_key, components in sorted(self._stored_slots.items()):
            dt_start = dt_util.parse_datetime(ts_key)
            if dt_start is None:
                continue
            active.append(PriceSlot(
                start=dt_util.as_utc(dt_start),
                electricity_chf_per_kwh=components.get("electricity", 0.0),
                grid_chf_per_kwh=components.get("grid", 0.0),
                regional_fees_chf_per_kwh=components.get("regional_fees", 0.0),
                metering_chf_per_kwh=components.get("metering", 0.0),
                integrated_chf_per_kwh=components.get("integrated", 0.0),
                feed_in_chf_per_kwh=components.get("feed_in", 0.0),
                components=components,
            ))

        return {
            "active": active,
            "baseline_chf_per_kwh": self.baseline_chf_per_kwh,
            "publication_timestamp": self._publication_timestamp,
            "last_api_success": self.last_api_success_utc,
            "link_status": self.link_status,
        }

    # -- Cleanup --

    def _cleanup_old_slots(self) -> None:
        """Remove slots older than 2 days."""
        cutoff = dt_util.utcnow() - timedelta(days=2)
        to_remove = [
            ts for ts in self._stored_slots
            if (dt := dt_util.parse_datetime(ts)) is not None and dt_util.as_utc(dt) < cutoff
        ]
        for ts in to_remove:
            del self._stored_slots[ts]
        if to_remove:
            _LOGGER.debug("EKZ: Cleaned up %d old slots", len(to_remove))

    # -- Helpers --

    def _write_debug_log(self, label: str, data: Any) -> None:
        """Write debug data to /config/ekz_tariff_debug.log (append)."""
        import json
        try:
            timestamp = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
            path = self.hass.config.path("ekz_tariff_debug.log")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{timestamp}] {label}\n")
                f.write(f"{'='*60}\n")
                f.write(json.dumps(data, indent=2, default=str, ensure_ascii=False))
                f.write("\n")
            _LOGGER.info("EKZ debug: wrote %s to %s", label, path)
        except Exception as err:
            _LOGGER.error("EKZ debug: failed to write %s: %s", label, err)

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
