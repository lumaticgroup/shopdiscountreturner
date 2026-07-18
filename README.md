# Trendyol discount bot

Telegram bot that scrapes **every discounted product** across Trendyol's
Turkey (TL) and UAE (AED) storefronts, categorises them by the site's
own top-level menu, and serves them back through a paginated
inline-button UI. A daily digest fires at 9am server time.

Storage is currently SQLite. The `db/` package is a facade — the next task
in this project swaps SQLite for Turso (libsql) without touching the bot.

## Architecture

```
shopdiscountreturner/
├── bot.py                    # Telegram handlers + daily digest job
├── config.py                 # env vars
├── formatters.py             # MarkdownV2 rendering + escaping (single choke point)
├── db/
│   ├── __init__.py           # facade — swap here for Turso later
│   ├── sqlite_backend.py
│   └── schema.sql            # single source of truth
├── scraper/
│   ├── __init__.py           # scrape_storefront(code) entry
│   ├── storefronts.py        # TR / UAE registry (storefrontId, culture, ...)
│   ├── categories.py         # top-level discovery from __navigation__PROPS
│   ├── trendyol_api.py       # product search via in-browser fetch
│   ├── trendyol_dom.py       # PlaywrightSession — one warmed context per storefront
│   └── models.py
├── scripts/inspect_page.py   # diagnostic
├── tests/
│   └── test_formatters.py
└── requirements.txt
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# edit .env: paste your BotFather token into TELEGRAM_BOT_TOKEN
```

## Running

```bash
# 1. Diagnostic (optional): confirm we can reach a storefront
python -m scripts.inspect_page --storefront trendyol_tr

# 2. First scrape (writes to trendyol.db)
python -m scraper --storefront trendyol_tr
python -m scraper --storefront trendyol_uae

# 3. Bot
python bot.py
```

In Telegram:

| Command | What it does |
|---|---|
| `/start` | Welcome + command list |
| `/storefront` | Pick a storefront (Trendyol/Shein × Turkey/UAE) |
| `/categories` | Tap through the category tree; paginated results |
| `/top` | Top 20 biggest discounts today |
| `/outlet` | Outlet / clearance items only |
| `/search <term>` | Match name or brand |
| `/discounts` | Force a fresh scrape now (rate-limited) |
| `/subscribe` / `/unsubscribe` | Daily 9am digest |

## How the scraper works

Trendyol geo-routes its HTML site by IP: from a European VPS the root
`www.trendyol.com` serves the French `/en` storefront and cookies alone
won't override it. However, Trendyol's product API accepts an explicit
`storefrontId` query param — hitting it with `storefrontId=1` returns
Turkish inventory in TL, `storefrontId=36` returns UAE inventory in AED,
regardless of source IP. Cloudflare gates the API with bot management,
so calls must originate from a real browser context (real TLS fingerprint
+ `__cf_bm` cookie).

Per storefront the flow is:

1. **Warm** a Playwright context: set `storefrontId` / `countryCode` /
   `language` / `platform` cookies, load the storefront homepage, keep
   the page alive. This earns the Cloudflare session cookies **and**
   yields `window["__navigation__PROPS"]` — the JS blob that contains
   the top-level campaign menu (Women / Men / Home / …).
2. Parse top-level categories from that blob. Each entry has a URL like
   `/campaign/list/women/1`; the numeric id is the top-level group,
   consumed by the API as `pathModel=women-x-g1`.
3. For each category, GET
   `apigw.trendyol.com/discovery-sfint-search-service/api/search/products`
   through the warmed page's `fetch()` with
   `?sst=DISCOUNT_APPLIED&pathModel=…&storefrontId=…&culture=…`. Paginate
   with `pi=<n>` until empty or `--max-pages`.
4. Dedup by `(storefront, product_id)`, upsert into `products`, and append
   to `price_history` **only** when price or discount changed since the
   last snapshot (protects Turso's write quota when we migrate).

Politeness:
- One Playwright browser per full run (not per URL — big cost fix vs the
  previous version), one warmed page per storefront.
- 3 concurrent fetches max, 1.5–3s random delay between requests.

## Storefronts

| Code | Store | Country | Currency | Country pin |
|---|---|---|---|---|
| `trendyol_tr` | Trendyol | Turkey | TL | `storefrontId=1`, culture `tr-TR` |
| `trendyol_uae` | Trendyol | UAE | AED | `storefrontId=36`, culture `en-AE` |
| `shein_tr` | Shein | Turkey | TL | host `tr.shein.com`, `currency=TRY` cookie |
| `shein_uae` | Shein | UAE | AED | host `ar.shein.com`, `currency=AED` cookie |

Users pick via `/storefront`; codes are namespaced `<store>_<country>` so
the picker is unambiguous. The pre-multi-store codes `tr` and `gulf` are
rewritten to `trendyol_tr` / `trendyol_uae` automatically: DB rows once on
startup (`db.init_db()`), and `DEFAULT_STOREFRONT` env values on load.
Trendyol's UAE storefront replaces the retired `sa-en` one.

## Tests

```bash
pytest tests/
```

Currently only `formatters.py` has tests. When the scraper API drifts,
the fastest way to catch it is `python -m scripts.inspect_page
--storefront trendyol_tr` — it prints the discovered top-level categories with
the pathModel it will send. Zero categories or empty responses means
Trendyol changed the JS state blob or the API shape.

## Roadmap

- **Next**: migrate `db/sqlite_backend.py` → `db/turso_backend.py` (libsql).
  Schema is already Turso-compatible; the facade in `db/__init__.py` is the
  only file that changes shape.
- Later: proxy support if Trendyol starts rate-limiting on volume.
