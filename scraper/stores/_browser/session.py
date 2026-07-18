"""
Store-agnostic Playwright browser session.

Some stores can't be scraped with a plain HTTP client — bot management
(Cloudflare, Shein risk control) rejects requests without a
real browser TLS fingerprint and the session cookies earned by loading an
actual page. For those we launch ONE Chromium per scrape run, keep ONE
warmed `BrowserContext` per context key (a storefront code), and either
issue API calls through `page.evaluate("fetch(...)")` or navigate listing
pages and extract their SSR JS state.

Extracted from `scraper/stores/trendyol/browser.py`, which is now a thin
Trendyol-specific adapter over this class. The container-safety notes
(memory flags, OOM diagnostics) carry over unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Optional

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


class EdgeBlocked(RuntimeError):
    """
    Raised when the site's edge kills the connection at the network level
    (net::ERR_HTTP2_PROTOCOL_ERROR, connection resets/timeouts) even for a
    real Chromium. That's IP/geo blocking — some edges (e.g. Akamai) drop
    traffic from outside their service regions — and no amount of header or
    fingerprint work fixes it. Run from an allowed network or via proxy.
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


def _warm_failure(where: str, url: str, e: Exception) -> RuntimeError:
    """
    Classify a warm-up failure into the right operator-facing diagnostic:
    a `net::ERR_*` from the navigation is the site's edge refusing us
    (EdgeBlocked), anything else is Chromium dying under us — almost
    always the container OOM-killing the browser (ChromiumDied).
    """
    msg = str(e)
    if "net::ERR_" in msg:
        err_code = msg.split("net::", 1)[1].split()[0].rstrip(".,")
        return EdgeBlocked(
            f"{url} refused the connection at the network level "
            f"(net::{err_code}). The site's edge is dropping this IP/region "
            f"— header or browser tweaks won't help. Run the scraper from an "
            f"allowed network (e.g. the production host's region) or through "
            f"a proxy."
        )
    _log_memory_state(f"at TargetClosed {where}")
    limit = _read_cgroup_mem_limit()
    limit_str = f"{limit // (1024 * 1024)} MB" if limit else "no cgroup limit"
    return ChromiumDied(
        f"Chromium died {where} ({type(e).__name__}: {e}). Container memory "
        f"limit is {limit_str}. Browser-based scraping needs ≥1 GB RAM; bump "
        f"the justrunmy.app plan or use a prescraped DB for the demo."
    )


# JS body of the in-browser fetch. Runs inside the page context, so the
# site's bot management sees the real browser TLS fingerprint and cookie jar.
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


class BrowserSession:
    """
    Owns one Chromium instance per scrape run, and one warmed page per
    context key (callers use their storefront code). Callers `warm(key, url)`
    up front, then repeatedly `fetch_json(key, ...)` for in-page API calls
    or `goto_and_extract(key, ...)` for SSR-state extraction.
    """

    def __init__(self, concurrency: int = 3, headless: bool = True):
        self._headless = headless
        self._sem = asyncio.Semaphore(concurrency)
        self._pw = None
        self._browser = None
        # keyed by the caller's context key (storefront code)
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
        # Every step tolerates an already-dead browser: Chromium can be
        # OOM-killed (or crash on a heavy page under --single-process)
        # mid-run, and cleanup must not mask the scrape's actual result.
        for page in self._pages.values():
            ctx = page.context
            try:
                await page.close()
            except Exception:
                pass
            try:
                await ctx.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            await self._pw.stop()

    def page(self, key: str):
        """The warmed page for `key` (diagnostics; raises if not warmed)."""
        if key not in self._pages:
            raise RuntimeError(f"context '{key}' not warmed — call warm() first")
        return self._pages[key]

    async def warm(
        self,
        key: str,
        url: str,
        *,
        cookies: Optional[list[dict]] = None,
        extract_js: Optional[str] = None,
        settle_ms: int = 1500,
    ) -> Any:
        """
        Create a context, set `cookies`, load `url` to earn the site's
        session/bot cookies, and optionally evaluate `extract_js` (a JS
        function source) on the loaded page. The page is kept alive on the
        session under `key` for subsequent calls.

        Returns the `extract_js` result (None if not given / not found).
        """
        # Smaller viewport = smaller raster = less RAM. We only need the JS
        # state blobs anyway, not visual fidelity.
        ctx = await self._browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 800, "height": 600},
            java_script_enabled=True,
            bypass_csp=True,
        )
        # Block images/media/fonts — they're the biggest contributors to
        # renderer RAM on a page load. The JS state blobs we need are
        # injected without them.
        async def _block(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await ctx.route("**/*", _block)
        if cookies:
            await ctx.add_cookies(cookies)
        try:
            page = await ctx.new_page()
        except Exception as e:
            raise _warm_failure("before a page could be opened", url, e) from e
        logger.info("Warming %s at %s", key, url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # Give the SSR-injected globals a moment to attach.
            await page.wait_for_timeout(settle_ms)
            extracted = (
                await page.evaluate(extract_js) if extract_js else None
            )
        except Exception as e:
            raise _warm_failure("while loading the warm-up page", url, e) from e
        self._pages[key] = page
        return extracted

    async def add_cookies(self, key: str, cookies: list[dict]) -> None:
        """
        (Re-)pin cookies on the warmed context. Several sites rewrite their
        storefront/currency cookies on every response, geolocating by IP —
        callers re-pin before each request or navigation.
        """
        await self.page(key).context.add_cookies(cookies)

    async def fetch_json(self, key: str, url: str) -> Optional[dict]:
        """
        Issue a GET through the warmed page's `fetch()` so bot management
        accepts it. Returns parsed JSON on 200, None otherwise. Applies the
        session's concurrency + politeness delay.
        """
        page = self.page(key)
        async with self._sem:
            try:
                result = await page.evaluate(_FETCH_JS, {"url": url})
            except Exception as e:
                logger.warning("in-browser fetch crashed: %s", e)
                return None
            finally:
                await asyncio.sleep(random.uniform(1.5, 3.0))

        status = result.get("status")
        if status != 200:
            logger.info("API %s → status %s", url, status)
            return None
        return result.get("body")

    async def goto_and_extract(
        self,
        key: str,
        url: str,
        extract_js: str,
        *,
        settle_ms: int = 1200,
        block_scripts: bool = False,
    ) -> tuple[str, Any]:
        """
        Navigate the warmed page to `url` and evaluate `extract_js` on it.
        Returns `(final_url, value)` — callers inspect `final_url` to detect
        bot-challenge redirects (e.g. Shein's `/risk/challenge`). `value` is
        None when navigation or extraction fails softly. Applies the
        session's concurrency + politeness delay.

        `block_scripts=True` aborts EXTERNAL script loads for this
        navigation. Sites that inject their SSR state inline (Shein's
        `gbRawData`) still expose it, while the hydration bundles — the
        main crash/OOM risk under --single-process — never run. Inline
        scripts are unaffected by route blocking.
        """
        page = self.page(key)
        final_url = url

        async def _block_scripts(route):
            if route.request.resource_type == "script":
                await route.abort()
            else:
                await route.continue_()

        async with self._sem:
            try:
                if block_scripts:
                    await page.route("**/*", _block_scripts)
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(settle_ms)
                final_url = page.url
                value = await page.evaluate(extract_js)
            except Exception as e:
                logger.warning("goto_and_extract failed for %s: %s", url, e)
                try:
                    final_url = page.url
                except Exception:
                    pass
                return final_url, None
            finally:
                if block_scripts:
                    try:
                        await page.unroute("**/*", _block_scripts)
                    except Exception:
                        pass
                await asyncio.sleep(random.uniform(1.5, 3.0))
        return final_url, value
