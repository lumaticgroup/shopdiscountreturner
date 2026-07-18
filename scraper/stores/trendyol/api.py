"""
Product-search API path. Hits Trendyol's internal search service through
the browser context managed by `PlaywrightSession`, since Cloudflare's
bot check refuses raw HTTP clients.
"""
from __future__ import annotations

import logging
from typing import Optional

import config
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
    # Pagination tracking uses RAW item ids, not kept products: a page where
    # every discount is below MIN_DISCOUNT_PCT is still a page of fresh
    # items, and deeper pages may qualify — only stop when Trendyol starts
    # repeating itself.
    seen_raw_ids: set[str] = set()
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

        # One-shot diagnostic: log the image-related keys of the first item
        # on the very first page we see, so we can confirm which shape
        # Trendyol is returning today. Cheap (once per scrape), invaluable
        # when the CDN schema drifts.
        if page_idx == 1 and items and not seen:
            first = items[0]
            image_keys = {
                k: first.get(k)
                for k in ("imageUrl", "images", "image", "stampsImageUrl",
                          "landingImageUrl", "productImageUrl")
                if k in first
            }
            logger.info(
                "Trendyol item image-keys sample (%s): %s",
                category_breadcrumb, image_keys,
            )

        new_raw = 0
        for item in items:
            pid = item.get("id")
            if pid is None or str(pid) in seen_raw_ids:
                continue
            seen_raw_ids.add(str(pid))
            new_raw += 1
            product = _to_product(item, storefront)
            if product:
                seen[product.product_id] = product

        logger.info(
            "%s p.%d: %d items → %d new (%d kept ≥%d%%)",
            category_breadcrumb, page_idx, len(items), new_raw,
            len(seen), config.MIN_DISCOUNT_PCT,
        )
        if new_raw == 0:
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
    if discount_pct <= 0 or discount_pct < config.MIN_DISCOUNT_PCT:
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
        image_url=_extract_image_url(item),
        price=sale_f,
        original_price=was_f,
        discount_pct=discount_pct,
        currency=sf.currency,
        category_id=None,
        is_outlet=False,
    )


# Trendyol's CDN. Image `path` fields in the API payload are typically
# relative to this host (e.g. "/ty123/product/media/image.jpg").
_CDN_BASE = "https://cdn.dsmcdn.com"


def _extract_image_url(item: dict) -> Optional[str]:
    """
    Trendyol's search API is inconsistent about how it returns product
    images. Seen shapes, in order of frequency:
      - item["imageUrl"] = "https://cdn.dsmcdn.com/..."          (rare/legacy)
      - item["images"]   = ["/ty123/product/media/x.jpg", ...]   (strings)
      - item["images"]   = [{"path": "/ty123/..."}, ...]         (dicts)
      - item["image"]    = "/ty123/..."
      - item["stampsImageUrl"] / "landingImageUrl" / "productImageUrl"
    We probe every shape and return the first absolute URL. Returns None
    if nothing usable is found (channel post then falls back to text-only).
    """
    candidate = item.get("imageUrl")
    if isinstance(candidate, str) and candidate.strip():
        return _absolutise(candidate)

    imgs = item.get("images")
    if isinstance(imgs, list) and imgs:
        first = imgs[0]
        if isinstance(first, str) and first.strip():
            return _absolutise(first)
        if isinstance(first, dict):
            for key in ("path", "url", "src", "imageUrl"):
                v = first.get(key)
                if isinstance(v, str) and v.strip():
                    return _absolutise(v)

    for key in ("image", "stampsImageUrl", "landingImageUrl", "productImageUrl"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return _absolutise(v)

    return None


def _absolutise(path_or_url: str) -> str:
    """Turn `/ty123/product/…` into `https://cdn.dsmcdn.com/ty123/product/…`."""
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    if path_or_url.startswith("//"):
        return "https:" + path_or_url
    if path_or_url.startswith("/"):
        return _CDN_BASE + path_or_url
    return _CDN_BASE + "/" + path_or_url
