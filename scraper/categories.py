"""
Store-agnostic category-tree persistence helpers, shared by every store
module. Extracted from `scraper/stores/trendyol/categories.py`, which
re-exports them for its existing callers.
"""
from __future__ import annotations

import re

from scraper import models


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


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
