"""get_place_details — fetch one place's full info from Google Maps.

We use the place-search deep link `?q=...` because hitting Google Maps with a
name + city in the URL lands us on the same single-place panel as if a user
clicked a search result. From there we scrape:

- title, address, rating, review count
- category chip
- phone / website
- the per-day opening-hours table (clicking the summary if needed)
- is_open_today, derived from today's row in the hours table

Sites with no hours (e.g. parks marked "Open 24 hours") return is_open_today=True
and a single "Buka 24 jam" entry.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import urllib.parse
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from browser.playwright_manager import get_manager
from config.selectors import MAPS_PLACE
from config.settings import settings
from tools.schemas import OpeningHoursEntry, PlaceDetails

SCREENSHOT_DIR = os.path.join(tempfile.gettempdir(), "travel_agent_screenshots")

logger = logging.getLogger(__name__)

# Cap concurrent place-detail fetches so 5+ parallel agent tool calls don't
# pile up on Chromium and starve each other on Google Maps' slow first paint.
_PLACE_DETAILS_SEMAPHORE = asyncio.Semaphore(3)

_RATING_RE = re.compile(r"(\d+[.,]\d+)")
_REVIEW_RE = re.compile(r"([\d.,]+)")

_DAY_LABELS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
_DAY_LABELS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _build_place_url(name: str, city: str) -> str:
    q = urllib.parse.quote_plus(f"{name} {city}".strip())
    return f"https://www.google.com/maps/search/{q}?hl={settings.google_maps_hl}&gl=id"


def _parse_float(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    match = _RATING_RE.search(text.replace(",", "."))
    return float(match.group(1)) if match else None


def _parse_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


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


async def _try_open_hours_table(page: Page) -> None:
    """The hours table is sometimes collapsed behind a 'show more' button."""
    for selector in (
        'div[aria-label*="Hours" i][role="button"]',
        'div[aria-label*="Jam" i][role="button"]',
        'div[jsaction*="openhours"]',
    ):
        try:
            btn = page.locator(selector).first
            if await btn.count() > 0 and await btn.is_visible(timeout=500):
                await btn.click()
                await page.wait_for_timeout(300)
                return
        except Exception:
            continue


async def _extract_hours(page: Page) -> Tuple[List[OpeningHoursEntry], Optional[bool]]:
    """Read the weekly hours table; return entries and today's open/closed flag."""
    await _try_open_hours_table(page)

    entries: List[OpeningHoursEntry] = []
    try:
        rows = page.locator(MAPS_PLACE.hours_table_row)
        count = await rows.count()
        for i in range(count):
            row = rows.nth(i)
            cells = row.locator("td")
            cell_count = await cells.count()
            if cell_count < 2:
                continue
            day_text = (await cells.nth(0).inner_text(timeout=400)).strip()
            hours_text = (await cells.nth(1).inner_text(timeout=400)).strip()
            day_text = day_text.split("\n")[0].strip()
            hours_text = hours_text.split("\n")[0].strip()
            if day_text:
                entries.append(OpeningHoursEntry(day=day_text, hours=hours_text or "Tidak tersedia"))
    except Exception as e:
        logger.debug("Hours table parse failed: %s", e)

    if not entries:
        try:
            label_el = page.locator('div[aria-label*="Hours" i], div[aria-label*="Jam" i]').first
            if await label_el.count() > 0:
                label = await label_el.get_attribute("aria-label")
                if label:
                    entries.append(OpeningHoursEntry(day="Hari ini", hours=label.strip()))
        except Exception:
            pass

    is_open_today = _derive_is_open_today(entries)
    return entries, is_open_today


def _derive_is_open_today(entries: List[OpeningHoursEntry]) -> Optional[bool]:
    if not entries:
        return None
    today_idx = datetime.now().weekday()  # 0 = Mon
    today_id = _DAY_LABELS_ID[today_idx].lower()
    today_en = _DAY_LABELS_EN[today_idx].lower()

    for e in entries:
        day_l = e.day.lower()
        if day_l.startswith(today_id) or day_l.startswith(today_en) or "hari ini" in day_l:
            hours_l = e.hours.lower()
            if "tutup" in hours_l or "closed" in hours_l:
                return False
            if "24 jam" in hours_l or "24 hours" in hours_l or "open 24" in hours_l:
                return True
            if re.search(r"\d{1,2}[:.]\d{2}", hours_l) or "buka" in hours_l or "open" in hours_l:
                return True
    return None


async def _get_attr_text(page: Page, selector: str) -> Optional[str]:
    try:
        el = page.locator(selector).first
        if await el.count() == 0:
            return None
        txt = await el.inner_text(timeout=600)
        return txt.strip() if txt else None
    except Exception:
        return None


async def _get_attr_value(page: Page, selector: str, attr: str) -> Optional[str]:
    try:
        el = page.locator(selector).first
        if await el.count() == 0:
            return None
        val = await el.get_attribute(attr)
        return val.strip() if val else None
    except Exception:
        return None


async def _wait_for_visual_ready(page: Page, max_wait_ms: int = 8000) -> None:
    """Wait until the place panel hero image and the map canvas have rendered.

    The screenshot needs to be visually complete (place photo + map tiles
    painted). We try in this order:
      1. Wait for the hero image element to be visible.
      2. Wait for `networkidle` with a short bounded budget so tile streams
         can settle.
      3. Final fixed delay so the GPU paint flush is observable.
    """
    try:
        hero_img = page.locator(
            'button[aria-label*="foto" i] img, '
            'button[jsaction*="heroHeaderImage"] img, '
            'div[role="img"][aria-label*="Foto" i]'
        ).first
        await hero_img.wait_for(state="visible", timeout=3500)
    except Exception:
        pass

    try:
        await page.wait_for_load_state("networkidle", timeout=max_wait_ms)
    except Exception:
        pass

    await page.wait_for_timeout(800)


async def _take_place_screenshot(page: Page, place_name: str) -> Optional[str]:
    """Capture viewport screenshot of the place panel and return the file path."""
    try:
        await _wait_for_visual_ready(page)
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        safe_name = re.sub(r"[^\w]", "_", place_name)[:30]
        path = os.path.join(SCREENSHOT_DIR, f"place_{safe_name}_{uuid.uuid4().hex[:6]}.png")
        await page.screenshot(path=path, full_page=False)
        logger.info("Place screenshot saved: %s", path)
        return path
    except Exception as e:
        logger.debug("Place screenshot failed: %s", e)
        return None


async def _goto_with_retry(page: Page, url: str, attempts: int = 2) -> bool:
    """Navigate to *url* using domcontentloaded with one fast retry.

    Returns True on success, False if all attempts time out.
    Maps' SPA never reliably fires the `load` event, and the page is usable
    once the DOM is parsed, so we wait only for `domcontentloaded`.
    """
    timeout_ms = max(settings.playwright_timeout_ms, 30000)
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return True
        except PlaywrightTimeoutError:
            logger.warning(
                "page.goto timeout (attempt %d/%d) for %s", attempt, attempts, url
            )
            if attempt == attempts:
                return False
            await page.wait_for_timeout(500)
        except Exception as exc:
            logger.debug("page.goto error (%s) for %s", exc, url)
            if attempt == attempts:
                return False
            await page.wait_for_timeout(500)
    return False


async def get_place_details(name: str, city: str) -> PlaceDetails:
    """Open the place panel in Google Maps and pull structured details + screenshot."""
    async with _PLACE_DETAILS_SEMAPHORE:
        return await _get_place_details_inner(name, city)


async def _get_place_details_inner(name: str, city: str) -> PlaceDetails:
    manager = await get_manager()
    # Keep images/tiles ON for this page — we screenshot it, so map and
    # place photo must be visible. Analytics-only blocking happens inside
    # the manager when block_resources=True; here we want a full render.
    page = await manager.new_stealth_page(block_resources=False)
    try:
        url = _build_place_url(name, city)
        logger.info("Maps place details → %s", url)

        if not await _goto_with_retry(page, url):
            logger.warning("All goto attempts timed out for %r in %r", name, city)
            return PlaceDetails(name=name, maps_url=url)

        await page.wait_for_timeout(1200)
        await _dismiss_consent_if_present(page)

        try:
            await page.wait_for_selector(
                MAPS_PLACE.title, timeout=settings.playwright_timeout_ms
            )
        except PlaywrightTimeoutError:
            try:
                first_link = page.locator("a.hfpxzc").first
                if await first_link.count() > 0:
                    await first_link.click()
                    await page.wait_for_selector(
                        MAPS_PLACE.title, timeout=settings.playwright_timeout_ms
                    )
                else:
                    raise
            except Exception:
                logger.warning("Could not load place panel for %r in %r", name, city)
                return PlaceDetails(name=name, maps_url=page.url)

        title_text = await _get_attr_text(page, MAPS_PLACE.title) or name
        rating_text = await _get_attr_text(page, MAPS_PLACE.rating)
        review_text = await _get_attr_text(page, MAPS_PLACE.review_count)
        address_text = await _get_attr_text(page, MAPS_PLACE.address_text)
        phone_text = await _get_attr_text(page, MAPS_PLACE.phone_text)
        website_url = await _get_attr_value(page, MAPS_PLACE.website_button, "href")
        category_text = await _get_attr_text(page, MAPS_PLACE.category_chip)

        hours_entries, is_open_today = await _extract_hours(page)

        screenshot_path = await _take_place_screenshot(page, title_text)

        return PlaceDetails(
            name=title_text,
            address=address_text,
            rating=_parse_float(rating_text),
            review_count=_parse_int(review_text),
            category=category_text,
            phone=phone_text,
            website=website_url,
            opening_hours=hours_entries,
            is_open_today=is_open_today,
            maps_url=page.url,
            screenshot_path=screenshot_path,
        )
    finally:
        await page.close()
