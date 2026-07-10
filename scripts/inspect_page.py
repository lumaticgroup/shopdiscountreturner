"""
Diagnostic: warm one storefront and report what the scraper sees. Useful
when categories or product lookups stop working.

Usage:
    python -m scripts.inspect_page --storefront tr
    python -m scripts.inspect_page --storefront gulf --save homepage.html
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper.stores import STOREFRONTS, get_storefront
from scraper.stores.trendyol.browser import PlaywrightSession
from scraper.stores.trendyol.categories import (
    parse_top_level_from_nav_props,
    path_model_for,
)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--storefront", default="tr", choices=list(STOREFRONTS.keys()))
    ap.add_argument("--save", default=None,
                    help="Optional path to save rendered homepage HTML")
    args = ap.parse_args()

    storefront = get_storefront(args.storefront)
    print(f"Warming {storefront.display_name} "
          f"(storefrontId={getattr(storefront, 'storefront_id', '?')}, "
          f"culture={getattr(storefront, 'culture', '?')})")

    async with PlaywrightSession(concurrency=1) as pw:
        nav_props = await pw.warm_storefront(storefront)

        if args.save:
            page = pw._pages[storefront.code]  # diagnostic reaches in intentionally
            html = await page.content()
            Path(args.save).write_text(html, encoding="utf-8")
            print(f"Saved {len(html):,} chars of rendered HTML to {args.save}")

    cats = parse_top_level_from_nav_props(nav_props, storefront)
    print(f"\nDiscovered {len(cats)} top-level categories:")
    for c in cats:
        pm = path_model_for(c) or "?"
        print(f"  {c.name!r:35}  {c.listing_path!r:45}  pathModel={pm}")


if __name__ == "__main__":
    asyncio.run(main())
