"""get_directions — multi-waypoint route from Google Maps.

Enhancements:
- Shortened maps.app.goo.gl URL via "Salin link" button response intercept.
- Route screenshot saved to a temp directory.
- Multi-mode transport info: Motor, Mobil, Bus/Transportasi Umum, Jalan Kaki.
  Each mode is loaded on its OWN fresh Playwright page to avoid stale DOM from
  Google Maps' SPA (navigating modes on the same tab keeps old trip cards visible
  during the transition, causing every mode to report identical data).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import urllib.parse
import uuid
from typing import Dict, List, Optional, Tuple

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from browser.playwright_manager import get_manager
from config.selectors import MAPS_DIRECTIONS
from config.settings import settings
from tools.schemas import DirectionsOutput

logger = logging.getLogger(__name__)

_KM_RE   = re.compile(r"([\d.,]+)\s*km", re.IGNORECASE)
_M_RE    = re.compile(r"([\d.,]+)\s*m\b", re.IGNORECASE)
_HOUR_RE = re.compile(r"(\d+)\s*(?:jam|hr|hour|h)\b", re.IGNORECASE)
_MIN_RE  = re.compile(r"(\d+)\s*(?:mnt|min|menit)\b", re.IGNORECASE)

SCREENSHOT_DIR = os.path.join(tempfile.gettempdir(), "travel_agent_screenshots")

# Transit-related keywords — if any appear in the card text the route is real transit
_TRANSIT_KW = ("halte", "stasiun", "bus", "angkot", "kereta", "transit",
               "mrt", "lrt", "krl", "grab", "ojek")


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

def build_directions_url(origin: str, waypoints: List[str], mode: str = "driving") -> str:
    parts = [urllib.parse.quote(origin.strip(), safe="")]
    for w in waypoints:
        if w and w.strip():
            parts.append(urllib.parse.quote(w.strip(), safe=""))
    path = "/".join(parts)
    return (
        f"https://www.google.com/maps/dir/{path}"
        f"?travelmode={mode}&hl={settings.google_maps_hl}&gl=id"
    )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_distance_km(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = (
        text.replace(".", "").replace(",", ".")
        if text.count(",") == 1 and text.count(".") > 1
        else text.replace(",", ".")
    )
    m = _KM_RE.search(cleaned)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            pass
    m = _M_RE.search(cleaned)
    if m:
        try:
            return float(m.group(1).replace(",", ".")) / 1000.0
        except ValueError:
            pass
    return None


def _parse_duration_minutes(text: str) -> Optional[int]:
    if not text:
        return None
    h = int(_HOUR_RE.search(text).group(1)) if _HOUR_RE.search(text) else 0
    mn = int(_MIN_RE.search(text).group(1)) if _MIN_RE.search(text) else 0
    total = h * 60 + mn
    return total if total > 0 else None


def _first_time_line(text: str) -> Optional[str]:
    for line in text.split("\n"):
        line = line.strip()
        if _HOUR_RE.search(line) or _MIN_RE.search(line):
            return line
    return None


def _format_summary(time_str: Optional[str], dist_km: Optional[float]) -> Optional[str]:
    if not time_str:
        return None
    if dist_km is not None:
        return f"{time_str} ({dist_km:.1f} km)"
    return time_str


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

async def _dismiss_consent_if_present(page: Page) -> None:
    for selector in (
        'button[aria-label*="Accept all" i]',
        'button[aria-label*="Setuju" i]',
    ):
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=800):
                await btn.click()
                await page.wait_for_timeout(400)
                return
        except Exception:
            continue


async def _read_first_card(page: Page) -> Tuple[Optional[str], Optional[float], str]:
    """Return (duration_text, distance_km, full_card_text) for the first trip card."""
    try:
        await page.wait_for_selector(MAPS_DIRECTIONS.trip_card, timeout=10000)
    except PlaywrightTimeoutError:
        return None, None, ""

    cards = await page.locator(MAPS_DIRECTIONS.trip_card).all()
    if not cards:
        return None, None, ""

    full_text = (await cards[0].inner_text(timeout=1500)).strip()

    # Try dedicated sub-elements first, fall back to regex on full text
    time_str: Optional[str] = None
    try:
        dur_el = cards[0].locator(MAPS_DIRECTIONS.duration_in_card).first
        if await dur_el.count() > 0:
            time_str = (await dur_el.inner_text(timeout=500)).strip() or None
    except Exception:
        pass
    if not time_str:
        time_str = _first_time_line(full_text)

    dist_km: Optional[float] = None
    try:
        dist_el = cards[0].locator(MAPS_DIRECTIONS.distance_in_card).first
        if await dist_el.count() > 0:
            dist_km = _parse_distance_km(
                (await dist_el.inner_text(timeout=500)).strip()
            )
    except Exception:
        pass
    if dist_km is None:
        dist_km = _parse_distance_km(full_text)

    return time_str, dist_km, full_text


async def _read_all_cards_text(page: Page) -> str:
    """Concatenate inner_text of every trip card on the page."""
    try:
        cards = await page.locator(MAPS_DIRECTIONS.trip_card).all()
        parts = []
        for card in cards:
            parts.append(await card.inner_text(timeout=800))
        return "\n".join(parts)
    except Exception:
        return ""


async def _navigate_and_load(page: Page, url: str, attempts: int = 2) -> bool:
    """Navigate using domcontentloaded with one retry. Returns True on success.

    `wait_until="load"` never reliably fires on Google Maps because map tiles
    keep streaming long after the DOM is usable. We wait for domcontentloaded
    instead and let `wait_for_selector` gate the actual extraction step.
    """
    timeout_ms = max(settings.playwright_timeout_ms, 30000)
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1500)
            await _dismiss_consent_if_present(page)
            return True
        except PlaywrightTimeoutError:
            logger.warning("goto timeout (attempt %d/%d) for %s", attempt, attempts, url)
            if attempt == attempts:
                return False
            await page.wait_for_timeout(500)
        except Exception as exc:
            logger.debug("goto error (%s) for %s", exc, url)
            if attempt == attempts:
                return False
            await page.wait_for_timeout(500)
    return False


# ---------------------------------------------------------------------------
# Short URL
# ---------------------------------------------------------------------------

async def _shorten_url(page: Page) -> Optional[str]:
    """Click 'Salin link' and intercept the batchexecute response to get maps.app.goo.gl URL."""
    collected: list[str] = []

    async def _on_response(response):
        try:
            if "batchexecute" not in response.url and "MapsUrlService" not in response.url:
                return
            body = await response.text()
            m = re.search(r"https://maps\.app\.goo\.gl/[A-Za-z0-9_-]+", body)
            if m:
                collected.append(m.group())
        except Exception:
            pass

    page.on("response", _on_response)
    try:
        btn = page.locator(
            'button:has-text("Salin link"), [aria-label*="Salin link" i]'
        ).first
        if await btn.count() > 0 and await btn.is_visible(timeout=2000):
            await btn.click()
            await page.wait_for_timeout(2500)
    except Exception as e:
        logger.debug("Salin link click failed: %s", e)
    finally:
        page.remove_listener("response", _on_response)

    return collected[0] if collected else None


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

async def _wait_for_route_visual_ready(page: Page, max_wait_ms: int = 8000) -> None:
    """Wait until the route polyline and map tiles have rendered before screenshotting."""
    try:
        await page.wait_for_load_state("networkidle", timeout=max_wait_ms)
    except Exception:
        pass
    await page.wait_for_timeout(1000)


async def _take_screenshot(page: Page, prefix: str = "route") -> Optional[str]:
    try:
        await _wait_for_route_visual_ready(page)
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOT_DIR, f"{prefix}_{uuid.uuid4().hex[:8]}.png")
        await page.screenshot(path=path, full_page=False)
        logger.info("Screenshot saved: %s", path)
        return path
    except Exception as e:
        logger.debug("Screenshot failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Per-mode fetch (each on its own fresh page)
# ---------------------------------------------------------------------------

async def _fetch_mode(
    origin: str, waypoints: List[str], mode: str
) -> Tuple[Optional[str], Optional[float], str]:
    """Open a brand-new Playwright page, navigate to *mode*, wait for trip cards.

    A fresh page is mandatory: Google Maps is an SPA and navigating between
    travelmode URLs on the same tab leaves the old mode's trip cards in the DOM
    until the new ones render, causing all modes to report identical results.

    Data-only fetch — `block_resources=True` (default) keeps it fast.
    """
    url = build_directions_url(origin, waypoints, mode)
    manager = await get_manager()
    page = await manager.new_stealth_page()
    try:
        logger.info("Fetching mode=%s ...", mode)
        if not await _navigate_and_load(page, url):
            return None, None, ""
        return await _read_first_card(page)
    except Exception as exc:
        logger.debug("_fetch_mode %s error: %s", mode, exc)
        return None, None, ""
    finally:
        await page.close()


async def _fetch_transit_with_validation(
    origin: str, waypoints: List[str]
) -> Optional[str]:
    """
    Fetch transit route and verify the result is real transit (not a driving
    fallback that Google sometimes returns when transit is unavailable).
    """
    url = build_directions_url(origin, waypoints, "transit")
    manager = await get_manager()
    page = await manager.new_stealth_page()
    try:
        logger.info("Fetching mode=transit ...")
        if not await _navigate_and_load(page, url):
            return None
        time_str, dist_km, _ = await _read_first_card(page)
        if not time_str:
            return None
        all_text = await _read_all_cards_text(page)
        has_transit = any(kw in all_text.lower() for kw in _TRANSIT_KW)
        if not has_transit:
            logger.debug("Transit page returned no transit keywords; treating as unavailable")
            return None
        return _format_summary(time_str, dist_km)
    except Exception as exc:
        logger.debug("_fetch_transit error: %s", exc)
        return None
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# Transport options orchestration
# ---------------------------------------------------------------------------

async def _get_transport_options(
    origin: str,
    waypoints: List[str],
    driving_time: Optional[str],
    driving_km: Optional[float],
) -> Dict[str, str]:
    """
    Build the full transport options dict by fetching each mode in parallel.

    Modes:
      Mobil          — primary driving result (already extracted, no new page)
      Motor          — travelmode=two-wheeler (separate page)
      Bus/Trans.Um   — travelmode=transit    (separate page + transit keyword check)
      Jalan Kaki     — travelmode=walking    (separate page; capped at 30 km)
    """
    # Fetch motor, transit, walking in parallel
    motor_fut    = asyncio.create_task(_fetch_mode(origin, waypoints, "two-wheeler"))
    transit_fut  = asyncio.create_task(_fetch_transit_with_validation(origin, waypoints))
    walking_fut  = asyncio.create_task(_fetch_mode(origin, waypoints, "walking"))

    (motor_time, motor_km, _), transit_summary, (walk_time, walk_km, _) = await asyncio.gather(
        motor_fut, transit_fut, walking_fut
    )

    options: Dict[str, str] = {}

    # --- Mobil ---
    options["Mobil"] = _format_summary(driving_time, driving_km) or "Tidak tersedia"

    # --- Motor ---
    motor_summary = _format_summary(motor_time, motor_km)
    if motor_summary:
        options["Motor"] = motor_summary
    elif driving_time and driving_km:
        # Heuristic: motor ~10 % faster than car for inter-city routes
        est = _parse_duration_minutes(driving_time)
        if est:
            est = int(est * 0.9)
            h, m = divmod(est, 60)
            t = f"{h} jam {m} mnt" if h else f"{m} mnt"
            options["Motor"] = f"~{t} ({driving_km:.1f} km, estimasi)"
        else:
            options["Motor"] = "Tidak tersedia"
    else:
        options["Motor"] = "Tidak tersedia"

    # --- Bus / Transportasi Umum ---
    options["Bus / Transportasi Umum"] = (
        transit_summary if transit_summary else "Tidak tersedia rute langsung"
    )

    # --- Jalan Kaki ---
    walk_summary = _format_summary(walk_time, walk_km)
    if walk_km is not None and walk_km > 30:
        options["Jalan Kaki"] = "Terlalu jauh untuk berjalan kaki"
    elif walk_summary and (driving_km is None or walk_km is None or walk_km <= driving_km * 2.0):
        options["Jalan Kaki"] = walk_summary
    elif driving_km is not None and driving_km > 30:
        options["Jalan Kaki"] = "Terlalu jauh untuk berjalan kaki"
    else:
        options["Jalan Kaki"] = "Tidak tersedia"

    return options


# ---------------------------------------------------------------------------
# Primary extract (driving page that stays open for screenshot + short URL)
# ---------------------------------------------------------------------------

async def _extract_primary_trip(
    page: Page,
) -> Tuple[Optional[float], Optional[str], Optional[int]]:
    """Read distance / duration from the first trip card on the already-loaded page."""
    try:
        card = page.locator(MAPS_DIRECTIONS.primary_route_summary).first
        if await card.count() == 0:
            card = page.locator(MAPS_DIRECTIONS.trip_card).first
        if await card.count() == 0:
            return None, None, None

        full_text = (await card.inner_text(timeout=1500)).strip()
        duration_text: Optional[str] = None
        distance_km: Optional[float] = None

        try:
            dur_el = card.locator(MAPS_DIRECTIONS.duration_in_card).first
            if await dur_el.count() > 0:
                duration_text = (await dur_el.inner_text(timeout=500)).strip() or None
        except Exception:
            pass

        try:
            dist_el = card.locator(MAPS_DIRECTIONS.distance_in_card).first
            if await dist_el.count() > 0:
                distance_km = _parse_distance_km(
                    (await dist_el.inner_text(timeout=500)).strip()
                )
        except Exception:
            pass

        if duration_text is None:
            duration_text = _first_time_line(full_text)
        if distance_km is None:
            distance_km = _parse_distance_km(full_text)

        return distance_km, duration_text, _parse_duration_minutes(duration_text or "")
    except Exception as exc:
        logger.debug("Failed to read trip card: %s", exc)
        return None, None, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def get_directions(origin: str, waypoints: List[str]) -> DirectionsOutput:
    """Open Google Maps Directions and return route totals, short URL, screenshot, and transport options."""
    driving_url = build_directions_url(origin, waypoints, mode="driving")
    manager = await get_manager()
    # Keep images on for this page so the route screenshot shows map tiles.
    page = await manager.new_stealth_page(block_resources=False)
    try:
        logger.info("Maps directions (driving) → %s", driving_url)
        if not await _navigate_and_load(page, driving_url):
            logger.warning("Driving page failed to load; returning URL only.")
            return DirectionsOutput(
                origin=origin, stops=list(waypoints), maps_url=driving_url
            )

        try:
            await page.wait_for_selector(
                MAPS_DIRECTIONS.trip_card, timeout=settings.playwright_timeout_ms
            )
        except PlaywrightTimeoutError:
            logger.warning("No trip card appeared; returning URL only.")
            return DirectionsOutput(
                origin=origin, stops=list(waypoints), maps_url=driving_url
            )

        await page.wait_for_timeout(800)

        # 1. Primary route info from the driving page
        distance_km, duration_text, duration_minutes = await _extract_primary_trip(page)

        # 2. Screenshot while on the driving results view
        screenshot_path = await _take_screenshot(page, prefix="route")

        # 3. Shortened URL (navigates within same page — must be after screenshot)
        maps_url_short = await _shorten_url(page)

    finally:
        await page.close()

    # 4. Transport options — each mode gets its OWN fresh page (parallel)
    transport_options = await _get_transport_options(
        origin, waypoints, duration_text, distance_km
    )

    return DirectionsOutput(
        origin=origin,
        stops=list(waypoints),
        total_km=distance_km,
        total_duration_text=duration_text,
        total_duration_minutes=duration_minutes,
        maps_url=driving_url,
        maps_url_short=maps_url_short,
        route_screenshot_path=screenshot_path,
        transport_options=transport_options,
    )
