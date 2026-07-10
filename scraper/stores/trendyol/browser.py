"""
Playwright browser session used to talk to Trendyol.

We can't call Trendyol's product API with a plain HTTP client — Cloudflare
bot management rejects requests without the right TLS fingerprint and the
`__cf_bm` cookie earned by loading a real HTML page. So we launch ONE
Chromium per scrape run, keep ONE `BrowserContext` per storefront (each
warmed with the storefront's cookies + a homepage load), and issue API
calls through `page.evaluate("fetch(...)")` from inside that context.

Naming is legacy — the file used to contain the DOM scrape fallback,
which is now dead because Trendyol reworked their product-card markup
AND the API works reliably enough not to need a fallback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, Optional
from urllib.parse import urlencode

from .storefronts import TrendyolStorefront as Storefront

logger = logging.getLogger("scraper.session")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# JS body of the in-browser fetch. Runs inside the page context, so
# Cloudflare sees the real browser TLS fingerprint and cookie jar.
_FETCH_JS = """
async ({url}) => {
  try {
    const r = await fetch(url, {
      credentials: "include",
      headers: { "Accept": "application/json, text/plain, */*" },
    });
    const status = r.status;
    let body = null;
    try { body = await r.json(); } catch (_) { body = null; }
    return { status, body };
  } catch (e) {
    return { status: 0, error: String(e) };
  }
}
"""


class PlaywrightSession:
    """
    Owns one Chromium instance per scrape run, and one warmed page per
    storefront. Callers `warm_storefront(sf)` up front, then repeatedly
    `fetch_api_json(sf, ...)` to hit apigw.trendyol.com.
    """

    def __init__(self, concurrency: int = 3, headless: bool = True):
        self._headless = headless
        self._sem = asyncio.Semaphore(concurrency)
        self._pw = None
        self._browser = None
        # keyed by storefront.code
        self._pages: dict[str, Any] = {}

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            for page in self._pages.values():
                ctx = page.context
                try:
                    await page.close()
                finally:
                    await ctx.close()
        finally:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()

    async def warm_storefront(self, sf: Storefront) -> dict:
        """
        Set storefront cookies, load the storefront homepage to earn CF's
        session cookies, and return the JS state blob
        `window["__navigation__PROPS"]` (used for top-level category
        discovery). The page is kept alive on the session for subsequent
        API fetches.

        Returns {} if the state blob is missing.
        """
        ctx = await self._browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1440, "height": 900},
        )
        await ctx.add_cookies([
            {"name": "storefrontId", "value": str(sf.storefront_id),
             "domain": ".trendyol.com", "path": "/"},
            {"name": "countryCode", "value": sf.country_code,
             "domain": ".trendyol.com", "path": "/"},
            {"name": "language", "value": sf.language,
             "domain": ".trendyol.com", "path": "/"},
            {"name": "platform", "value": "web",
             "domain": ".trendyol.com", "path": "/"},
            {"name": "AZ_SELECTED", "value": "true",
             "domain": ".trendyol.com", "path": "/"},
        ])
        page = await ctx.new_page()
        home_url = sf.base_url + (sf.home_path or "/")
        logger.info("Warming %s at %s", sf.code, home_url)
        await page.goto(home_url, wait_until="domcontentloaded", timeout=45000)
        # Give the SSR-injected globals a moment to attach.
        await page.wait_for_timeout(1500)

        nav_props = await page.evaluate(
            "() => window['__navigation__PROPS'] || null"
        )
        self._pages[sf.code] = page
        return nav_props or {}

    async def _reforce_storefront_cookies(self, sf: Storefront) -> None:
        """
        Trendyol's server rewrites `storefrontId`/`countryCode` cookies
        during page loads and even during API calls, geolocating us based
        on IP. We have to re-pin them before every API call or we drift
        to whatever storefront the edge picks.
        """
        page = self._pages[sf.code]
        await page.context.add_cookies([
            {"name": "storefrontId", "value": str(sf.storefront_id),
             "domain": ".trendyol.com", "path": "/"},
            {"name": "countryCode", "value": sf.country_code,
             "domain": ".trendyol.com", "path": "/"},
            {"name": "language", "value": sf.language,
             "domain": ".trendyol.com", "path": "/"},
        ])

    async def fetch_api_json(
        self,
        sf: Storefront,
        path: str,
        params: dict,
    ) -> Optional[dict]:
        """
        Issue a GET against apigw.trendyol.com through the warmed page's
        `fetch()` so Cloudflare accepts it. Returns parsed JSON on 200,
        None otherwise. Applies the session's concurrency + politeness
        delay.
        """
        page = self._pages.get(sf.code)
        if page is None:
            raise RuntimeError(
                f"storefront '{sf.code}' not warmed — call warm_storefront() first"
            )

        # Merge the mandatory identity params so callers don't have to.
        merged = {
            "channelId": 1,
            "storefrontId": sf.storefront_id,
            "culture": sf.culture,
            **params,
        }
        url = f"https://apigw.trendyol.com{path}?{urlencode(merged, doseq=True)}"

        async with self._sem:
            await self._reforce_storefront_cookies(sf)
            try:
                result = await page.evaluate(_FETCH_JS, {"url": url})
            except Exception as e:
                logger.warning("in-browser fetch crashed: %s", e)
                return None
            finally:
                await asyncio.sleep(random.uniform(1.5, 3.0))

        status = result.get("status")
        if status != 200:
            logger.info("API %s → status %s", path, status)
            return None
        return result.get("body")
