"""EKZ tariff API client (Public + myEKZ)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Final

import asyncio
import aiohttp
from aiohttp import ClientError

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

_LOGGER = logging.getLogger(__name__)


class EkzTariffApiError(RuntimeError):
    """Raised when EKZ API calls fail."""


class EkzTariffAuthError(EkzTariffApiError):
    """Raised for OAuth/token problems."""


class EkzTariffApi:
    BASE_URL: Final[str] = "https://api.tariffs.ekz.ch/v1"
    CHF_PER_KWH_UNITS: Final[set[str]] = {"CHF_kWh", "CHF/kWh"}
    CHF_PER_MONTH_UNITS: Final[set[str]] = {"CHF_m", "CHF/month", "CHF_month"}
    IGNORED_COMPONENT_KEYS: Final[set[str]] = {"feed_in", "refund_storage"}

    def __init__(self, session: aiohttp.ClientSession, oauth_session: OAuth2Session | None = None) -> None:
        self._session = session
        self._oauth_session = oauth_session

    async def fetch_public_tariff(
        self,
        tariff_name: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"tariff_name": tariff_name}
        if start is not None and end is not None:
            params["start_timestamp"] = start.isoformat()
            params["end_timestamp"] = end.isoformat()

        url = f"{self.BASE_URL}/tariffs"
        _LOGGER.debug("EKZ GET %s params=%s", url, params)
        async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            text = await resp.text()
            _LOGGER.debug("public tariffs status=%s body=%s", resp.status, text[:2000])
            resp.raise_for_status()
            payload: Any = await resp.json()

        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected EKZ public payload: {payload!r}")
        prices = payload.get("prices")
        if not isinstance(prices, list):
            raise ValueError(f"Unexpected EKZ public payload shape, missing 'prices': {payload!r}")
        return payload

    # Keycloak error strings that indicate the refresh token is genuinely invalid
    _AUTH_ERROR_KEYWORDS: frozenset = frozenset({
        "invalid_grant",
        "session_not_active",
        "token_expired",
        "token is not active",
    })

    async def _async_get_access_token(self) -> str:
        if not self._oauth_session:
            raise EkzTariffAuthError("No OAuth session available (myEKZ not configured)")
        try:
            await self._oauth_session.async_ensure_token_valid()
        except ConfigEntryAuthFailed:
            raise
        except aiohttp.ClientResponseError as err:
            # 400/401 from token endpoint may be a real auth error
            if err.status in (400, 401):
                _LOGGER.warning("EKZ token refresh HTTP %s – reauthentication required", err.status)
                raise ConfigEntryAuthFailed(f"EKZ token refresh HTTP {err.status}") from err
            # 5xx or other HTTP errors are transient – coordinator will retry
            _LOGGER.warning("EKZ token refresh transient HTTP %s (will retry)", err.status)
            raise EkzTariffApiError(f"EKZ token refresh transient failure: {err}") from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            # Network / timeout errors are never auth failures
            _LOGGER.warning("EKZ token refresh network error (will retry): %s", err)
            raise EkzTariffApiError(f"EKZ token refresh network error: {err}") from err
        except Exception as err:
            msg = str(err).lower()
            if any(kw in msg for kw in self._AUTH_ERROR_KEYWORDS):
                _LOGGER.warning("EKZ token genuinely invalid – reauthentication required: %s", err)
                raise ConfigEntryAuthFailed(f"EKZ token invalid: {err}") from err
            # Unknown error – treat as transient to avoid false re-auth notifications
            _LOGGER.warning("EKZ token refresh unexpected error (treating as transient): %s", err)
            raise EkzTariffApiError(f"EKZ token refresh failed: {err}") from err

        token = self._oauth_session.token or {}
        access_token = token.get("access_token")
        if not access_token:
            raise ConfigEntryAuthFailed("EKZ OAuth token missing access_token")
        _LOGGER.debug("EKZ access token OK, expires_in=%s", token.get("expires_in"))
        return access_token

    async def fetch_ems_link_status(self, *, ems_instance_id: str, redirect_uri: str) -> dict[str, Any]:
        access_token = await self._async_get_access_token()
        url = f"{self.BASE_URL}/emsLinkStatus"
        params = {"ems_instance_id": ems_instance_id, "redirect_uri": redirect_uri}
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

        try:
            async with self._session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
                if resp.status == 401:
                    raise EkzTariffAuthError(f"401 Unauthorized: {text}")
                if resp.status >= 400:
                    raise EkzTariffApiError(f"EKZ API error {resp.status}: {text}")
                data: Any = await resp.json()
                _LOGGER.debug("emsLinkStatus parsed payload=%r", data)
        except ClientError as err:
            raise EkzTariffApiError(f"HTTP error calling emsLinkStatus: {err}") from err

        if not isinstance(data, dict):
            raise EkzTariffApiError(f"Unexpected emsLinkStatus payload: {data!r}")
        return data

    async def fetch_customer_tariffs(
        self,
        *,
        ems_instance_id: str,
        tariff_type: str | None = None,
        start_timestamp: str | None = None,
        end_timestamp: str | None = None,
    ) -> dict[str, Any]:
        access_token = await self._async_get_access_token()
        url = f"{self.BASE_URL}/customerTariffs"
        params: dict[str, str] = {"ems_instance_id": ems_instance_id}
        if tariff_type:
            params["tariffType"] = tariff_type
        if start_timestamp:
            params["start_timestamp"] = start_timestamp
        if end_timestamp:
            params["end_timestamp"] = end_timestamp

        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

        try:
            async with self._session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
                _LOGGER.debug("customerTariffs status=%s body=%s", resp.status, text[:1200])
                if resp.status == 401:
                    raise EkzTariffAuthError(f"401 Unauthorized: {text}")
                if resp.status >= 400:
                    raise EkzTariffApiError(f"EKZ API error {resp.status}: {text}")
                data: Any = await resp.json()
        except ClientError as err:
            raise EkzTariffApiError(f"HTTP error calling customerTariffs: {err}") from err

        _LOGGER.debug("customerTariffs parsed payload=%r", data)

        if isinstance(data, list):
            return {"prices": [x for x in data if isinstance(x, dict)], "publication_timestamp": None}

        if isinstance(data, dict):
            prices = data.get("prices")
            if isinstance(prices, list):
                return {
                    "prices": [x for x in prices if isinstance(x, dict)],
                    "publication_timestamp": data.get("publication_timestamp"),
                }
            tariffs = data.get("tariffs")
            if isinstance(tariffs, list):
                return {
                    "prices": [x for x in tariffs if isinstance(x, dict)],
                    "publication_timestamp": data.get("publication_timestamp"),
                }

        raise EkzTariffApiError(f"Unexpected customerTariffs payload: {data!r}")

    @classmethod
    def _sum_list_unit(cls, val: Any, allowed_units: set[str]) -> float | None:
        if not isinstance(val, list):
            return None
        total = 0.0
        found = False
        for entry in val:
            if not isinstance(entry, dict):
                continue
            unit = entry.get("unit")
            if not isinstance(unit, str) or unit not in allowed_units:
                continue
            v = entry.get("value")
            if isinstance(v, (int, float)):
                total += float(v)
                found = True
        return total if found else None

    @classmethod
    def parse_components_chf_per_kwh(cls, price_item: dict[str, Any]) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, val in price_item.items():
            if key in ("start_timestamp", "end_timestamp", "publication_timestamp"):
                continue
            if key in cls.IGNORED_COMPONENT_KEYS:
                continue

            s = cls._sum_list_unit(val, cls.CHF_PER_KWH_UNITS)
            if isinstance(s, (int, float)) and s != 0.0:
                out[str(key)] = float(s)
                continue

            if isinstance(val, dict):
                unit = val.get("unit")
                if isinstance(unit, str) and unit in cls.CHF_PER_KWH_UNITS:
                    v = val.get("value")
                    if isinstance(v, (int, float)) and float(v) != 0.0:
                        out[str(key)] = float(v)
                        continue

            if isinstance(val, (int, float)) and float(val) != 0.0:
                out[str(key)] = float(val)

        return out

    @classmethod
    def parse_components_chf_per_month(cls, price_item: dict[str, Any]) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, val in price_item.items():
            if key in ("start_timestamp", "end_timestamp", "publication_timestamp"):
                continue
            if key in cls.IGNORED_COMPONENT_KEYS:
                continue

            s = cls._sum_list_unit(val, cls.CHF_PER_MONTH_UNITS)
            if isinstance(s, (int, float)) and s != 0.0:
                out[str(key)] = float(s)
                continue

            if isinstance(val, dict):
                unit = val.get("unit")
                if isinstance(unit, str) and unit in cls.CHF_PER_MONTH_UNITS:
                    v = val.get("value")
                    if isinstance(v, (int, float)) and float(v) != 0.0:
                        out[str(key)] = float(v)
                        continue

            if isinstance(val, (int, float)) and float(val) != 0.0:
                out[str(key)] = float(val)

        return out
