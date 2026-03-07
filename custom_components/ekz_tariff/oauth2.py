"""OAuth2 helpers for EKZ Tariff."""
from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN

AUTHORIZATION_URL = "https://login.ekz.ch/auth/realms/myEKZ/protocol/openid-connect/auth"
TOKEN_URL = "https://login.ekz.ch/auth/realms/myEKZ/protocol/openid-connect/token"


def _external_callback(hass) -> str:
    external = hass.config.external_url
    if not external:
        raise HomeAssistantError(
            "External URL is not set. Please set it under Settings -> System -> Network -> External URL."
        )
    return external.rstrip("/") + "/auth/external/callback"


class OAuth2FlowHandler(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle the OAuth2 flow for myEKZ."""

    DOMAIN = DOMAIN

    async def async_get_redirect_uri(self) -> str:
        return _external_callback(self.hass)

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        return {"scope": "openid offline_access"}


async def async_get_auth_implementation(hass):
    redirect_uri = _external_callback(hass)
    return config_entry_oauth2_flow.LocalOAuth2Implementation(
        hass,
        DOMAIN,
        client_id=None,
        client_secret=None,
        authorize_url=AUTHORIZATION_URL,
        token_url=TOKEN_URL,
        redirect_uri=redirect_uri,
    )
