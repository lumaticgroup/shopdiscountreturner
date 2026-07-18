"""
Shein store module. Owns the `shein_tr` (Turkey/TL) and `shein_uae`
(UAE/AED via ar.shein.com + currency cookie) storefronts. Flow class:
risk-control-gated, no callable public API — we warm a Playwright browser
context and extract the SSR goods state from listing-page navigations.
See api.py, categories.py.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

import db
from scraper.base import Storefront, StoreScraper
from scraper.categories import persist_tree

from . import api
from . import categories as cat_mod
from .storefronts import STOREFRONTS as _SHEIN_STOREFRONTS

logger = logging.getLogger("scraper.shein")

# Pause between category listings. The per-page politeness delay (1.5–3s in
# BrowserSession) is not enough at the category boundary: empirically, five
# quick Flash Sale pages followed by back-to-back category navigations put
# the session straight into /risk/action/limit. Categories are where Shein's
# scoring bites, so give each transition a human-scale gap.
_CATEGORY_DELAY_RANGE = (8.0, 15.0)


class SheinStore(StoreScraper):
    code = "shein"
    display_name = "Shein"

    @property
    def storefronts(self) -> list[Storefront]:
        return list(_SHEIN_STOREFRONTS.values())

    async def scrape(
        self,
        storefront_code: str,
        *,
        max_pages_per_category: int,
        concurrency: int,
        dry_run: bool = False,
    ) -> dict:
        sf = _SHEIN_STOREFRONTS[storefront_code]
        logger.info(
            "Starting scrape for %s (%s, currency cookie %s)",
            sf.display_name, sf.base_url, sf.currency_cookie,
        )

        from .._browser import BrowserSession, EdgeBlocked

        all_products: dict[tuple[str, str], object] = {}
        challenged = False
        listings: list = []
        async with BrowserSession(concurrency=concurrency) as session:
            try:
                await session.warm(sf.code, sf.base_url + "/", cookies=sf.cookies())
            except EdgeBlocked as e:
                logger.warning("Shein edge-blocked this network: %s", e)
                return {
                    "categories": 0,
                    "products": 0,
                    "dry_run": dry_run,
                    "error": "edge_blocked",
                }

            # Pinned listings + homepage-discovered department categories.
            # discover_categories keeps pinned entries first, which matters:
            # category pages are gated harder, and one risk challenge burns
            # the context — the guaranteed listings must already be done.
            listings = await cat_mod.discover_categories(session, sf)

            cat_id_map: dict[str, Optional[int]] = {}
            if not dry_run:
                persist_tree(db, listings)
                stored = db.list_top_categories(sf.code)
                cat_id_map = {row["breadcrumb"]: row["id"] for row in stored}

            # Resume support: a rate-limited run rarely gets through all
            # ~28 categories, and restarting from the top every time means
            # the tail never gets scraped. Keep pinned listings first
            # (guaranteed baseline), but rotate the discovered categories to
            # start where the previous run was cut off.
            n_pinned = len(cat_mod.LISTINGS)
            pinned, discovered = listings[:n_pinned], listings[n_pinned:]
            cursor = db.get_scrape_cursor(sf.code) if not dry_run else None
            if cursor:
                idx = next(
                    (i for i, c in enumerate(discovered)
                     if c.breadcrumb == cursor), None,
                )
                if idx:
                    discovered = discovered[idx:] + discovered[:idx]
                    logger.info(
                        "%s: resuming discovered categories at %r "
                        "(%d rotated behind)", sf.code, cursor, idx,
                    )
            ordered = pinned + discovered

            for i, cat in enumerate(ordered):
                if i > 0:
                    # Human-scale pause between listings — see
                    # _CATEGORY_DELAY_RANGE.
                    await asyncio.sleep(random.uniform(*_CATEGORY_DELAY_RANGE))
                try:
                    products = await api.fetch_listing(
                        session, sf, cat.listing_path, cat.breadcrumb,
                        max_pages=max_pages_per_category,
                    )
                except api.RiskChallenged as e:
                    # One challenge means the whole context is burned for
                    # this run — stop here, keep whatever we gathered, and
                    # remember where to pick up next run.
                    logger.warning(
                        "Risk challenge at %r — stopping storefront: %s",
                        cat.breadcrumb, e,
                    )
                    challenged = True
                    # Only discovered categories are worth a cursor —
                    # pinned listings run first every run regardless.
                    if not dry_run and any(
                        c.breadcrumb == cat.breadcrumb for c in discovered
                    ):
                        db.set_scrape_cursor(sf.code, cat.breadcrumb)
                    break

                stored_id = cat_id_map.get(cat.breadcrumb)
                for p in products:
                    p.category_id = stored_id
                    all_products[(p.storefront, p.product_id)] = p

            if not challenged and not dry_run:
                # Full clean pass — next run starts from the top again.
                db.set_scrape_cursor(sf.code, None)

        products_list = list(all_products.values())
        logger.info("Scrape done: %d unique discounted products", len(products_list))

        if not dry_run and products_list:
            db.upsert_products(products_list)

        summary = {
            "categories": len(listings),
            "products": len(products_list),
            "dry_run": dry_run,
        }
        if challenged:
            summary["error"] = "risk_challenge"
        return summary


store = SheinStore()
