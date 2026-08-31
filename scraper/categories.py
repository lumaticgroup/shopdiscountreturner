"""
Store-agnostic category-tree persistence helpers, shared by every store
module. Extracted from `scraper/stores/trendyol/categories.py`, which
re-exports them for its existing callers.
"""

import re
from typing import Optional

from scraper import models


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# Case-insensitive tokens that mark a category as clearance/outlet across
# stores. "outlet" is universal (used in TR, EN, DE, FR listings); the
# others catch stores that call the section something else. Match against
# the display name AND the listing URL — Trendyol tags outlet in the URL
# path, Shein in the section title.
_OUTLET_TOKENS = ("outlet", "clearance", "warehouse")


def is_outlet_category(name: Optional[str], listing_path: Optional[str]) -> bool:
    """True when either the category name or its URL flags it as outlet."""
    hay = f"{name or ''} {listing_path or ''}".lower()
    return any(tok in hay for tok in _OUTLET_TOKENS)


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
