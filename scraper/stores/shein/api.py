"""
Shein product-listing path. Shein's risk control captchas plain HTTP
clients (curl on a category page 302s to `/risk/challenge?captcha_type=…`)
and its productList API wants signed params — but listing pages carry the
full goods array in their SSR state (`window.gbRawData`), which a warmed
real-browser navigation gets for free. So: navigate, extract, paginate.

Even a warmed headless Chromium can get challenged; when that happens the
navigation lands on `/risk/challenge` and we raise `RiskChallenged` so the
store can abort that storefront gracefully instead of hammering N more
pages into the same wall.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import config
from scraper import models
from .._browser import BrowserSession
from .storefronts import SheinStorefront as Storefront

logger = logging.getLogger("scraper.shein")

_CDN_FALLBACK = "https://img.ltwebstatic.com"


class RiskChallenged(RuntimeError):
    """Navigation was redirected to Shein's `/risk/challenge` captcha."""


# In-page extractor. Shein reshuffles where the goods array lives (it moved
# from `results.goods` into `bffPageData.products.list` with their BFF
# migration), so instead of hardcoding paths we walk the blob and return the
# first array of goods_id-shaped dicts. On miss, returns the blob's
# top-level keys so the log tells us where the schema moved instead of a
# silent empty scrape.
GOODS_JS = """
() => {
  const raw = window.gbRawData || null;
  if (!raw) return null;
  let found = null;
  const walk = (node, depth) => {
    if (depth > 9 || !node || found) return;
    if (Array.isArray(node)) {
      if (node.length && node[0] && typeof node[0] === 'object' &&
          ('goods_id' in node[0] || 'goodsId' in node[0] || 'goods_sn' in node[0])) {
        found = node;
        return;
      }
      node.slice(0, 3).forEach(c => walk(c, depth + 1));
    } else if (typeof node === 'object') {
      for (const k of Object.keys(node)) walk(node[k], depth + 1);
    }
  };
  walk(raw, 0);
  if (found) {
    try { return JSON.parse(JSON.stringify(found)); } catch (_) { return null; }
  }
  return { __keys: Object.keys(raw) };
}
"""


async def fetch_listing(
    session: BrowserSession,
    sf: Storefront,
    listing_path: str,
    breadcrumb: str,
    max_pages: int = 5,
) -> list[models.ScrapedProduct]:
    """
    Return every discounted product for one listing page, paginating
    `?page=N` until an empty page or `max_pages`.

    Raises `RiskChallenged` if Shein redirects us to its captcha.
    """
    seen: dict[str, models.ScrapedProduct] = {}
    # Raw-id tracking for pagination: a page of sub-threshold discounts is
    # still a fresh page — only stop when Shein starts repeating items.
    seen_raw_ids: set[str] = set()
    base = sf.base_url + listing_path

    for page_idx in range(1, max_pages + 1):
        url = base if page_idx == 1 else f"{base}?page={page_idx}"
        # Shein's edge rewrites the currency cookie by IP — re-pin per nav.
        await session.add_cookies(sf.code, sf.cookies())
        # block_scripts: gbRawData is injected inline; skipping the hydration
        # bundles keeps single-process Chromium from crashing on these pages.
        final_url, payload = await session.goto_and_extract(
            sf.code, url, GOODS_JS, block_scripts=True,
        )

        if "/risk/challenge" in final_url or "captcha" in final_url:
            raise RiskChallenged(f"{sf.code}: challenged at {final_url}")

        if payload is None:
            logger.warning("%s p.%d: no gbRawData at %s", breadcrumb, page_idx, final_url)
            break
        if isinstance(payload, dict):  # the __keys diagnostic shape
            logger.warning(
                "%s p.%d: goods array not found; gbRawData keys: %s",
                breadcrumb, page_idx, payload.get("__keys"),
            )
            break

        items = [x for x in payload if isinstance(x, dict)]
        if not items:
            break

        # One-shot diagnostic: first item's keys, for schema-drift triage.
        if page_idx == 1 and not seen:
            logger.info(
                "Shein item keys sample (%s): %s",
                breadcrumb, sorted(items[0].keys()),
            )

        new_raw = 0
        for item in items:
            pid = item.get("goods_id") or item.get("goodsId") or item.get("goods_sn")
            if pid is None or str(pid) in seen_raw_ids:
                continue
            seen_raw_ids.add(str(pid))
            new_raw += 1
            product = _to_product(item, sf)
            if product:
                seen[product.product_id] = product

        logger.info(
            "%s p.%d: %d items → %d new (%d kept ≥%d%%)",
            breadcrumb, page_idx, len(items), new_raw,
            len(seen), config.MIN_DISCOUNT_PCT,
        )
        if new_raw == 0:
            break

    return list(seen.values())


def _to_product(item: dict, sf: Storefront) -> Optional[models.ScrapedProduct]:
    pid = item.get("goods_id") or item.get("goodsId") or item.get("goods_sn")
    if pid is None:
        return None

    name = item.get("goods_name") or item.get("goodsName")
    if not name:
        return None

    # Flash-sale items carry a deeper `flashPrice` alongside the regular
    # `salePrice` — the flash figures are what the customer pays.
    sale = _amount(item.get("flashPrice")) \
        or _amount(item.get("salePrice") or item.get("sale_price"))
    was = _amount(item.get("retailPrice") or item.get("retail_price"))
    discount = _int(item.get("flashUnitDiscount")) \
        or _int(item.get("unit_discount") or item.get("unitDiscount"))

    if discount is None and sale is not None and was and was > sale:
        discount = round((was - sale) / was * 100)

    # Filter: below the floor → not interesting for the deals channel.
    if not discount or discount <= 0 or discount < config.MIN_DISCOUNT_PCT \
            or sale is None:
        return None
    if was is None or was <= sale:
        was = round(sale / (1 - discount / 100), 2) if discount < 100 else sale

    return models.ScrapedProduct(
        product_id=str(pid),
        storefront=sf.code,
        name=str(name)[:200],
        url=_product_url(item, str(pid), sf),
        brand=item.get("brand_name") or None,
        image_url=_extract_image_url(item),
        price=float(sale),
        original_price=float(was),
        discount_pct=int(discount),
        currency=sf.currency,
        category_id=None,
        is_outlet=False,
    )


def _product_url(item: dict, pid: str, sf: Storefront) -> str:
    for key in ("goods_url", "goodsUrl", "detail_url", "detailUrl", "url"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v if v.startswith("http") else sf.base_url + v
    # Canonical detail-URL shape: /{goods_url_name}-p-{goods_id}-cat-{cat_id}.html
    # (the -cat- segment is optional server-side).
    slug = item.get("goods_url_name") or item.get("goodsUrlName") or "product"
    slug = str(slug).strip().replace(" ", "-")
    cat_id = item.get("cat_id") or item.get("catId")
    tail = f"-cat-{cat_id}" if cat_id else ""
    return f"{sf.base_url}/{slug}-p-{pid}{tail}.html"


def _extract_image_url(item: dict) -> Optional[str]:
    for key in ("goods_img", "goodsImg", "goods_thumb", "original_img", "image"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return _absolutise(v)
    return None


def _absolutise(path_or_url: str) -> str:
    """Shein CDN refs usually arrive protocol-relative (`//img.ltweb…`)."""
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    if path_or_url.startswith("//"):
        return "https:" + path_or_url
    if path_or_url.startswith("/"):
        return _CDN_FALLBACK + path_or_url
    return _CDN_FALLBACK + "/" + path_or_url


def _amount(v: Any) -> Optional[float]:
    """Shein prices arrive as {amount: "12.34", amountWithSymbol: "…"}."""
    if isinstance(v, dict):
        v = v.get("amount", v.get("value"))
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
