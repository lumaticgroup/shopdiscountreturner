"""
Shein store module. Owns the `shein_tr` (Turkey/TL) and `shein_uae`
(UAE/AED via ar.shein.com + currency cookie) storefronts. Flow class:
risk-control-gated, no callable public API — we warm a Playwright browser
context and extract the SSR goods state from listing-page navigations.
See api.py, categories.py.
"""
from __future__ import annotations

import logging
from typing import Optional

import db
from scraper.base import Storefront, StoreScraper
from scraper.categories import persist_tree

from . import api
from . import categories as cat_mod
from .storefronts import STOREFRONTS as _SHEIN_STOREFRONTS

logger = logging.getLogger("scraper.shein")


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

            for cat in listings:
                try:
                    products = await api.fetch_listing(
                        session, sf, cat.listing_path, cat.breadcrumb,
                        max_pages=max_pages_per_category,
                    )
                except api.RiskChallenged as e:
                    # One challenge means the whole context is burned for
                    # this run — stop here and keep whatever we gathered
                    # from the listings before it.
                    logger.warning(
                        "Risk challenge at %r — stopping storefront: %s",
                        cat.breadcrumb, e,
                    )
                    challenged = True
                    break

                stored_id = cat_id_map.get(cat.breadcrumb)
                for p in products:
                    p.category_id = stored_id
                    all_products[(p.storefront, p.product_id)] = p

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
