import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", "trendyol.db")

# Daily digest time (server local time)
DAILY_DIGEST_HOUR = int(os.environ.get("DAILY_DIGEST_HOUR", "9"))
DAILY_DIGEST_MINUTE = int(os.environ.get("DAILY_DIGEST_MINUTE", "0"))

# Scrape scope. Higher = more coverage, more time.
MAX_PAGES_PER_CATEGORY = int(os.environ.get("MAX_PAGES_PER_CATEGORY", "5"))
SCRAPER_CONCURRENCY = int(os.environ.get("SCRAPER_CONCURRENCY", "3"))

# Scrape-level discount floor: products below this % never enter the DB,
# for every store (static and dynamic). This is what makes the bot a
# "deals" bot rather than a catalog mirror. CHANNEL_MIN_DISCOUNT_PCT
# below filters again at posting time, so the channel threshold can be
# raised above this floor without re-scraping.
MIN_DISCOUNT_PCT = int(os.environ.get("MIN_DISCOUNT_PCT", "35"))

# Digest / pagination sizing
DIGEST_TOP_N = int(os.environ.get("DIGEST_TOP_N", "20"))
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "10"))

# Default storefront for new users. Legacy "tr"/"gulf" values from old
# .env files are mapped to the namespaced codes.
_LEGACY_STOREFRONTS = {"tr": "trendyol_tr", "gulf": "trendyol_uae"}
DEFAULT_STOREFRONT = os.environ.get("DEFAULT_STOREFRONT", "trendyol_tr")
DEFAULT_STOREFRONT = _LEGACY_STOREFRONTS.get(DEFAULT_STOREFRONT, DEFAULT_STOREFRONT)

# Minimum seconds between on-demand scrapes triggered by /discounts
SCRAPE_CACHE_SECONDS = int(os.environ.get("SCRAPE_CACHE_SECONDS", "600"))

# ≥N% off Telegram channel firehose. Empty channel id disables posting.
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
CHANNEL_MIN_DISCOUNT_PCT = int(os.environ.get("CHANNEL_MIN_DISCOUNT_PCT", "35"))
CHANNEL_POST_RATE_PER_MIN = int(os.environ.get("CHANNEL_POST_RATE_PER_MIN", "20"))

# Mini App (Telegram WebApp) — hosted by FastAPI in this same process.
# Telegram requires HTTPS: use ngrok for dev, Caddy/nginx for prod.
WEBAPP_BASE_URL = os.environ.get("WEBAPP_BASE_URL", "").strip()
WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8000"))

# JWT signing key. Rotate to invalidate all outstanding sessions.
WEBAPP_SECRET = os.environ.get("WEBAPP_SECRET", "")
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "168"))  # 7 days

# First-admin seed. When these are set and there's no admin in the DB, the
# bot seeds this user once at startup. Leave blank to skip.
BOOTSTRAP_ADMIN_EMAIL = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip()
BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
