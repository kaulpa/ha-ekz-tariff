"""Constants for EKZ Tariff."""
from __future__ import annotations

DOMAIN = "ekz_tariff"

CONF_BASELINE_TARIFF_NAME = "baseline_tariff_name"
CONF_PUBLISH_TIME = "publish_time"
CONF_EMS_INSTANCE_ID = "ems_instance_id"
CONF_REDIRECT_URI = "redirect_uri"

DEFAULT_BASELINE_TARIFF_NAME = "electricity_standard"
DEFAULT_PUBLISH_TIME = "18:15"
DEFAULT_NAME = "EKZ Tariff"

PLATFORMS: list[str] = ["sensor", "button"]
