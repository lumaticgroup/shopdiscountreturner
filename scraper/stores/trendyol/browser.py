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


class ChromiumDied(RuntimeError):
    """
    Raised when Chromium is killed by the host between launch and first page
    creation. On container hosts (justrunmy.app, Fly, small Docker) this is
    almost always OOM. Message carries the memory snapshot so operators see
    the exact numbers in the log and in the /discounts UI reply.
    """


def _read_meminfo() -> dict:
    """Best-effort snapshot from /proc/meminfo. Returns {} off-Linux."""
    try:
        with open("/proc/meminfo") as f:
            out = {}
            for line in f:
                k, _, rest = line.partition(":")
                val = rest.strip().split()
                if val and val[0].isdigit():
                    out[k.strip()] = int(val[0])  # kB
            return out
    except OSError:
        return {}


def _read_cgroup_mem_limit() -> Optional[int]:
    """
    Return the container's memory limit in bytes, or None if not
    cgroup-limited. Tries cgroup v2 first, falls back to v1.
    """
    for path in (
        "/sys/fs/cgroup/memory.max",                    # v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # v1
    ):
        try:
            with open(path) as f:
                raw = f.read().strip()
            if raw == "max":
                return None
            n = int(raw)
            # v1 uses a huge sentinel value for "unlimited"; anything > 1 TB
            # isn't a real container limit.
            if n > (1 << 40):
                return None
            return n
        except (OSError, ValueError):
            continue
    return None


def _log_memory_state(where: str) -> None:
    """
    Log a short RAM snapshot so OOM kills are self-diagnosable from the log
    alone. No-op if we can't read /proc (macOS dev boxes).
    """
    mi = _read_meminfo()
    if not mi:
        return
    limit = _read_cgroup_mem_limit()
    total_mb = mi.get("MemTotal", 0) // 1024
    avail_mb = mi.get("MemAvailable", 0) // 1024
    limit_mb = (limit or 0) // (1024 * 1024)
    logger.info(
        "mem %s: available=%d MB, host_total=%d MB, cgroup_limit=%s",
        where, avail_mb, total_mb,
        f"{limit_mb} MB" if limit else "none",
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
        _log_memory_state("before chromium launch")
        # Container-safe Chromium flags. Notes on why each one is here:
        #   --no-sandbox / --disable-setuid-sandbox: most container runtimes
        #     (justrunmy.app included) don't grant Chromium's sandbox caps.
        #   --disable-dev-shm-usage: /dev/shm is often 64 MB in containers,
        #     which kills the renderer.
        #   --single-process: all Chromium subsystems in one OS process. Uses
        #     LESS total RAM in tiny containers (no per-tab renderer fork).
        #     Trade-off: slower and less stable, but on <=1 GB plans this is
        #     the difference between "works" and "OOM-killed".
        #   --renderer-process-limit=1: enforce it even if --single-process
        #     is somehow ignored.
        #   --disable-features=Translate,BackForwardCache,...: shave background
        #     memory.
        #   --js-flags=--max-old-space-size=256: cap V8 heap so we don't push
        #     over the container limit warming a single page.
        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
                "--renderer-process-limit=1",
                "--disable-features=Translate,BackForwardCache,IsolateOrigins,site-per-process",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-extensions",
                "--js-flags=--max-old-space-size=256",
            ],
            chromium_sandbox=False,
        )
        _log_memory_state("after chromium launch")
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
        # Smaller viewport = smaller raster = less RAM. We only need
        # __navigation__PROPS anyway, not visual fidelity.
        ctx = await self._browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 800, "height": 600},
            java_script_enabled=True,
            bypass_csp=True,
        )
        # Block images/media/fonts — they're the biggest contributors to
        # renderer RAM on a homepage load. Trendyol still injects the JS
        # state blob we need without them.
        async def _block(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await ctx.route("**/*", _block)
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
        try:
            page = await ctx.new_page()
        except Exception as e:
            # Almost always Chromium was OOM-killed between new_context and
            # new_page. Surface a diagnostic message that names the cause so
            # /discounts shows something actionable instead of "Scrape failed".
            _log_memory_state("at TargetClosed")
            limit = _read_cgroup_mem_limit()
            limit_str = f"{limit // (1024 * 1024)} MB" if limit else "no cgroup limit"
            raise ChromiumDied(
                f"Chromium died before a page could be opened "
                f"({type(e).__name__}: {e}). Container memory limit is "
                f"{limit_str}. Trendyol scraping needs ≥1 GB RAM; bump the "
                f"justrunmy.app plan or use a prescraped DB for the demo."
            ) from e
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
