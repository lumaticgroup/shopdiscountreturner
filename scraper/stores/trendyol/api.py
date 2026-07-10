"""
Product-search API path. Hits Trendyol's internal search service through
the browser context managed by `PlaywrightSession`, since Cloudflare's
bot check refuses raw HTTP clients.
"""
from __future__ import annotations

import logging
from typing import Optional

from scraper import models
from .browser import PlaywrightSession
from .storefronts import TrendyolStorefront as Storefront

logger = logging.getLogger("scraper.api")

SEARCH_PATH = "/discovery-sfint-search-service/api/search/products"


async def fetch_category(
    session: PlaywrightSession,
    storefront: Storefront,
    path_model: str,
    category_breadcrumb: str,
    max_pages: int = 5,
    only_discounted: bool = True,
) -> list[models.ScrapedProduct]:
    """
    Return every discounted product for the given `path_model` (e.g.
    "women-x-g1"), paginating until an empty page or `max_pages`.
    """
    seen: dict[str, models.ScrapedProduct] = {}
    for page_idx in range(1, max_pages + 1):
        params = {
            "pathModel": path_model,
            "pi": page_idx,
        }
        if only_discounted:
            params["sst"] = "DISCOUNT_APPLIED"

        payload = await session.fetch_api_json(storefront, SEARCH_PATH, params)
        if not payload:
            break
        items = payload.get("products") or []
        if not items:
            break

        new_count = 0
        for item in items:
            product = _to_product(item, storefront)
            if product and product.product_id not in seen:
                seen[product.product_id] = product
                new_count += 1

        logger.info(
            "%s p.%d: %d items → %d new (total %d)",
            category_breadcrumb, page_idx, len(items), new_count, len(seen),
        )
        if new_count == 0:
            break

    return list(seen.values())


def _to_product(item: dict, sf: Storefront) -> Optional[models.ScrapedProduct]:
    pid = item.get("id")
    if pid is None:
        return None

    # Trendyol's price schema shifts subtly per market:
    #   TR: current == discountedPrice == sale; old == was ("crossed-out")
    #   AE: current == was; discountedPrice == sale; old is 0
    # Rule that works for both: sale = discountedPrice, was = max(old, current).
    # If the "was" isn't higher than the sale, there's no visible discount
    # to surface.
    price = item.get("price") or {}
    sale = price.get("discountedPrice") or price.get("current")
    old = price.get("old") or 0
    current = price.get("current") or 0
    was = max(old, current)

    if not sale or not was or was <= sale:
        return None

    try:
        sale_f = float(sale)
        was_f = float(was)
    except (TypeError, ValueError):
        return None

    discount_pct = round((was_f - sale_f) / was_f * 100)
    if discount_pct <= 0:
        return None

    brand_field = item.get("brand")
    brand = brand_field.get("name") if isinstance(brand_field, dict) else brand_field

    raw_url = item.get("url") or f"/-p-{pid}"
    url = raw_url if raw_url.startswith("http") else sf.base_url + raw_url

    return models.ScrapedProduct(
        product_id=str(pid),
        storefront=sf.code,
        name=(item.get("name") or "Unknown")[:200],
        url=url,
        brand=brand,
        image_url=item.get("imageUrl"),
        price=sale_f,
        original_price=was_f,
        discount_pct=discount_pct,
        currency=sf.currency,
        category_id=None,
        is_outlet=False,
    )
