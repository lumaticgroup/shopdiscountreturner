"""
Category tree discovery.

Trendyol's homepage embeds the mega-menu contents in a JS global
(`window["__navigation__PROPS"]`) rather than serving them as static
`<nav>` links. `PlaywrightSession.warm_storefront()` returns that blob;
we walk it here to produce top-level `ScrapedCategory` nodes.

Deeper depth is grown lazily: when a listing scrape returns a product,
its breadcrumb hints at the full path, and `persist_tree` can back-fill.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper import models
from .storefronts import TrendyolStorefront as Storefront

logger = logging.getLogger("scraper.categories")

# Trendyol embeds top-level category URLs in three different shapes
# depending on the storefront:
#   /campaign/list/women/1        (UAE, FR — "campaign" scheme, slug/id)
#   butik/liste/1/kadin           (TR/AZ — "butik" scheme, id/slug)
#   kadin-giyim-x-g1-c82          (children — already in pathModel form)
# All three yield a numeric group id; the API's `pathModel` uses
# `<slug>-x-g<id>` where the slug prefix is cosmetic.
_URL_ALREADY_PATH_MODEL_RE = re.compile(r"(?:^|/)([a-z0-9-]+-x-g\d+(?:-c\d+)?)$")
_URL_CAMPAIGN_RE = re.compile(r"/?campaign/list/([a-z0-9-]+)/(\d+)")
_URL_BUTIK_RE = re.compile(r"/?butik/liste/(\d+)/([a-z0-9-]+)")


def parse_top_level_from_nav_props(
    nav_props: dict,
    storefront: Storefront,
) -> list[models.ScrapedCategory]:
    """Extract top-level categories from the warmed nav_props blob."""
    entries = nav_props.get("categories") if isinstance(nav_props, dict) else None
    if not entries:
        logger.warning("nav_props has no 'categories' array for %s", storefront.code)
        return []

    out: list[models.ScrapedCategory] = []
    seen_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("title") or entry.get("name") or "").strip()
        url = (entry.get("webUrl") or entry.get("url") or entry.get("link") or "").strip()
        if not name or not url or url in seen_paths:
            continue
        seen_paths.add(url)
        out.append(
            models.ScrapedCategory(
                storefront=storefront.code,
                name=name,
                slug=_slug(name),
                breadcrumb=name,
                depth=0,
                parent_breadcrumb=None,
                listing_path=url,
            )
        )
    return out


def path_model_for(category: models.ScrapedCategory) -> Optional[str]:
    """
    Build the API `pathModel` value from a nav_props URL.
    Accepts the three URL shapes Trendyol uses across storefronts.
    Returns None if the URL isn't a category listing we recognise.
    """
    url = category.listing_path or ""
    m = _URL_ALREADY_PATH_MODEL_RE.search(url)
    if m:
        return m.group(1)
    m = _URL_CAMPAIGN_RE.search(url)
    if m:
        return f"{m.group(1)}-x-g{m.group(2)}"
    m = _URL_BUTIK_RE.search(url)
    if m:
        return f"{m.group(2)}-x-g{m.group(1)}"
    return None


def parse_breadcrumb(breadcrumb_html: str) -> list[str]:
    """Return the sequence of category names from a breadcrumb element."""
    soup = BeautifulSoup(breadcrumb_html, "lxml")
    names: list[str] = []
    for el in soup.select("a, span"):
        t = el.get_text(strip=True)
        if t and t.lower() not in {"home", "anasayfa"}:
            names.append(t)
    dedup: list[str] = []
    for n in names:
        if not dedup or dedup[-1] != n:
            dedup.append(n)
    return dedup


def persist_tree(db, categories: list[models.ScrapedCategory]) -> dict[str, int]:
    """
    Upsert every node and return {breadcrumb: id}.
    Nodes must be in depth order so parent IDs are known.
    """
    ids: dict[str, int] = {}
    for cat in sorted(categories, key=lambda c: c.depth):
        parent_id = ids.get(cat.parent_breadcrumb) if cat.parent_breadcrumb else None
        cid = db.upsert_category(
            storefront=cat.storefront,
            name=cat.name,
            slug=cat.slug,
            breadcrumb=cat.breadcrumb,
            depth=cat.depth,
            parent_id=parent_id,
        )
        ids[cat.breadcrumb] = cid
    return ids


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _relative(href: str, base: str) -> str:
    if href.startswith("http"):
        return href.split(base, 1)[-1] if href.startswith(base) else href
    return href
