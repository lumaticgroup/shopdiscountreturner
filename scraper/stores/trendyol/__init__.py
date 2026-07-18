"""
Trendyol store module. Owns the `trendyol_tr` (Turkey/TL) and
`trendyol_uae` (UAE/AED) storefronts. Flow class: Cloudflare-gated, no public API — we warm a
Playwright browser context and call the internal search API through
`page.evaluate(fetch(...))`. See browser.py, categories.py, api.py.
"""
from __future__ import annotations

import logging
from typing import Optional

import db
from scraper.base import Storefront, StoreScraper

from . import api, browser, categories as cat_mod
from .storefronts import LEGACY_CODES as LEGACY_STOREFRONT_CODES  # noqa: F401 — picked up by registry discovery
from .storefronts import STOREFRONTS as _TR_STOREFRONTS

logger = logging.getLogger("scraper.trendyol")


class TrendyolStore(StoreScraper):
    code = "trendyol"
    display_name = "Trendyol"

    @property
    def storefronts(self) -> list[Storefront]:
        return list(_TR_STOREFRONTS.values())

    async def scrape(
        self,
        storefront_code: str,
        *,
        max_pages_per_category: int,
        concurrency: int,
        dry_run: bool = False,
    ) -> dict:
        sf = _TR_STOREFRONTS[storefront_code]
        logger.info(
            "Starting scrape for %s (storefrontId=%d, culture=%s)",
            sf.display_name, sf.storefront_id, sf.culture,
        )

        async with browser.PlaywrightSession(concurrency=concurrency) as pw:
            nav_props = await pw.warm_storefront(sf)
            top_categories = cat_mod.parse_top_level_from_nav_props(nav_props, sf)
            logger.info("Discovered %d top-level categories", len(top_categories))
            if not top_categories:
                return {"categories": 0, "products": 0, "dry_run": dry_run,
                        "error": "no_categories"}

            cat_id_map: dict[str, Optional[int]] = {}
            if not dry_run:
                cat_mod.persist_tree(db, top_categories)
                stored = db.list_top_categories(sf.code)
                cat_id_map = {row["breadcrumb"]: row["id"] for row in stored}

            all_products: dict[tuple[str, str], object] = {}

            for cat in top_categories:
                path_model = cat_mod.path_model_for(cat)
                if not path_model:
                    logger.info(
                        "Skipping category %r — no pathModel derivable from %r",
                        cat.breadcrumb, cat.listing_path,
                    )
                    continue

                products = await api.fetch_category(
                    pw, sf, path_model, cat.breadcrumb,
                    max_pages=max_pages_per_category,
                )
                stored_id = cat_id_map.get(cat.breadcrumb)
                for p in products:
                    p.category_id = stored_id
                    all_products[(p.storefront, p.product_id)] = p

        products_list = list(all_products.values())
        logger.info("Scrape done: %d unique discounted products", len(products_list))

        if not dry_run:
            db.upsert_products(products_list)

        return {
            "categories": len(top_categories),
            "products": len(products_list),
            "dry_run": dry_run,
        }


store = TrendyolStore()
