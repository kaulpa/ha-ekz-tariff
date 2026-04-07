"""Constants for EKZ Tariff."""
from __future__ import annotations

DOMAIN = "ekz_tariff"

CONF_PUBLISH_TIME = "publish_time"
CONF_EMS_INSTANCE_ID = "ems_instance_id"
CONF_REDIRECT_URI = "redirect_uri"
CONF_MODE = "mode"  # "public" or "protected"
CONF_TARIFF_NAME = "tariff_name"  # For public API

# Validation settings
CONF_MIN_SLOTS_PER_DAY = "min_slots_per_day"
CONF_MIN_PRICE_CHF_PER_KWH = "min_price_chf_per_kwh"
CONF_MAX_PRICE_CHF_PER_KWH = "max_price_chf_per_kwh"
CONF_MAX_RETRIES_NO_DATA = "max_retries_no_data"
CONF_MAX_RETRIES_INVALID_DATA = "max_retries_invalid_data"
CONF_RETRY_INTERVAL_MINUTES = "retry_interval_minutes"
CONF_DEBUG_MODE = "debug_mode"

DEFAULT_PUBLISH_TIME = "18:15"
DEFAULT_NAME = "EKZ Tariff"
DEFAULT_MODE = "public"  # Default to public (no auth required)
DEFAULT_TARIFF_NAME = "electricity_standard"  # Standard retail tariff

DEFAULT_MIN_SLOTS_PER_DAY = 96
DEFAULT_MIN_PRICE_CHF_PER_KWH = 0.10
DEFAULT_MAX_PRICE_CHF_PER_KWH = 0.99
DEFAULT_MAX_RETRIES_NO_DATA = 4
DEFAULT_MAX_RETRIES_INVALID_DATA = 4
DEFAULT_RETRY_INTERVAL_MINUTES = 15

# Dispatcher signal sent when validated tomorrow data is ready
SIGNAL_EKZ_NEW_DATA = "ekz_tariff_new_data"

PLATFORMS: list[str] = ["sensor", "button", "binary_sensor"]
