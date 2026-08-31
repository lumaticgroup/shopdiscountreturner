"""
Telegram bot. Owns:
  - command handlers (/start /storefront /categories /top /outlet /search
    /subscribe /unsubscribe /discounts /publish /admin /addstore /delstore /sample_store /admin_login)
  - inline welcome menu shown to users (mirrors the commands as buttons)
  - in-bot admin panel (dynamic store CRUD, scraper reload, channel publishing)
  - inline-button callbacks (menu, category tree, pagination, storefront,
    publishing mode, admin store management)
  - daily digest job (9am server local time by default)
  - auto-publish job: every AUTO_PUBLISH_INTERVAL_MINUTES it refreshes
    every storefront and drains the channel queue, but only when the admin
    has set publish_mode='auto'
  - a single scrape lock + short cache so concurrent /discounts calls don't
    stampede a fresh refresh
"""

import asyncio
import json
import logging
import time
from datetime import time as dt_time
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import channel
import config
import db
from formatters import (
    escape_md_v2,
    format_product_list,
)
from scraper import scrape_storefront
from scraper.stores import (
    LEGACY_STOREFRONT_CODES,
    STOREFRONTS,
    get_storefront,
    refresh_dynamic_stores,
    store_for_storefront,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("trendyol_bot")

_scrape_lock = asyncio.Lock()
_last_scrape_at: dict[str, float] = {}

# Serialises background channel drains: at CHANNEL_POST_RATE_PER_MIN a big
# backlog takes minutes, and a second scrape finishing mid-drain must not
# start a competing loop over the same candidates.
_channel_lock = asyncio.Lock()


def _spawn_channel_firehose(bot) -> None:
    """Drain the channel queue in the background only when auto-publishing is enabled."""
    if _publish_mode() != "auto":
        return
    async def _run():
        async with _channel_lock:
            try:
                await channel.post_qualifying_deals(bot)
            except Exception:
                logger.exception("Channel firehose failed")
    asyncio.get_running_loop().create_task(_run())



# ---------- storefront + role helpers ----------

def _user_storefront(chat_id: int) -> str:
    try:
        pref = db.get_storefront_pref(chat_id)
    except Exception:
        return config.DEFAULT_STOREFRONT
    # Prefs can outlive their storefront (store removed/disabled) — fall
    # back rather than erroring every command for that user.
    if pref not in STOREFRONTS:
        return config.DEFAULT_STOREFRONT
    return pref


def _storefront_display(code: str) -> str:
    try:
        s = get_storefront(code)
        return f"{s.display_name} ({s.currency})"
    except Exception:
        return code


def _is_admin(chat_id: int) -> bool:
    if config.ADMIN_CHAT_IDS and chat_id in config.ADMIN_CHAT_IDS:
        return True
    return db.role_for_chat(chat_id) == "admin"


_PUBLISH_MODES = ("manual", "auto")


def _publish_mode() -> str:
    m = db.get_setting("publish_mode", "manual")
    return m if m in _PUBLISH_MODES else "manual"


def _set_publish_mode(mode: str) -> None:
    if mode not in _PUBLISH_MODES:
        raise ValueError(f"invalid publish mode: {mode!r}")
    db.set_setting("publish_mode", mode)


# ---------- welcome menu (inline keyboard "box") ----------

def _publish_button_label() -> str:
    return (
        "📢 Publishing: Auto"
        if _publish_mode() == "auto"
        else "📢 Publishing: Manual"
    )


def _customer_keyboard() -> InlineKeyboardMarkup:
    """Clean menu for regular customers (deals browsing only)."""
    rows = [
        [
            InlineKeyboardButton("🔥 Top Deals", callback_data="menu:top"),
            InlineKeyboardButton("🍾 Outlet", callback_data="menu:outlet"),
        ],
        [
            InlineKeyboardButton("📁 Categories", callback_data="menu:categories"),
            InlineKeyboardButton("🔍 Search", callback_data="menu:search"),
        ],
        [
            InlineKeyboardButton("🏬 Storefront", callback_data="menu:storefront"),
            InlineKeyboardButton("🔄 Refresh Deals", callback_data="menu:discounts"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def _admin_customer_keyboard() -> InlineKeyboardMarkup:
    """Deals browsing menu with a quick return button for administrators."""
    rows = [
        [
            InlineKeyboardButton("🔥 Top Deals", callback_data="menu:top"),
            InlineKeyboardButton("🍾 Outlet", callback_data="menu:outlet"),
        ],
        [
            InlineKeyboardButton("📁 Categories", callback_data="menu:categories"),
            InlineKeyboardButton("🔍 Search", callback_data="menu:search"),
        ],
        [
            InlineKeyboardButton("🏬 Storefront", callback_data="menu:storefront"),
            InlineKeyboardButton("🔄 Refresh Deals", callback_data="menu:discounts"),
        ],
        [
            InlineKeyboardButton("⚙️ Back to Admin Dashboard", callback_data="admin:menu"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


# ---------- admin in-bot menu builders ----------

def _admin_menu_text_and_markup() -> tuple[str, InlineKeyboardMarkup, ParseMode]:
    stores = db.list_dynamic_stores(enabled_only=False)
    enabled_count = sum(1 for s in stores if s.get("enabled", 1))
    total_count = len(stores)
    mode = _publish_mode()

    text = (
        "👑 *Administrator Dashboard*\n\n"
        f"🏬 Dynamic Stores: *{enabled_count}/{total_count}* active\n"
        f"📢 Channel Publishing: *{mode.title()}*\n\n"
        "Choose an administrative management action below:"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏬 Manage Stores", callback_data="admin:stores"),
            InlineKeyboardButton("➕ Add Store Guide", callback_data="admin:add_guide"),
        ],
        [
            InlineKeyboardButton("⚡ Scrape All", callback_data="admin:scrape_all"),
            InlineKeyboardButton("🔄 Reload Registry", callback_data="admin:reload"),
        ],
        [
            InlineKeyboardButton("📢 Publishing Menu", callback_data="menu:publish"),
            InlineKeyboardButton("📋 Config Templates", callback_data="admin:samples"),
        ],
        [
            InlineKeyboardButton("👁 Customer Deals View", callback_data="admin:customer_view"),
        ],
    ])
    return text, kb, ParseMode.MARKDOWN_V2




def _build_stores_list_keyboard() -> tuple[str, InlineKeyboardMarkup, ParseMode]:
    stores = db.list_dynamic_stores(enabled_only=False)
    if not stores:
        text = (
            "🏬 *Dynamic Stores*\n\n"
            "No dynamic stores registered yet\\.\n"
            "Use `/addstore <JSON>` or check `/sample_store` to add one\\."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Store Guide", callback_data="admin:add_guide")],
            [InlineKeyboardButton("↩ Admin Menu", callback_data="admin:menu")],
        ])
        return text, kb, ParseMode.MARKDOWN_V2

    rows: list[list[InlineKeyboardButton]] = []
    for s in stores:
        status_icon = "🟢" if s.get("enabled", 1) else "🔴"
        label = f"{status_icon} {s['display_name']} ({s['code']})"
        rows.append([InlineKeyboardButton(label, callback_data=f"admin:store:{s['code']}")])

    rows.append([
        InlineKeyboardButton("➕ Add Store Guide", callback_data="admin:add_guide"),
        InlineKeyboardButton("🔄 Reload Registry", callback_data="admin:reload"),
    ])
    rows.append([InlineKeyboardButton("↩ Admin Menu", callback_data="admin:menu")])

    text = "🏬 *Dynamic Stores List*\n\nSelect a store below to view settings or toggle state:"
    return text, InlineKeyboardMarkup(rows), ParseMode.MARKDOWN_V2


def _build_store_detail(code: str) -> tuple[str, InlineKeyboardMarkup, ParseMode]:
    store = db.get_dynamic_store(code)
    if not store:
        return (
            "Store not found\\.",
            InlineKeyboardMarkup([[InlineKeyboardButton("↩ Back to Stores", callback_data="admin:stores")]]),
            ParseMode.MARKDOWN_V2,
        )

    is_enabled = bool(store.get("enabled", 1))
    status_str = "🟢 Enabled" if is_enabled else "🔴 Disabled"
    toggle_label = "🔴 Disable Store" if is_enabled else "🟢 Enable Store"

    text = (
        f"🏬 *Store:* `{escape_md_v2(store['display_name'])}`\n\n"
        f"• *Code:* `{escape_md_v2(store['code'])}`\n"
        f"• *Flow Type:* `{escape_md_v2(store['flow_type'])}`\n"
        f"• *Storefront Code:* `{escape_md_v2(store['storefront_code'])}`\n"
        f"• *Currency:* `{escape_md_v2(store['currency'])}`\n"
        f"• *Base URL:* {escape_md_v2(store['base_url'])}\n"
        f"• *Status:* {status_str}\n"
        f"• *Updated:* `{escape_md_v2(str(store.get('updated_at', '')))}`\n"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(toggle_label, callback_data=f"admin:toggle:{code}"),
            InlineKeyboardButton("🗑 Delete Store", callback_data=f"admin:del:{code}"),
        ],
        [InlineKeyboardButton("↩ Back to Stores", callback_data="admin:stores")],
    ])
    return text, kb, ParseMode.MARKDOWN_V2


# ---------- commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    First-touch entry point.
    - Regular Customers see only the clean customer shopping menu.
    - Administrators see the Administrator Dashboard.
    """
    chat_id = update.effective_chat.id
    sf = _user_storefront(chat_id)
    is_admin = _is_admin(chat_id)
    user_name = update.effective_user.first_name or "Shopper"

    if is_admin:
        text, kb, mode = _admin_menu_text_and_markup()
        await update.effective_message.reply_text(
            f"*Welcome, Administrator {escape_md_v2(user_name)}*\n\n" + text,
            reply_markup=kb,
            parse_mode=mode,
        )
    else:
        await update.effective_message.reply_text(
            f"*Welcome, {escape_md_v2(user_name)}*\n"
            f"Storefront: *{escape_md_v2(_storefront_display(sf))}*\n\n"
            "Discover the latest discounted products and outlet deals below\\.",
            reply_markup=_customer_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2,
        )



async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the in-bot admin control panel. Admins only."""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.effective_message.reply_text(
            "⛔ *Admin only*\\.\n"
            "If you have the admin password, use `/admin_login <password>` to authenticate\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    text, kb, mode = _admin_menu_text_and_markup()
    await update.effective_message.reply_text(
        text, reply_markup=kb, parse_mode=mode,
    )


# ---------- content helpers (shared by commands and menu callbacks) ----------

def _build_top(chat_id: int, outlet_only: bool = False) -> tuple[str, None, Optional[ParseMode]]:
    sf = _user_storefront(chat_id)
    products = db.top_discounts(
        sf, limit=config.DIGEST_TOP_N, outlet_only=outlet_only,
    )
    title = "Outlet / clearance" if outlet_only else f"Top discounts — {_storefront_display(sf)}"
    empty = "No outlet items yet." if outlet_only else "No products yet — run /discounts to fetch the latest."
    if not products:
        return empty, None, None
    return (
        format_product_list(products, title=title),
        None,
        ParseMode.MARKDOWN_V2,
    )


def _build_categories(chat_id: int) -> tuple[str, Optional[InlineKeyboardMarkup], Optional[ParseMode]]:
    sf = _user_storefront(chat_id)
    rows = db.list_top_categories(sf)
    if not rows:
        return (
            "No categories yet — run /discounts to fetch the latest.",
            None, None,
        )
    return (
        f"*Categories* — {escape_md_v2(_storefront_display(sf))}",
        _category_keyboard(rows),
        ParseMode.MARKDOWN_V2,
    )


def _publishing_menu_text_and_markup() -> tuple[str, InlineKeyboardMarkup, ParseMode]:
    mode = _publish_mode()
    interval = config.AUTO_PUBLISH_INTERVAL_MINUTES
    if mode == "auto":
        header = f"🔁 *Automatic* — every {interval} min"
        toggle_row = [InlineKeyboardButton(
            "⏸ Switch to Manual", callback_data="pub:manual",
        )]
    else:
        header = "🖐 *Manual*"
        toggle_row = [InlineKeyboardButton(
            "▶ Switch to Automatic", callback_data="pub:auto",
        )]

    body = (
        f"Publishing mode: {header}\n\n"
        "*Manual* — nothing is posted to the channel until you tap "
        "*Publish now*\\.\n"
        f"*Automatic* — every {interval} minutes the bot refreshes every "
        "storefront and posts the latest qualifying deals to the channel\\.\n\n"
        "Tap *Publish now* to drain the queue immediately in either mode\\."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Publish now", callback_data="pub:now")],
        toggle_row,
        [InlineKeyboardButton("↩ Admin Menu", callback_data="admin:menu")],
    ])
    return body, kb, ParseMode.MARKDOWN_V2


async def _send_publishing_menu(bot, chat_id: int) -> None:
    text, markup, mode = _publishing_menu_text_and_markup()
    await bot.send_message(
        chat_id, text, reply_markup=markup, parse_mode=mode,
    )


def _build_storefront_picker() -> tuple[str, InlineKeyboardMarkup, None]:
    kb = [
        [InlineKeyboardButton(
            f"{s.display_name} ({s.currency})", callback_data=f"sf:{s.code}",
        )]
        for s in STOREFRONTS.values()
    ]
    return "Choose a storefront:", InlineKeyboardMarkup(kb), None


# ---------- commands (thin wrappers around the builders) ----------

async def storefront_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup, _ = _build_storefront_picker()
    await update.effective_message.reply_text(text, reply_markup=markup)


async def categories_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup, mode = _build_categories(update.effective_chat.id)
    await update.effective_message.reply_text(
        text, reply_markup=markup, parse_mode=mode,
    )


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup, mode = _build_top(update.effective_chat.id)
    await update.effective_message.reply_text(
        text, reply_markup=markup, parse_mode=mode,
        disable_web_page_preview=True,
    )


async def outlet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup, mode = _build_top(update.effective_chat.id, outlet_only=True)
    await update.effective_message.reply_text(
        text, reply_markup=markup, parse_mode=mode,
        disable_web_page_preview=True,
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sf = _user_storefront(chat_id)
    term = " ".join(context.args or []).strip()
    if not term:
        await update.effective_message.reply_text(
            "Usage: `/search elbise`", parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    products = db.search_products(sf, term, limit=config.DIGEST_TOP_N)
    await update.effective_message.reply_text(
        format_product_list(
            products, title=f"Search: {term}",
            empty_msg=f"No discounted matches for _{escape_md_v2(term)}_ yet\\.",
        ),
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
    )


async def _execute_discounts(bot, chat_id: int):
    """Core logic to trigger an on-demand scrape and return top results."""
    sf = _user_storefront(chat_id)
    store = store_for_storefront(sf)
    await bot.send_message(
        chat_id=chat_id,
        text=f"Fetching the latest deals from {store.display_name}, "
        "this may take a few minutes…",
    )

    async with _scrape_lock:
        now = time.time()
        last = _last_scrape_at.get(sf, 0)
        if now - last > config.SCRAPE_CACHE_SECONDS:
            try:
                await scrape_storefront(
                    sf,
                    max_pages_per_category=config.MAX_PAGES_PER_CATEGORY,
                    concurrency=config.SCRAPER_CONCURRENCY,
                )
                _last_scrape_at[sf] = time.time()
                _spawn_channel_firehose(bot)
            except Exception as e:
                logger.exception("Scrape failed")
                from scraper.stores.trendyol.browser import ChromiumDied
                if isinstance(e, ChromiumDied):
                    msg = (
                        "Live refresh couldn't finish this time. "
                        "Showing the latest cached results if any exist."
                    )
                else:
                    msg = (
                        "Live refresh failed. "
                        "Showing the latest cached results if any exist."
                    )
                await bot.send_message(chat_id=chat_id, text=msg)

    products = db.top_discounts(sf, limit=config.DIGEST_TOP_N)
    if not products:
        msg = (
            f"No products found with ≥30% discount yet.\n\n"
            f"We found items but they all have smaller discounts. "
            f"Try again later when bigger sales are available."
        )
    else:
        msg = format_product_list(
            products,
            title=f"Top discounts — {_storefront_display(sf)}",
            empty_msg="Nothing to show yet\\.",
        )
    await bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode=ParseMode.MARKDOWN_V2 if products else None,
        disable_web_page_preview=True,
    )


async def discounts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger an on-demand scrape (rate-limited) then show top results."""
    await _execute_discounts(context.bot, update.effective_chat.id)



async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.add_subscriber(update.effective_chat.id)
    await update.effective_message.reply_text(
        f"Subscribed. Daily digest at "
        f"{config.DAILY_DIGEST_HOUR:02d}:{config.DAILY_DIGEST_MINUTE:02d} (server time)."
    )


async def unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.remove_subscriber(update.effective_chat.id)
    await update.effective_message.reply_text("Unsubscribed.")


async def publish_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only. Flush every pending ≥N% product to the channel."""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.effective_message.reply_text("Admin only.")
        return

    if _channel_lock.locked():
        await update.effective_message.reply_text(
            "⏳ Channel broadcast is already in progress. Please wait a moment."
        )
        return

    candidates = db.list_channel_candidates(config.CHANNEL_MIN_DISCOUNT_PCT)
    if not candidates:
        await update.effective_message.reply_text(
            f"ℹ️ No new deals pending to publish.\n\n"
            f"All deals with ≥{config.CHANNEL_MIN_DISCOUNT_PCT}% discount have already been posted to the channel."
        )
        return

    await update.effective_message.reply_text(
        f"📤 Publishing {len(candidates)} qualifying deal(s) (≥{config.CHANNEL_MIN_DISCOUNT_PCT}%) to the channel…"
    )
    try:
        async with _channel_lock:
            posted = await channel.post_qualifying_deals(context.bot)
    except Exception:
        logger.exception("publish failed")
        await update.effective_message.reply_text(
            "Publish failed — please check server logs."
        )
        return
    await update.effective_message.reply_text(
        f"✅ Finished! Posted {posted} product(s) to the channel."
    )



# ---------- Admin Store & Auth Commands ----------

async def addstore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: Add or update a dynamic store via JSON payload."""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    raw_text = update.effective_message.text or ""
    parts = raw_text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await update.effective_message.reply_text(
            "Usage: `/addstore <JSON>`\n\n"
            "Send the complete JSON configuration object for the dynamic store\\.\n"
            "Use `/sample_store` to view templates\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    raw_json = parts[1].strip()
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        await update.effective_message.reply_text(
            f"❌ *Invalid JSON:*\n`{escape_md_v2(str(e))}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if not isinstance(data, dict):
        await update.effective_message.reply_text("❌ Payload must be a JSON object.")
        return

    required = ("code", "display_name", "flow_type", "base_url", "currency", "storefront_code", "config")
    missing = [k for k in required if k not in data]
    if missing:
        await update.effective_message.reply_text(
            f"❌ Missing required keys: `{', '.join(missing)}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    flow_type = data["flow_type"]
    if flow_type not in ("api", "header"):
        await update.effective_message.reply_text(
            "❌ `flow_type` must be either `'api'` or `'header'`.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    config_obj = data["config"]
    if not isinstance(config_obj, dict) or "list_url" not in config_obj or "fields" not in config_obj:
        await update.effective_message.reply_text(
            "❌ `config` must be an object containing `list_url` and `fields`.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    code = str(data["code"]).strip()
    db.upsert_dynamic_store(
        code=code,
        display_name=str(data["display_name"]).strip(),
        flow_type=flow_type,
        base_url=str(data["base_url"]).strip(),
        currency=str(data["currency"]).strip(),
        storefront_code=str(data["storefront_code"]).strip(),
        config_json=json.dumps(config_obj),
        enabled=bool(data.get("enabled", True)),
        created_by=None,
    )
    refresh_dynamic_stores()

    await update.effective_message.reply_text(
        f"✅ Store *{escape_md_v2(code)}* saved and loaded into scraper registry\\!",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def delstore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: Delete a dynamic store by code."""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: `/delstore <store_code>`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    code = args[0].strip()
    if not db.get_dynamic_store(code):
        await update.effective_message.reply_text(f"Store '{code}' not found.")
        return

    db.delete_dynamic_store(code)
    refresh_dynamic_stores()
    await update.effective_message.reply_text(
        f"🗑 Store *{escape_md_v2(code)}* deleted from dynamic stores\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def sample_store_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: Show copy-pasteable store configuration templates."""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    sample_api = {
        "code": "dummyjson",
        "display_name": "DummyJSON",
        "flow_type": "api",
        "base_url": "https://dummyjson.com",
        "currency": "USD",
        "storefront_code": "dummy_us",
        "config": {
            "list_url": "https://dummyjson.com/products?limit=100",
            "response_items_path": "$.products[*]",
            "fields": {
                "product_id": "$.id",
                "name": "$.title",
                "url": "$.thumbnail",
                "image_url": "$.thumbnail",
                "price": "$.price",
                "original_price": "$.price",
                "discount_pct": "$.discountPercentage",
                "brand": "$.brand",
            },
        },
    }

    sample_header = {
        "code": "myshop",
        "display_name": "My Shop",
        "flow_type": "header",
        "base_url": "https://myshop.com",
        "currency": "EUR",
        "storefront_code": "myshop_eu",
        "config": {
            "list_url": "https://myshop.com/api/deals",
            "response_items_path": "$.items[*]",
            "headers": {"User-Agent": "Mozilla/5.0 ...", "Accept-Language": "de-DE"},
            "cookies": {"session": "abc123"},
            "fields": {
                "product_id": "$.sku",
                "name": "$.title",
                "url": "$.url",
                "image_url": "$.image",
                "price": "$.price.now",
                "original_price": "$.price.was",
                "discount_pct": "$.discount",
            },
            "pagination": {"param": "page", "start": 1, "max": 5},
        },
    }

    text = (
        "📋 *Dynamic Store JSON Templates*\n\n"
        "Copy and modify one of these templates, then send with `/addstore <JSON>`:\n\n"
        "*1\\. API Flow (Plain JSON)*:\n"
        f"```json\n{json.dumps(sample_api, indent=2)}\n```\n\n"
        "*2\\. Header Flow (Custom Headers/Cookies)*:\n"
        f"```json\n{json.dumps(sample_header, indent=2)}\n```"
    )
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN_V2,
    )


async def admin_login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim admin status in chat using the configured admin password."""
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: `/admin_login <password>`", parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    password = args[0].strip()
    if config.ADMIN_PASSWORD and password == config.ADMIN_PASSWORD:
        admin_user = db.get_user_by_email("admin@bot.local")
        if not admin_user:
            admin_id = db.create_user("admin@bot.local", "in_bot_admin", role="admin")
        else:
            admin_id = admin_user["id"]
        db.link_chat_to_user(chat_id, admin_id)

        # Instantly transform to Admin View!
        text, kb, mode = _admin_menu_text_and_markup()
        user_name = update.effective_user.first_name or "Admin"
        await update.effective_message.reply_text(
            f"👑 *Admin Mode Activated\\!*\n\n"
            f"*Welcome, Administrator {escape_md_v2(user_name)}*\n\n" + text,
            reply_markup=kb,
            parse_mode=mode,
        )
    else:
        await update.effective_message.reply_text("❌ Incorrect admin password.")



# ---------- callback query (buttons) ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    chat_id = q.message.chat_id

    if data.startswith("menu:"):
        await _handle_menu(context, chat_id, data[len("menu:"):])
        return

    if data.startswith("pub:"):
        await _handle_publish_action(context, q, chat_id, data[len("pub:"):])
        return

    if data.startswith("admin:"):
        await _handle_admin_action(context, q, chat_id, data[len("admin:"):])
        return

    if data.startswith("sf:"):
        code = data[3:]
        code = LEGACY_STOREFRONT_CODES.get(code, code)
        if code in STOREFRONTS:
            db.set_storefront_pref(chat_id, code)
            await q.edit_message_text(
                f"Storefront set to *{escape_md_v2(_storefront_display(code))}*\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        return

    if data.startswith("cat:"):
        parts = data.split(":")
        cat_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 0
        await _render_category(q, cat_id, page)
        return

    if data.startswith("catnav:"):
        target = data[len("catnav:"):]
        sf = _user_storefront(chat_id)
        if target == "root":
            rows = db.list_top_categories(sf)
        else:
            rows = db.list_child_categories(int(target))
        if not rows:
            await q.edit_message_text("No sub-categories.")
            return
        await q.edit_message_text(
            "Choose a category:",
            reply_markup=_category_keyboard(rows),
        )
        return


async def _handle_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int, action: str) -> None:
    bot = context.bot

    if action == "top":
        text, markup, mode = _build_top(chat_id)
    elif action == "outlet":
        text, markup, mode = _build_top(chat_id, outlet_only=True)
    elif action == "categories":
        text, markup, mode = _build_categories(chat_id)
    elif action == "storefront":
        text, markup, mode = _build_storefront_picker()
    elif action == "discounts":
        await _execute_discounts(bot, chat_id)
        return
    elif action == "search":
        await bot.send_message(
            chat_id,
            "Send `/search <term>` — e.g. `/search elbise`\\.",
            parse_mode=ParseMode.MARKDOWN_V2,

        )
        return
    elif action == "publish":
        if not _is_admin(chat_id):
            await bot.send_message(chat_id, "Admin only.")
            return
        await _send_publishing_menu(bot, chat_id)
        return
    else:
        logger.warning("unknown menu action %r", action)
        return

    await bot.send_message(
        chat_id, text,
        reply_markup=markup,
        parse_mode=mode,
        disable_web_page_preview=True,
    )


async def _handle_publish_action(
    context: ContextTypes.DEFAULT_TYPE, query, chat_id: int, action: str,
) -> None:
    bot = context.bot
    if not _is_admin(chat_id):
        await query.answer("Admin only.", show_alert=True)
        return

    if action == "now":
        if _channel_lock.locked():
            await query.answer("⏳ Channel broadcast is already in progress...", show_alert=True)
            return

        candidates = db.list_channel_candidates(config.CHANNEL_MIN_DISCOUNT_PCT)
        if not candidates:
            await query.answer(
                f"ℹ️ No new deals pending. All qualifying deals (≥{config.CHANNEL_MIN_DISCOUNT_PCT}%) are already posted!",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            f"📤 Publishing {len(candidates)} qualifying product(s) (≥{config.CHANNEL_MIN_DISCOUNT_PCT}%) to the channel…"
        )
        try:
            async with _channel_lock:
                posted = await channel.post_qualifying_deals(bot)
        except Exception:
            logger.exception("publish (button) failed")
            await bot.send_message(chat_id, "Publish failed — check server logs.")
            return
        await bot.send_message(
            chat_id, f"✅ Finished! Posted {posted} product(s) to the channel.",
        )
        return


    if action in _PUBLISH_MODES:
        _set_publish_mode(action)
        logger.info("Publish mode set to %s by chat %s", action, chat_id)
        text, markup, mode = _publishing_menu_text_and_markup()
        await query.edit_message_text(
            text, reply_markup=markup, parse_mode=mode,
        )
        return

    logger.warning("unknown publish action %r", action)


async def _handle_admin_action(
    context: ContextTypes.DEFAULT_TYPE, query, chat_id: int, action: str,
) -> None:
    if not _is_admin(chat_id):
        await query.answer("Admin only.", show_alert=True)
        return

    if action == "menu":
        text, kb, mode = _admin_menu_text_and_markup()
        await query.edit_message_text(text, reply_markup=kb, parse_mode=mode)
        return

    if action == "stores":
        text, kb, mode = _build_stores_list_keyboard()
        await query.edit_message_text(text, reply_markup=kb, parse_mode=mode)
        return

    if action == "scrape_all":
        await query.answer("🚀 Starting scrape across all storefronts in background...", show_alert=True)
        async def _run_scrape_all():
            async with _scrape_lock:
                for sf_code in STOREFRONTS.keys():
                    try:
                        await scrape_storefront(
                            sf_code,
                            max_pages_per_category=config.MAX_PAGES_PER_CATEGORY,
                            concurrency=config.SCRAPER_CONCURRENCY,
                        )
                        _last_scrape_at[sf_code] = time.time()
                    except Exception:
                        logger.exception("Admin scrape all failed for %s", sf_code)
            _spawn_channel_firehose(context.bot)
            await context.bot.send_message(chat_id, "✅ Background scrape of all storefronts completed!")
        asyncio.create_task(_run_scrape_all())
        return

    if action.startswith("store:"):
        code = action[len("store:"):]
        text, kb, mode = _build_store_detail(code)
        await query.edit_message_text(text, reply_markup=kb, parse_mode=mode)
        return

    if action.startswith("toggle:"):
        code = action[len("toggle:"):]
        store = db.get_dynamic_store(code)
        if store:
            new_state = not bool(store.get("enabled", 1))
            db.set_dynamic_store_enabled(code, new_state)
            refresh_dynamic_stores()
            text, kb, mode = _build_store_detail(code)
            await query.edit_message_text(text, reply_markup=kb, parse_mode=mode)
        return

    if action.startswith("del:"):
        code = action[len("del:"):]
        db.delete_dynamic_store(code)
        refresh_dynamic_stores()
        await query.answer(f"Store '{code}' deleted.", show_alert=True)
        text, kb, mode = _build_stores_list_keyboard()
        await query.edit_message_text(text, reply_markup=kb, parse_mode=mode)
        return

    if action == "reload":
        loaded = refresh_dynamic_stores()
        await query.answer(f"Registry reloaded: {loaded} store(s) active.", show_alert=True)
        return

    if action == "add_guide":
        guide_text = (
            "➕ *Add Dynamic Store Guide*\n\n"
            "To add a new dynamic store scraper, use the command:\n"
            "`/addstore <JSON>`\n\n"
            "Use `/sample_store` to view JSON templates\\.\n"
            "You can also declare stores in your `.env` file with `STORE_<NAME>`\\."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View Templates", callback_data="admin:samples")],
            [InlineKeyboardButton("↩ Admin Menu", callback_data="admin:menu")],
        ])
        await query.edit_message_text(guide_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN_V2)
        return

    if action == "samples":
        await sample_store_cmd(query, context)
        return

    if action == "customer_view":
        sf = _user_storefront(chat_id)
        text = (
            f"👁 *Customer Deals View Preview*\n"
            f"Storefront: *{escape_md_v2(_storefront_display(sf))}*\n\n"
            "This is the exact deals menu regular customers see in the bot\\."
        )
        await query.edit_message_text(
            text,
            reply_markup=_admin_customer_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if action == "back_main":
        text, kb, mode = _admin_menu_text_and_markup()
        await query.edit_message_text(
            text,
            reply_markup=kb,
            parse_mode=mode,
        )
        return




async def _render_category(query, cat_id: int, page: int):
    offset = page * config.PAGE_SIZE
    products = db.products_in_category(cat_id, offset=offset, limit=config.PAGE_SIZE)
    total = db.count_products_in_category(cat_id)
    cat = db.get_category(cat_id)
    title = cat["breadcrumb"] if cat else "Category"

    if total == 0:
        await query.edit_message_text(
            f"No discounted products in *{escape_md_v2(title)}* yet\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    body = format_product_list(products, title=f"{title}  (page {page + 1})")

    total_pages = (total + config.PAGE_SIZE - 1) // config.PAGE_SIZE
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅ Prev", callback_data=f"cat:{cat_id}:{page - 1}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡", callback_data=f"cat:{cat_id}:{page + 1}"))
    kb_rows = []
    if nav_row:
        kb_rows.append(nav_row)
    kb_rows.append([InlineKeyboardButton("↩ Categories", callback_data="catnav:root")])
    reply_markup = InlineKeyboardMarkup(kb_rows)

    await query.edit_message_text(
        body,
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )


def _category_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []
    for r in rows:
        count = r.get("product_count", 0)
        label = f"{r['name']} ({count})" if count else r["name"]
        kb.append([InlineKeyboardButton(label, callback_data=f"cat:{r['id']}:0")])
    return InlineKeyboardMarkup(kb)


# ---------- auto-publish worker ----------

async def auto_publish_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Repeating job. Scrapes every registered storefront and drains the channel
    queue when publish_mode='auto'. No-ops in manual mode so the admin can
    toggle without touching the scheduler — the next tick just skips.
    """
    if _publish_mode() != "auto":
        return
    logger.info("Auto-publish tick: refreshing all storefronts")
    async with _scrape_lock:
        for code in STOREFRONTS.keys():
            try:
                await scrape_storefront(
                    code,
                    max_pages_per_category=config.MAX_PAGES_PER_CATEGORY,
                    concurrency=config.SCRAPER_CONCURRENCY,
                )
                _last_scrape_at[code] = time.time()
            except Exception:
                logger.exception("Auto-publish scrape failed for %s", code)
    try:
        async with _channel_lock:
            posted = await channel.post_qualifying_deals(context.bot)
        logger.info("Auto-publish tick: posted %d product(s)", posted)
    except Exception:
        logger.exception("Auto-publish channel drain failed")


# ---------- daily digest ----------

async def daily_digest_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running daily digest job")
    for code in STOREFRONTS.keys():
        try:
            await scrape_storefront(
                code,
                max_pages_per_category=config.MAX_PAGES_PER_CATEGORY,
                concurrency=config.SCRAPER_CONCURRENCY,
            )
        except Exception:
            logger.exception("Daily scrape failed for storefront %s", code)

    try:
        async with _channel_lock:
            await channel.post_qualifying_deals(context.bot)
    except Exception:
        logger.exception("Channel firehose failed during daily digest")

    for sub in db.list_subscribers():
        chat_id = sub["chat_id"]
        sf = sub["storefront_pref"]
        products = db.top_discounts(sf, limit=config.DIGEST_TOP_N)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=format_product_list(
                    products,
                    title=f"Daily discounts — {_storefront_display(sf)}",
                    empty_msg="Nothing to show today\\.",
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("Digest send failed for %s", chat_id)
        await asyncio.sleep(0.05)


# ---------- entry ----------

def _build_application() -> Application:
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("storefront", storefront_cmd))
    app.add_handler(CommandHandler("categories", categories_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("outlet", outlet_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("discounts", discounts_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_cmd))
    app.add_handler(CommandHandler("publish", publish_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("admin_login", admin_login_cmd))
    app.add_handler(CommandHandler("addstore", addstore_cmd))
    app.add_handler(CommandHandler("delstore", delstore_cmd))
    app.add_handler(CommandHandler("sample_store", sample_store_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))

    app.job_queue.run_daily(
        daily_digest_job,
        time=dt_time(hour=config.DAILY_DIGEST_HOUR, minute=config.DAILY_DIGEST_MINUTE),
        name="daily_discount_digest",
    )
    app.job_queue.run_repeating(
        auto_publish_job,
        interval=config.AUTO_PUBLISH_INTERVAL_MINUTES * 60,
        first=config.AUTO_PUBLISH_INTERVAL_MINUTES * 60,
        name="auto_publish",
    )
    return app


def _load_env_stores_into_db() -> int:
    """
    Read `STORE_*` env vars, upsert each into `dynamic_stores`, and return
    the count. Called before `refresh_dynamic_stores()` so env-declared
    stores are live from the very first scraper call.
    """
    import env_stores

    specs = env_stores.load_env_stores()
    for spec in specs:
        db.upsert_dynamic_store(
            code=spec["code"],
            display_name=spec["display_name"],
            flow_type=spec["flow_type"],
            base_url=spec["base_url"],
            currency=spec["currency"],
            storefront_code=spec["storefront_code"],
            config_json=spec["config_json"],
            enabled=True,
            created_by=None,
        )
    if specs:
        logger.info("Loaded %s from env", env_stores.summarise(specs))
    return len(specs)


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (see .env.example).")

    db.init_db()
    _load_env_stores_into_db()
    refresh_dynamic_stores()

    logger.info("Initializing Pure Telegram Bot (Polling mode)...")
    app = _build_application()
    app.run_polling()


if __name__ == "__main__":
    main()
