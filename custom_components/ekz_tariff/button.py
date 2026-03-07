"""Button entities for EKZ Tariff."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import async_create as async_create_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_EMS_INSTANCE_ID, CONF_REDIRECT_URI
from .coordinator import EkzTariffCoordinator

_LOGGER = logging.getLogger(__name__)


def _device_info(entry: ConfigEntry) -> dict[str, Any]:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.title,
        "manufacturer": "EKZ",
        "model": "Tariff API",
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EkzTariffCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EkzTariffLinkButton(coordinator, entry)], True)


class EkzTariffLinkButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Anlage verknüpfen"
    _attr_icon = "mdi:link-variant"

    def __init__(self, coordinator: EkzTariffCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self.entry = entry
        self._last_url: str | None = None
        self._last_status: str | None = None
        self._attr_unique_id = f"{entry.entry_id}_ekz_link"
        self._attr_device_info = _device_info(entry)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"link_status": self._last_status, "linking_url": self._last_url}

    async def async_press(self) -> None:
        config = self.coordinator.config or {}
        ems_instance_id = config.get(CONF_EMS_INSTANCE_ID) or self.entry.data.get(CONF_EMS_INSTANCE_ID)
        redirect_uri = config.get(CONF_REDIRECT_URI) or self.entry.data.get(CONF_REDIRECT_URI)

        if not ems_instance_id or not redirect_uri:
            msg = "ems_instance_id/redirect_uri fehlen. Bitte Integration entfernen und neu hinzufügen."
            _LOGGER.warning(msg)
            await self._notify("EKZ Tariff – Linking", msg)
            return

        try:
            data = await self.coordinator.api.fetch_ems_link_status(
                ems_instance_id=str(ems_instance_id),
                redirect_uri=str(redirect_uri),
            )
        except Exception as err:
            _LOGGER.exception("emsLinkStatus failed: %s", err)
            await self._notify("EKZ Tariff – Linking", f"emsLinkStatus fehlgeschlagen: {err}")
            return

        self._last_status = str(data.get("link_status") or "")
        self._last_url = None

        if self._last_status == "link_required":
            url = data.get("linking_process_redirect_uri")
            if isinstance(url, str) and url.startswith("http"):
                self._last_url = url
                await self._notify(
                    "EKZ Tariff – Anlage auswählen",
                    "Bitte diesen Link öffnen, um die EKZ Anlage zu verknüpfen:\n\n" + url,
                )
            else:
                await self._notify("EKZ Tariff – Linking", f"link_required, aber keine gültige URL: {data!r}")
        elif self._last_status == "linked":
            await self._notify("EKZ Tariff – Linking", "Bereits verknüpft (link_status=linked).")
        else:
            await self._notify(
                "EKZ Tariff – Linking",
                f"Unbekannter link_status: {self._last_status!r}\n\n{data!r}",
            )

        self.async_write_ha_state()

    async def _notify(self, title: str, message: str) -> None:
        async_create_notification(
            self.coordinator.hass,
            message=message,
            title=title,
            notification_id=f"{DOMAIN}_{self.entry.entry_id}_ekz_link",
        )
