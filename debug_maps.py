"""Quick debug script — capture what Google Maps actually renders.

Run with:
    python3 debug_maps.py

Outputs:
    debug_maps_screenshot.png  — visual of the page after load
    debug_maps_body.html       — full page HTML to inspect selectors
"""

import asyncio
import sys
sys.path.insert(0, '.')
from playwright.async_api import async_playwright
from browser.stealth import apply_stealth


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--enable-unsafe-swiftshader",
                    "--ignore-gpu-blocklist",
                    "--disable-gpu-sandbox",
                ],
        )
        context = await browser.new_context(
            locale="id-ID",
            timezone_id="Asia/Jakarta",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "id,en;q=0.8"},
        )
        page = await context.new_page()
        await apply_stealth(page)

        # Capture browser console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append((msg.type, msg.text)) if msg.type in ("error", "warning") else None)
        page.on("pageerror", lambda err: console_errors.append(("pageerror", str(err))))

        url = "https://www.google.com/maps/search/kuliner+malam+Surabaya?hl=id&gl=id"
        print(f"Opening: {url}")

        # Use 'load' so all scripts execute, then wait for network to go idle,
        # then an extra pause for the SPA to render DOM nodes.
        await page.goto(url, wait_until="load")
        print("'load' fired — waiting for networkidle...")
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            print("  (networkidle timed out — continuing anyway)")
        print("Waiting 3s extra for SPA rendering...")
        await page.wait_for_timeout(3000)

        # Screenshot
        await page.screenshot(path="debug_maps_screenshot.png", full_page=False)
        print("Screenshot saved → debug_maps_screenshot.png")

        # HTML
        html = await page.content()
        with open("debug_maps_body.html", "w") as f:
            f.write(html)
        print(f"HTML saved → debug_maps_body.html ({len(html):,} chars)")

        # Check which candidate selectors exist
        print("\n--- Selector probe ---")
        candidates = [
            'div[role="feed"]',
            'div[role="main"]',
            'div[role="article"]',
            'div[role="listitem"]',
            'div[aria-label]',
            'div.m6QErb',
            'div.Nv2PK',
            'a[href*="/maps/place/"]',
            'a.hfpxzc',
            'div[jsaction*="mouseover"]',
            'div[jsaction*="click"]',
            'canvas',
            'h1',
            'input',
            'body',
        ]
        for sel in candidates:
            count = await page.locator(sel).count()
            print(f"  {sel!s:<45} → {count} match(es)")

        # Print first aria-label values to see what's rendered
        print("\n--- First 5 aria-label values ---")
        aria_els = page.locator("[aria-label]")
        aria_count = await aria_els.count()
        for i in range(min(5, aria_count)):
            label = await aria_els.nth(i).get_attribute("aria-label")
            tag = await aria_els.nth(i).evaluate("el => el.tagName")
            print(f"  <{tag.lower()}> aria-label={label!r}")

        # Console errors
        print(f"\n--- Console errors/warnings ({len(console_errors)}) ---")
        for t, msg in console_errors[:20]:
            print(f"  [{t}] {msg[:200]}")

        # Current URL (may have redirected)
        print(f"\nFinal URL: {page.url}")

        # Page title
        title = await page.title()
        print(f"Page title: {title}")

        await browser.close()


asyncio.run(main())
