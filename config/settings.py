"""Runtime settings loaded from environment variables.

Keep these centralized so the agent / tools / bot never read os.environ
directly. Tweak via .env without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str

    # MiniMax AI
    minimax_api_key: str
    minimax_base_url: str
    minimax_model_name: str

    # Bot access security
    bot_access_token: str

    # Playwright
    playwright_headless: bool
    playwright_timeout_ms: int

    # Google Maps
    google_maps_locale: str
    google_maps_hl: str

    # Fuel (kept for legacy, not displayed to users)
    fuel_price_pertalite: int
    fuel_price_pertamax: int
    fuel_price_solar: int
    default_fuel_consumption: int

    log_level: str


def load_settings() -> Settings:
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),

        minimax_api_key=os.getenv("MINIMAX_API_KEY", ""),
        minimax_base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.chat/v1"),
        minimax_model_name=os.getenv("MINIMAX_MODEL_NAME", "MiniMax-Text-01"),

        bot_access_token=os.getenv("BOT_ACCESS_TOKEN", ""),

        playwright_headless=_get_bool("PLAYWRIGHT_HEADLESS", True),
        playwright_timeout_ms=_get_int("PLAYWRIGHT_TIMEOUT_MS", 20000),

        google_maps_locale=os.getenv("GOOGLE_MAPS_LOCALE", "id"),
        google_maps_hl=os.getenv("GOOGLE_MAPS_HL", "id"),

        fuel_price_pertalite=_get_int("FUEL_PRICE_PERTALITE", 10000),
        fuel_price_pertamax=_get_int("FUEL_PRICE_PERTAMAX", 12500),
        fuel_price_solar=_get_int("FUEL_PRICE_SOLAR", 6800),
        default_fuel_consumption=_get_int("DEFAULT_FUEL_CONSUMPTION_KM_PER_LITER", 12),

        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


settings = load_settings()
