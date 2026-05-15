"""web_search — Google Search via Playwright (no API key required).

The agent uses this when it needs facts that Google Maps doesn't have:
ticket prices, current fuel prices, opening-hour exceptions during holidays.

We parse the top organic results: title + snippet + URL. The snippet is the
critical bit — the agent reads it directly to answer questions like
"what's the entrance fee?" without needing to follow links.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import List

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from browser.playwright_manager import get_manager
from config.selectors import GOOGLE_SEARCH
from config.settings import settings
from tools.schemas import WebSearchHit, WebSearchOutput

logger = logging.getLogger(__name__)


def _build_search_url(query: str) -> str:
    q = urllib.parse.quote_plus(query.strip())
    return f"https://www.google.com/search?q={q}&hl={settings.google_maps_hl}&num=10"


async def _dismiss_consent_if_present(page: Page) -> None:
    for selector in (
        'button[aria-label*="Accept all" i]',
        'button[aria-label*="Setuju" i]',
        'button:has-text("Accept all")',
        'button:has-text("Saya setuju")',
    ):
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=800):
                await btn.click()
                await page.wait_for_timeout(300)
                return
        except Exception:
            continue


async def _extract_hits(page: Page, max_results: int) -> List[WebSearchHit]:
    hits: List[WebSearchHit] = []
    blocks = page.locator(GOOGLE_SEARCH.result_block)
    count = await blocks.count()
    seen_urls: set[str] = set()
    for i in range(count):
        if len(hits) >= max_results:
            break
        block = blocks.nth(i)
        try:
            link_loc = block.locator(GOOGLE_SEARCH.result_link).first
            if await link_loc.count() == 0:
                continue
            href = await link_loc.get_attribute("href")
            if not href or not href.startswith("http"):
                continue
            if href in seen_urls:
                continue

            title = ""
            title_loc = block.locator(GOOGLE_SEARCH.result_title).first
            if await title_loc.count() > 0:
                title = (await title_loc.inner_text(timeout=400)).strip()

            snippet = ""
            snippet_loc = block.locator(GOOGLE_SEARCH.result_snippet).first
            if await snippet_loc.count() > 0:
                snippet = (await snippet_loc.inner_text(timeout=400)).strip()

            if not title and not snippet:
                continue

            seen_urls.add(href)
            hits.append(WebSearchHit(title=title or "(no title)", snippet=snippet, url=href))
        except Exception as e:
            logger.debug("Skipping a search result block: %s", e)
            continue
    return hits


async def web_search(query: str, max_results: int = 5) -> WebSearchOutput:
    """Run a Google Search and return parsed organic results."""
    manager = await get_manager()
    page = await manager.new_stealth_page()
    try:
        url = _build_search_url(query)
        logger.info("Web search → %s", url)
        await page.goto(url, wait_until="domcontentloaded")
        await _dismiss_consent_if_present(page)

        try:
            await page.wait_for_selector(GOOGLE_SEARCH.result_block, timeout=settings.playwright_timeout_ms)
        except PlaywrightTimeoutError:
            logger.warning("No search results appeared for %r", query)
            return WebSearchOutput(query=query, hits=[])

        hits = await _extract_hits(page, max_results)
        return WebSearchOutput(query=query, hits=hits)
    finally:
        await page.close()
