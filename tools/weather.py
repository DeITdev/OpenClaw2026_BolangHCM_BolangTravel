"""get_weather — BMKG weather forecast via Playwright.

Flow:
1. Search BMKG wilayah API to find the adm4 code for the requested city.
2. Fetch the 3-day forecast from BMKG prakiraan-cuaca API using that code.
3. Filter forecasts to the travel date (or today if not given).
4. Return structured WeatherOutput.

We navigate to BMKG's public JSON API endpoints using Playwright so the shared
browser session handles any cookie or rate-limit quirks automatically.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from browser.playwright_manager import get_manager
from tools.schemas import WeatherForecast, WeatherOutput

logger = logging.getLogger(__name__)

_BMKG_WILAYAH = "https://api.bmkg.go.id/publik/wilayah"
_BMKG_CUACA   = "https://api.bmkg.go.id/publik/prakiraan-cuaca"

# BMKG numeric weather codes → Indonesian label
_WEATHER_LABELS: dict[int, str] = {
    0:  "Cerah",
    1:  "Cerah Berawan",
    2:  "Berawan",
    3:  "Berawan Tebal",
    4:  "Udara Kabur",
    5:  "Asap",
    10: "Kabut",
    45: "Kabut Tebal",
    60: "Hujan Ringan",
    61: "Hujan Sedang",
    63: "Hujan Lebat",
    80: "Hujan Lokal",
    95: "Hujan Petir",
    97: "Hujan Petir Lebat",
}


async def _fetch_json(page: Page, url: str) -> Optional[dict | list]:
    """Navigate to a BMKG JSON endpoint and parse the response body."""
    try:
        await page.goto(url, wait_until="load", timeout=15000)
        await page.wait_for_timeout(1200)
        body = await page.evaluate("document.body.innerText")
        return json.loads(body)
    except PlaywrightTimeoutError:
        logger.warning("Timeout fetching BMKG: %s", url)
        return None
    except json.JSONDecodeError as exc:
        logger.debug("BMKG response is not JSON (%s): %s", url, exc)
        return None
    except Exception as exc:
        logger.debug("BMKG fetch failed (%s): %s", url, exc)
        return None


def _parse_dt(raw: str) -> Optional[datetime]:
    """Parse BMKG datetime strings: '2026-05-15 06:00:00' or '202605150600'."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M", "%Y%m%d%H"):
        try:
            return datetime.strptime(raw[: len(fmt.replace("%", "XX").replace("X", ""))], fmt)
        except ValueError:
            continue
    return None


def _build_forecasts(
    cuaca_raw: list,
    target_date: str,
    max_days: int = 3,
) -> List[WeatherForecast]:
    """
    Parse BMKG's nested cuaca list into WeatherForecast objects.

    BMKG returns cuaca as a list of lists — each outer element can be a list
    of time-step dicts for a particular period (e.g. per 3 hours or per day).
    We flatten and filter to the window: today → today + max_days.
    """
    try:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        target = datetime.strptime(target_date, "%Y-%m-%d")
        window_end = today + timedelta(days=max_days)
    except Exception:
        today = datetime.now()
        target = today
        window_end = today + timedelta(days=3)

    results: List[WeatherForecast] = []

    for period in cuaca_raw:
        items = period if isinstance(period, list) else [period]
        for item in items:
            if not isinstance(item, dict):
                continue

            raw_dt = str(item.get("local_datetime") or item.get("datetime") or "")
            dt = _parse_dt(raw_dt)
            if dt is None:
                continue

            dt_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            if dt_day < today or dt_day > window_end:
                continue

            code = item.get("weather", -1)
            condition = item.get("weather_desc") or _WEATHER_LABELS.get(code, "Tidak diketahui")

            results.append(
                WeatherForecast(
                    datetime_local=dt.strftime("%Y-%m-%d %H:%M"),
                    condition=condition,
                    temp_celsius=item.get("t"),
                    humidity_percent=item.get("hu"),
                    wind_speed_kmh=item.get("ws"),
                    precipitation_mm=item.get("tp"),
                )
            )

    # Sort chronologically; keep at most 12 entries (≈3 days × 4 slots)
    results.sort(key=lambda f: f.datetime_local)
    return results[:12]


async def get_weather(city: str, travel_date: Optional[str] = None) -> WeatherOutput:
    """
    Fetch BMKG weather forecast for *city* on *travel_date*.

    Args:
        city: City or district name, e.g. "Surabaya" or "Keputih Surabaya".
        travel_date: ISO date "YYYY-MM-DD". Defaults to today.
    """
    target_date = travel_date or datetime.now().strftime("%Y-%m-%d")

    manager = await get_manager()
    page = await manager.new_stealth_page()
    try:
        # ── Step 1: Find administrative code ──────────────────────────────────
        search_url = f"{_BMKG_WILAYAH}?q={urllib.parse.quote(city)}&limit=5"
        logger.info("BMKG wilayah search: %s", search_url)
        search_resp = await _fetch_json(page, search_url)

        if not search_resp:
            return WeatherOutput(location_name=city, error="Tidak dapat terhubung ke BMKG")

        places = (
            search_resp.get("data", [])
            if isinstance(search_resp, dict)
            else search_resp
        )
        if not places:
            return WeatherOutput(
                location_name=city,
                error=f"Wilayah '{city}' tidak ditemukan di database BMKG",
            )

        best = places[0]
        adm4 = best.get("adm4", "")
        desa = best.get("desa") or best.get("kecamatan") or city
        kota = best.get("kotkab") or best.get("kota") or ""
        province = best.get("provinsi", "")
        location_name = f"{desa}, {kota}".strip(", ")

        if not adm4:
            return WeatherOutput(
                location_name=location_name,
                province=province,
                error="Kode wilayah BMKG tidak tersedia untuk lokasi ini",
            )

        # ── Step 2: Fetch forecast ────────────────────────────────────────────
        cuaca_url = f"{_BMKG_CUACA}?adm4={adm4}"
        logger.info("BMKG cuaca fetch: %s", cuaca_url)
        cuaca_resp = await _fetch_json(page, cuaca_url)

        if not cuaca_resp:
            return WeatherOutput(
                location_name=location_name,
                province=province,
                error="Data cuaca tidak tersedia dari BMKG",
            )

        data_list = (
            cuaca_resp.get("data", [])
            if isinstance(cuaca_resp, dict)
            else cuaca_resp
        )
        if not data_list:
            return WeatherOutput(
                location_name=location_name,
                province=province,
                error="BMKG mengembalikan data cuaca kosong",
            )

        cuaca_raw = data_list[0].get("cuaca", []) if isinstance(data_list[0], dict) else []
        forecasts = _build_forecasts(cuaca_raw, target_date)

        return WeatherOutput(
            location_name=location_name,
            province=province,
            forecasts=forecasts,
            source="BMKG",
        )

    finally:
        await page.close()
