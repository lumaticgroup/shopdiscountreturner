"""
Diagnostic: warm one storefront and report what the scraper sees. Useful
when categories or product lookups stop working, and for confirming field
mappings when a store's payload schema drifts.

Dispatches on the storefront's owning store:
  trendyol → nav-props category discovery report
  shein    → first listing page: raw item keys + first mapped products

Usage:
    python -m scripts.inspect_page --storefront trendyol_tr
    python -m scripts.inspect_page --storefront trendyol_uae --save homepage.html
    python -m scripts.inspect_page --storefront shein_tr
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper.stores import STOREFRONTS, store_for_storefront


async def _save_html(page, path: str) -> None:
    html = await page.content()
    Path(path).write_text(html, encoding="utf-8")
    print(f"Saved {len(html):,} chars of rendered HTML to {path}")


async def inspect_trendyol(storefront_code: str, save: str | None) -> None:
    from scraper.stores.trendyol.browser import PlaywrightSession
    from scraper.stores.trendyol.categories import (
        parse_top_level_from_nav_props,
        path_model_for,
    )
    from scraper.stores.trendyol.storefronts import get

    sf = get(storefront_code)
    print(f"Warming {sf.display_name} "
          f"(storefrontId={sf.storefront_id}, culture={sf.culture})")

    async with PlaywrightSession(concurrency=1) as pw:
        nav_props = await pw.warm_storefront(sf)
        if save:
            await _save_html(pw._pages[sf.code], save)

    cats = parse_top_level_from_nav_props(nav_props, sf)
    print(f"\nDiscovered {len(cats)} top-level categories:")
    for c in cats:
        pm = path_model_for(c) or "?"
        print(f"  {c.name!r:35}  {c.listing_path!r:45}  pathModel={pm}")


def _print_sample(products, raw_items) -> None:
    if raw_items:
        print(f"\nFirst raw item keys ({len(raw_items)} items on page 1):")
        print(" ", sorted(raw_items[0].keys()))
        print("\nFirst raw item:")
        print(json.dumps(raw_items[0], indent=2, ensure_ascii=False, default=str)[:3000])
    print(f"\nMapped {len(products)} discounted products; first 5:")
    for p in products[:5]:
        print(f"  -{p.discount_pct}% {p.price} {p.currency}  {p.name[:50]!r}")
        print(f"      url:   {p.url}")
        print(f"      image: {p.image_url}")


async def inspect_shein(storefront_code: str, save: str | None) -> None:
    from scraper.stores._browser import BrowserSession
    from scraper.stores.shein import api
    from scraper.stores.shein.categories import LISTINGS
    from scraper.stores.shein.storefronts import get

    sf = get(storefront_code)
    name, path = LISTINGS[0]
    print(f"Warming {sf.display_name} at {sf.base_url}; probing {name} ({path})")

    async with BrowserSession(concurrency=1) as session:
        await session.warm(sf.code, sf.base_url + "/", cookies=sf.cookies())
        await session.add_cookies(sf.code, sf.cookies())
        final_url, payload = await session.goto_and_extract(
            sf.code, sf.base_url + path, api.GOODS_JS,
        )
        challenged = "/risk/challenge" in final_url or "captcha" in final_url
        print(f"Landed on {final_url}"
              + ("  ⚠ RISK CHALLENGE" if challenged else ""))
        if save:
            await _save_html(session.page(sf.code), save)

    if isinstance(payload, dict):
        print(f"goods array not found; gbRawData keys: {payload.get('__keys')}")
        return
    raw_items = [x for x in (payload or []) if isinstance(x, dict)]
    products = [p for p in (api._to_product(i, sf) for i in raw_items) if p]
    _print_sample(products, raw_items)


_INSPECTORS = {
    "trendyol": inspect_trendyol,
    "shein": inspect_shein,
}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--storefront", default="trendyol_tr",
                    choices=list(STOREFRONTS.keys()))
    ap.add_argument("--save", default=None,
                    help="Optional path to save rendered page HTML")
    args = ap.parse_args()

    store = store_for_storefront(args.storefront)
    inspector = _INSPECTORS.get(store.code)
    if inspector is None:
        print(f"No inspector for store {store.code!r} "
              f"(dynamic stores can be probed with curl directly)")
        return
    await inspector(args.storefront, args.save)


if __name__ == "__main__":
    asyncio.run(main())
