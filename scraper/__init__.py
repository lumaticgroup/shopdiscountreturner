"""
Multi-store scraper entry point.

Every store lives under `scraper/stores/<code>/` and registers itself in
the registry at `scraper.stores`. This module is a thin dispatcher: given
a storefront code, look up which store owns it and delegate.
"""
from __future__ import annotations

import asyncio
import logging

from .stores import (
    STOREFRONTS,
    STORES,
    all_storefronts,
    get_storefront,
    store_for_storefront,
)

logger = logging.getLogger("scraper")


async def scrape_storefront(
    storefront_code: str,
    max_pages_per_category: int = 5,
    concurrency: int = 3,
    dry_run: bool = False,
) -> dict:
    """Delegate to the store that owns this storefront."""
    store = store_for_storefront(storefront_code)
    return await store.scrape(
        storefront_code,
        max_pages_per_category=max_pages_per_category,
        concurrency=concurrency,
        dry_run=dry_run,
    )


__all__ = [
    "scrape_storefront",
    "STOREFRONTS",
    "STORES",
    "get_storefront",
    "store_for_storefront",
    "all_storefronts",
]


# CLI entry: `python -m scraper --storefront tr [--dry-run]`
def _cli():
    import argparse
    import logging as _lg

    import db

    _lg.basicConfig(level=_lg.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    ap = argparse.ArgumentParser()
    ap.add_argument("--storefront", default="tr", choices=list(STOREFRONTS.keys()))
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db.init_db()
    result = asyncio.run(scrape_storefront(
        args.storefront,
        max_pages_per_category=args.max_pages,
        concurrency=args.concurrency,
        dry_run=args.dry_run,
    ))
    print(result)


if __name__ == "__main__":
    _cli()
