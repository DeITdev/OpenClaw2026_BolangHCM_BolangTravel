"""search_places_on_maps — drive Google Maps like a human.

Strategy:
1. Build a deep-link URL: https://www.google.com/maps/search/{query}+{city}
2. Wait for the results feed (role="feed").
3. Scroll the feed once or twice so lazy-loaded cards materialize.
4. Read each result <a> link — name comes from aria-label, the maps_url from href.
5. Walk the parent card to pick up rating / review count / address.

We never click into a result here; that's get_place_details's job. This
function should stay fast (~3-5s) so the agent can issue several searches.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import List, Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from browser.playwright_manager import get_manager
from config.selectors import MAPS_SEARCH
from config.settings import settings
from tools.schemas import PlaceSummary, SearchPlacesOutput

logger = logging.getLogger(__name__)

_MAX_SCROLL_ROUNDS = 4
_RATING_RE = re.compile(r"(\d+[.,]\d+)")
_REVIEW_RE = re.compile(r"\(([\d.,]+)\)")


def _build_search_url(query: str, city: str) -> str:
    q = urllib.parse.quote_plus(f"{query} {city}".strip())
    return f"https://www.google.com/maps/search/{q}?hl={settings.google_maps_hl}&gl=id"


async def _dismiss_consent_if_present(page: Page) -> None:
    """Google's EU consent banner sometimes appears on first hit. Best-effort accept."""
    for selector in (
        'button[aria-label*="Accept all" i]',
        'button[aria-label*="Setuju" i]',
        'button:has-text("Accept all")',
        'button:has-text("Saya setuju")',
    ):
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                await page.wait_for_timeout(500)
                return
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue


async def _scroll_feed(page: Page, rounds: int = _MAX_SCROLL_ROUNDS) -> None:
    """Scroll the results feed to trigger lazy load of more cards."""
    feed = page.locator(MAPS_SEARCH.results_container).first
    for _ in range(rounds):
        try:
            await feed.evaluate("(el) => el.scrollBy(0, el.clientHeight)")
            await page.wait_for_timeout(700)
        except Exception:
            break


def _parse_float(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    match = _RATING_RE.search(text.replace(",", "."))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _parse_review_count(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    match = _REVIEW_RE.search(text)
    raw = match.group(1) if match else text
    raw = raw.replace(".", "").replace(",", "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


async def _extract_card(card_link) -> Optional[PlaceSummary]:
    """Pull one PlaceSummary from a result link element."""
    try:
        name = await card_link.get_attribute(MAPS_SEARCH.name_attr_from_link)
        href = await card_link.get_attribute("href")
        if not name or not href:
            return None

        card = card_link.locator("xpath=ancestor::div[@role='article'][1]")
        if await card.count() == 0:
            card = card_link.locator("xpath=..")

        rating_text = None
        review_text = None
        address_text = None
        price_text = None

        try:
            rating_loc = card.locator(MAPS_SEARCH.rating_in_card).first
            if await rating_loc.count() > 0:
                rating_text = (await rating_loc.inner_text(timeout=500)).strip()
        except Exception:
            pass

        try:
            review_loc = card.locator(MAPS_SEARCH.review_count_in_card).first
            if await review_loc.count() > 0:
                review_text = (await review_loc.inner_text(timeout=500)).strip()
        except Exception:
            pass

        try:
            addr_locs = card.locator(MAPS_SEARCH.address_chip)
            count = await addr_locs.count()
            chips: List[str] = []
            for i in range(count):
                txt = (await addr_locs.nth(i).inner_text(timeout=300)).strip()
                if txt:
                    chips.append(txt)
            if chips:
                address_text = " · ".join(chips[:3])
        except Exception:
            pass

        try:
            price_loc = card.locator(MAPS_SEARCH.price_indicator).first
            if await price_loc.count() > 0:
                price_text = (await price_loc.inner_text(timeout=500)).strip()
        except Exception:
            pass

        return PlaceSummary(
            name=name.strip(),
            address=address_text,
            rating=_parse_float(rating_text),
            review_count=_parse_review_count(review_text),
            price_level=price_text or None,
            maps_url=href,
        )
    except Exception as e:
        logger.debug("Failed to parse result card: %s", e)
        return None


async def search_places_on_maps(query: str, city: str, limit: int = 6) -> SearchPlacesOutput:
    """Run a Google Maps text search and return up to `limit` places."""
    manager = await get_manager()
    page = await manager.new_stealth_page()
    try:
        url = _build_search_url(query, city)
        logger.info("Maps search → %s", url)

        # Maps SPA never fires `load` cleanly; domcontentloaded + selector wait
        # is both faster and more reliable. One retry on transient timeout.
        timeout_ms = max(settings.playwright_timeout_ms, 30000)
        goto_ok = False
        for attempt in (1, 2):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                goto_ok = True
                break
            except PlaywrightTimeoutError:
                logger.warning("Maps search goto timeout (attempt %d/2)", attempt)
                if attempt == 2:
                    return SearchPlacesOutput(query=query, city=city, places=[])
                await page.wait_for_timeout(500)

        if not goto_ok:
            return SearchPlacesOutput(query=query, city=city, places=[])

        await page.wait_for_timeout(1500)
        await _dismiss_consent_if_present(page)

        try:
            await page.wait_for_selector(MAPS_SEARCH.results_container, timeout=settings.playwright_timeout_ms)
        except PlaywrightTimeoutError:
            single_title = page.locator("h1.DUwDvf").first
            if await single_title.count() > 0:
                name = (await single_title.inner_text()).strip()
                return SearchPlacesOutput(
                    query=query,
                    city=city,
                    places=[PlaceSummary(name=name, maps_url=page.url)],
                )
            logger.warning("No results feed appeared for %r in %r", query, city)
            return SearchPlacesOutput(query=query, city=city, places=[])

        await _scroll_feed(page)

        links = page.locator(MAPS_SEARCH.result_link)
        total = await links.count()
        logger.info("Found %d candidate result links", total)

        out: List[PlaceSummary] = []
        seen_urls: set[str] = set()
        for i in range(total):
            if len(out) >= limit:
                break
            summary = await _extract_card(links.nth(i))
            if not summary:
                continue
            if summary.maps_url in seen_urls:
                continue
            seen_urls.add(summary.maps_url or "")
            out.append(summary)

        return SearchPlacesOutput(query=query, city=city, places=out)
    finally:
        await page.close()
