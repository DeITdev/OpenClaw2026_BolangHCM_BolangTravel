"""Shared Playwright browser manager.

One Chromium instance is reused across tool calls; each tool gets a fresh
stealthed page (cheap, ~50ms) instead of paying ~1s of browser startup per
invocation. The manager is process-wide and lazy-initialized, so the first
tool call boots Playwright and the rest reuse it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from browser.stealth import apply_stealth
from config.settings import settings

logger = logging.getLogger(__name__)


class PlaywrightManager:
    """Singleton-style wrapper around a single Chromium browser.

    Pages are cheap and isolated; contexts can be reused for the same logical
    'user session' to keep cookies (Google consent banner etc.) sticky.
    """

    _instance: Optional["PlaywrightManager"] = None
    _init_lock = asyncio.Lock()

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._default_context: Optional[BrowserContext] = None
        self._start_lock = asyncio.Lock()

    @classmethod
    async def get(cls) -> "PlaywrightManager":
        async with cls._init_lock:
            if cls._instance is None:
                cls._instance = PlaywrightManager()
            return cls._instance

    async def _ensure_started(self) -> None:
        if self._browser is not None:
            return
        async with self._start_lock:
            if self._browser is not None:
                return
            logger.info("Starting Playwright (headless=%s)", settings.playwright_headless)
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=settings.playwright_headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--enable-unsafe-swiftshader",
                    "--ignore-gpu-blocklist",
                    "--disable-gpu-sandbox",
                ],
            )
            self._default_context = await self._new_context()

    async def _new_context(self) -> BrowserContext:
        assert self._browser is not None
        context = await self._browser.new_context(
            locale=f"{settings.google_maps_locale}-ID",
            timezone_id="Asia/Jakarta",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": f"{settings.google_maps_locale},en;q=0.8"},
        )
        context.set_default_timeout(settings.playwright_timeout_ms)
        return context

    async def new_stealth_page(
        self,
        fresh_context: bool = False,
        block_resources: bool = True,
    ) -> Page:
        """Return a freshly stealthed page.

        fresh_context=True spins up a brand-new context (no shared cookies),
        useful when prior runs left Google Maps in an awkward state.

        block_resources=True (default) drops images/fonts/analytics so DOM
        loads in ~3s instead of waiting 20s+ for map tiles. Set False when a
        visually accurate screenshot is required (e.g. route map).
        """
        await self._ensure_started()
        context = await self._new_context() if fresh_context else self._default_context
        assert context is not None
        page = await context.new_page()
        await apply_stealth(page)
        if block_resources:
            await self._install_resource_blocker(page)
        return page

    @staticmethod
    async def _install_resource_blocker(page: Page) -> None:
        """Skip heavy resources that aren't needed for DOM scraping.

        Map tiles, fonts, analytics, and ads make `wait_until=load` hang for
        20s+ on slow links. We only need DOM text + screenshot, so abort
        everything else.
        """
        block_types = {"image", "media", "font", "websocket", "manifest", "ping", "beacon"}
        block_url_substrings = (
            "googletagmanager", "google-analytics", "doubleclick", "gstatic.com/recaptcha",
            "googleadservices", "googlesyndication", "/gen_204", "/log204",
        )

        async def _route(route, request):
            try:
                if request.resource_type in block_types:
                    await route.abort()
                    return
                url = request.url
                if any(s in url for s in block_url_substrings):
                    await route.abort()
                    return
                await route.continue_()
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass

        try:
            await page.route("**/*", _route)
        except Exception:
            logger.debug("Failed to install resource blocker", exc_info=True)

    async def shutdown(self) -> None:
        logger.info("Shutting down Playwright")
        if self._default_context is not None:
            await self._default_context.close()
            self._default_context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


async def get_manager() -> PlaywrightManager:
    return await PlaywrightManager.get()


async def shutdown_manager() -> None:
    """Best-effort shutdown for app exit."""
    manager = PlaywrightManager._instance
    if manager is not None:
        await manager.shutdown()
        PlaywrightManager._instance = None
