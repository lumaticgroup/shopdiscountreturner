"""
Generic API-flow store. Fetches a JSON list endpoint with httpx and
extracts products via JSONPath expressions declared in the store's
`config_json`.

Config shape (mirrors `dynamic_stores.config_json`):

    {
      "list_url": "https://api.example.com/search?discount=true",
      "method": "GET",
      "response_items_path": "$.products[*]",
      "fields": {
        "product_id":     "$.id",
        "name":           "$.title",
        "url":            "$.url",
        "image_url":      "$.image",
        "price":          "$.price.current",
        "original_price": "$.price.was",
        "discount_pct":   "$.discountPercent",
        "brand":          "$.brand.name"
      },
      "pagination": {"param": "page", "start": 1, "max": 5}
    }

`headers` and `cookies` keys are only honoured by `GenericHeaderStore` —
the API flow uses httpx defaults.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, ClassVar
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from jsonpath_ng.ext import parse as jp_parse

import db
from scraper.base import Storefront, StoreScraper
from scraper.models import ScrapedProduct

logger = logging.getLogger("scraper.generic")

DEFAULT_PAGE_MAX = 5
DEFAULT_ITEMS_PATH = "$[*]"


class GenericAPIStore(StoreScraper):
    """
    Store scraper for sites that expose a plain JSON list endpoint.

    Constructed dynamically from a `dynamic_stores` DB row — see
    `scraper.stores.refresh_dynamic_stores`.
    """

    flow_type: ClassVar[str] = "api"

    def __init__(
        self,
        *,
        code: str,
        display_name: str,
        storefront_code: str,
        currency: str,
        base_url: str,
        config: dict,
    ) -> None:
        self.code = code
        self.display_name = display_name
        self.base_url = base_url
        self._storefront = Storefront(
            code=storefront_code,
            display_name=display_name,
            currency=currency,
            store_code=code,
        )
        self._config = config or {}
        self._items_expr = jp_parse(
            self._config.get("response_items_path") or DEFAULT_ITEMS_PATH
        )
        self._field_exprs = {
            name: jp_parse(path)
            for name, path in (self._config.get("fields") or {}).items()
        }

    @property
    def storefronts(self) -> list[Storefront]:
        return [self._storefront]

    async def scrape(
        self,
        storefront_code: str,
        *,
        max_pages_per_category: int,
        concurrency: int,
        dry_run: bool = False,
    ) -> dict:
        if storefront_code != self._storefront.code:
            raise ValueError(
                f"{self.code}: unknown storefront {storefront_code!r}"
            )

        products = await self._fetch_all_pages(max_pages_per_category)
        logger.info(
            "%s: parsed %d discounted products", self.code, len(products),
        )

        if not dry_run:
            db.upsert_products(products)

        return {
            "categories": 0,
            "products": len(products),
            "dry_run": dry_run,
        }

    async def _fetch_all_pages(self, max_pages_cap: int) -> list[ScrapedProduct]:
        pagination = self._config.get("pagination") or {}
        param = pagination.get("param")
        start = int(pagination.get("start", 1))
        max_pages = min(int(pagination.get("max", DEFAULT_PAGE_MAX)), max_pages_cap)

        all_products: dict[str, ScrapedProduct] = {}

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for page in range(start, start + max_pages):
                url = self._paginated_url(param, page) if param else self._config["list_url"]
                try:
                    response = await self._fetch(client, url)
                except Exception as e:
                    logger.warning(
                        "%s: fetch failed for %s (%s) — stopping",
                        self.code, url, e,
                    )
                    break

                items = self._extract_items(response)
                if not items:
                    break

                page_new = 0
                for item in items:
                    product = self._to_product(item)
                    if product is None:
                        continue
                    if product.product_id not in all_products:
                        page_new += 1
                    all_products[product.product_id] = product

                if page_new == 0:
                    break

                # Politeness: don't hammer the site.
                await asyncio.sleep(random.uniform(1.0, 2.0))

        return list(all_products.values())

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> Any:
        method = (self._config.get("method") or "GET").upper()
        resp = await client.request(method, url)
        resp.raise_for_status()
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise RuntimeError(f"non-JSON response from {url}: {e}") from e

    def _paginated_url(self, param: str, page: int) -> str:
        base = self._config["list_url"]
        parsed = urlparse(base)
        existing = parsed.query
        new_query = f"{existing}&{param}={page}" if existing else f"{param}={page}"
        return urlunparse(parsed._replace(query=new_query))

    def _extract_items(self, payload: Any) -> list[Any]:
        return [m.value for m in self._items_expr.find(payload)]

    def _to_product(self, item: Any) -> ScrapedProduct | None:
        def get(field: str) -> Any:
            expr = self._field_exprs.get(field)
            if expr is None:
                return None
            matches = expr.find(item)
            return matches[0].value if matches else None

        product_id = get("product_id")
        if product_id is None:
            return None
        product_id = str(product_id)

        name = get("name")
        url = get("url")
        if not name or not url:
            return None

        price = _to_float(get("price"))
        original_price = _to_float(get("original_price"))
        discount_pct = _to_int(get("discount_pct"))
        if discount_pct is None and price is not None and original_price and original_price > price:
            discount_pct = round((original_price - price) / original_price * 100)

        # Filter: no discount → not interesting.
        if not discount_pct or discount_pct <= 0:
            return None

        return ScrapedProduct(
            product_id=product_id,
            storefront=self._storefront.code,
            name=str(name)[:200],
            url=_absolute_url(str(url), self.base_url),
            brand=(str(get("brand")) if get("brand") else None),
            image_url=(str(get("image_url")) if get("image_url") else None),
            price=price,
            original_price=original_price,
            discount_pct=discount_pct,
            currency=self._storefront.currency,
            category_id=None,
            is_outlet=False,
        )


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _absolute_url(href: str, base: str) -> str:
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return base.rstrip("/") + href
    return href
