# Trendyol discount bot

Telegram bot that scrapes **every discounted product** across Trendyol's
Turkey (TL) and UAE (AED) storefronts, Shein, and custom dynamic stores,
categorises them, and serves them back through an interactive inline-button UI.
A daily digest fires at 9am server time, and an auto-publishing firehose posts deals to a Telegram channel.

Storage is SQLite. The `db/` package is a facade — ready to swap for Turso (libsql) without touching the bot.

## Architecture

```
shopdiscountreturner/
├── bot.py                    # Pure Telegram bot handlers, admin menu + jobs
├── config.py                 # env vars & admin config
├── formatters.py             # MarkdownV2 rendering + escaping (single choke point)
├── channel.py                # Channel firehose poster (≥35% off)
├── env_stores.py             # Parses STORE_* env dynamic store definitions
├── db/
│   ├── __init__.py           # facade — swap here for Turso later
│   ├── sqlite_backend.py
│   └── schema.sql            # single source of truth
├── scraper/
│   ├── __init__.py           # scrape_storefront(code) entry
│   ├── categories.py         # top-level category discovery
│   ├── models.py             # Product, Category models
│   └── stores/               # Store scrapers (Trendyol, Shein, Generic API/Header)
├── scripts/inspect_page.py   # diagnostic
├── tests/
│   ├── test_formatters.py
│   └── test_shein_api.py
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

# 2. First scrape (writes to discounts.db)
python -m scraper --storefront trendyol_tr
python -m scraper --storefront trendyol_uae

# 3. Start the Telegram Bot
python bot.py
```

## In Telegram

### User Commands

| Command | What it does |
|---|---|
| `/start` | Interactive welcome menu & deal browser |
| `/storefront` | Pick active storefront (Trendyol/Shein × Turkey/UAE) |
| `/categories` | Tap through the category tree; paginated results |
| `/top` | Top 20 biggest discounts today |
| `/outlet` | Outlet / clearance items only |
| `/search <term>` | Match name or brand |
| `/discounts` | Force a fresh scrape now (rate-limited) |
| `/subscribe` / `/unsubscribe` | Daily 9am digest |

### Admin Commands

| Command | What it does |
|---|---|
| `/admin` | Interactive admin panel (Store management, scrapers reload, channel publishing) |
| `/addstore <json>` | Add or update a dynamic store scraper directly in chat |
| `/delstore <code>` | Delete a dynamic store scraper |
| `/sample_store` | Show copy-pasteable JSON configuration templates for API and Header scrapers |
| `/publish` | Flush pending deals (≥35%) to the configured Telegram channel |
| `/admin_login <pwd>` | Authenticate as admin via chat password |

## Dynamic Store Configuration

You can add custom stores without writing new Python code:
1. In chat using `/addstore <JSON>` (see `/sample_store` for templates).
2. In `.env` with variables named `STORE_<NAME>`.

## Tests

```bash
pytest tests/
```
