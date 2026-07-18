"""
Trendyol-specific adapter over the shared `BrowserSession`
(`scraper/stores/_browser/session.py`).

We can't call Trendyol's product API with a plain HTTP client — Cloudflare
bot management rejects requests without the right TLS fingerprint and the
`__cf_bm` cookie earned by loading a real HTML page. The generic Chromium
machinery (container-safe flags, OOM diagnostics, in-page fetch) lives in
the shared session; this module owns what's Trendyol's alone: the
storefront cookie pinning, the `__navigation__PROPS` warm-up extraction,
and the apigw.trendyol.com URL/identity-param construction.

Public surface (`PlaywrightSession.warm_storefront` / `.fetch_api_json`,
`ChromiumDied`) is unchanged from before the extraction.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlencode

from .._browser import BrowserSession, ChromiumDied  # noqa: F401 (re-export)
from .storefronts import TrendyolStorefront as Storefront

logger = logging.getLogger("scraper.trendyol.browser")

_NAV_PROPS_JS = "() => window['__navigation__PROPS'] || null"


def _storefront_cookies(sf: Storefront, *, full: bool) -> list[dict]:
    """
    Trendyol's server rewrites `storefrontId`/`countryCode` cookies during
    page loads and even during API calls, geolocating us based on IP. The
    warm-up sets the `full` jar; before every API call we re-pin the three
    identity cookies or we drift to whatever storefront the edge picks.
    """
    cookies = [
        {"name": "storefrontId", "value": str(sf.storefront_id),
         "domain": ".trendyol.com", "path": "/"},
        {"name": "countryCode", "value": sf.country_code,
         "domain": ".trendyol.com", "path": "/"},
        {"name": "language", "value": sf.language,
         "domain": ".trendyol.com", "path": "/"},
    ]
    if full:
        cookies += [
            {"name": "platform", "value": "web",
             "domain": ".trendyol.com", "path": "/"},
            {"name": "AZ_SELECTED", "value": "true",
             "domain": ".trendyol.com", "path": "/"},
        ]
    return cookies


class PlaywrightSession:
    """
    Trendyol view of the shared browser session. Callers
    `warm_storefront(sf)` up front, then repeatedly `fetch_api_json(sf, ...)`
    to hit apigw.trendyol.com.
    """

    def __init__(self, concurrency: int = 3, headless: bool = True):
        self._session = BrowserSession(concurrency=concurrency, headless=headless)

    @property
    def _pages(self) -> dict[str, Any]:
        # Diagnostics (scripts/inspect_page.py) reach in intentionally.
        return self._session._pages

    async def __aenter__(self):
        await self._session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return await self._session.__aexit__(exc_type, exc, tb)

    async def warm_storefront(self, sf: Storefront) -> dict:
        """
        Set storefront cookies, load the storefront homepage to earn CF's
        session cookies, and return the JS state blob
        `window["__navigation__PROPS"]` (used for top-level category
        discovery). The page is kept alive on the session for subsequent
        API fetches.

        Returns {} if the state blob is missing.
        """
        home_url = sf.base_url + (sf.home_path or "/")
        nav_props = await self._session.warm(
            sf.code,
            home_url,
            cookies=_storefront_cookies(sf, full=True),
            extract_js=_NAV_PROPS_JS,
        )
        return nav_props or {}

    async def fetch_api_json(
        self,
        sf: Storefront,
        path: str,
        params: dict,
    ) -> Optional[dict]:
        """
        Issue a GET against apigw.trendyol.com through the warmed page's
        `fetch()` so Cloudflare accepts it. Returns parsed JSON on 200,
        None otherwise.
        """
        # Merge the mandatory identity params so callers don't have to.
        merged = {
            "channelId": 1,
            "storefrontId": sf.storefront_id,
            "culture": sf.culture,
            **params,
        }
        url = f"https://apigw.trendyol.com{path}?{urlencode(merged, doseq=True)}"
        await self._session.add_cookies(sf.code, _storefront_cookies(sf, full=False))
        return await self._session.fetch_json(sf.code, url)
