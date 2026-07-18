"""
Shein listing entry points, two tiers:

1. Pinned listings — `/flash-sale.html`, `/daily-new.html`. Exist on every
   regional host and, empirically, almost never trip risk control. These
   are the guaranteed baseline and are always scraped first.
2. Discovered categories — the homepage (which also doesn't challenge)
   renders a category strip of `/<Name>-c-<id>.html` links covering the
   full department tree (Women/Men/Kids/Shoes/Electronics/…). We harvest
   those per run instead of hardcoding IDs, because Shein's category IDs
   differ per regional host and shift with merchandising campaigns.

Category PAGES are gated harder than pinned listings — one challenge burns
the browser context for the rest of the run, so the store scrapes tier 1
before attempting tier 2 and keeps whatever it gathered.

Discount filtering happens in `api._to_product` (items below
MIN_DISCOUNT_PCT are dropped).
"""
from __future__ import annotations

import logging

from scraper import models
from scraper.categories import slugify

from .._browser import BrowserSession
from .storefronts import SheinStorefront

logger = logging.getLogger("scraper.shein")

# (display name, path) — identical across regional hosts. Tier 1: always
# scraped, and first, so a category-page challenge can't cost us these.
LISTINGS: list[tuple[str, str]] = [
    ("Flash Sale", "/flash-sale.html"),
    ("Daily New", "/daily-new.html"),
]

# Harvest `/<Name>-c-<id>.html` links from the homepage's category strip.
# Name comes from the URL slug — the anchor text is often an <img> alt or
# localised label, while the slug is stable English.
DISCOVER_JS = """
() => {
  const seen = new Set();
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/^\\/?([A-Za-z0-9,-]+)-c-(\\d+)\\.html/);
    if (!m) continue;
    const path = '/' + m[1] + '-c-' + m[2] + '.html';
    if (seen.has(path)) continue;
    seen.add(path);
    const name = m[1].replace(/,/g, '').replace(/-+/g, ' ').trim();
    out.push({ name, path });
  }
  return out;
}
"""


def top_level_categories(sf: SheinStorefront) -> list[models.ScrapedCategory]:
    """Tier-1 pinned listings only — the no-network fallback."""
    return [
        _category(sf, name, path)
        for name, path in LISTINGS
    ]


async def discover_categories(
    session: BrowserSession, sf: SheinStorefront,
) -> list[models.ScrapedCategory]:
    """
    Pinned listings + every category link the homepage renders. Best-effort:
    on any discovery failure we fall back to the pinned listings alone.
    """
    cats = top_level_categories(sf)
    known_paths = {c.listing_path for c in cats}

    try:
        final_url, links = await session.goto_and_extract(
            sf.code, sf.base_url + "/", DISCOVER_JS, settle_ms=6000,
        )
    except Exception:
        logger.exception("%s: category discovery crashed — pinned only", sf.code)
        return cats
    if not links or "/risk/" in final_url:
        logger.warning(
            "%s: no category links discovered (landed %s) — pinned only",
            sf.code, final_url,
        )
        return cats

    for link in links:
        name, path = link.get("name"), link.get("path")
        if not name or not path or path in known_paths:
            continue
        known_paths.add(path)
        cats.append(_category(sf, name, path))

    logger.info(
        "%s: %d listings (%d pinned + %d discovered categories)",
        sf.code, len(cats), len(LISTINGS), len(cats) - len(LISTINGS),
    )
    return cats


def _category(
    sf: SheinStorefront, name: str, path: str,
) -> models.ScrapedCategory:
    return models.ScrapedCategory(
        storefront=sf.code,
        name=name,
        slug=slugify(name),
        breadcrumb=name,
        depth=0,
        parent_breadcrumb=None,
        listing_path=path,
    )
