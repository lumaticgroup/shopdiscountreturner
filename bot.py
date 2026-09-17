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
import hmac
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
import formatters
from formatters import (
    escape_md_v2,
    format_product_list,
)
import locales
from locales import t, DEFAULT_LANGUAGE
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
logger = logging.getLogger("bot")

# Serializes scrape requests per process.
_scrape_lock = asyncio.Lock()
_last_scrape_at: dict[str, float] = {}

# Channel posting lock: avoids concurrent drains.
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



# ---------- storefront, language + role helpers ----------

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


def _user_lang(chat_id: int) -> str:
    try:
        return db.get_language_pref(chat_id) or DEFAULT_LANGUAGE
    except Exception:
        return DEFAULT_LANGUAGE


def _storefront_display(code: str, lang: str = "fa") -> str:
    key = f"sf_{code}"
    if key in locales.STRINGS:
        return t(key, lang)
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


# ---------- keyboards & menus ----------

def _language_keyboard() -> InlineKeyboardMarkup:
    """Language selection keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇮🇷 فارسی", callback_data="lang:fa"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
        ]
    ])


def _customer_keyboard(is_admin: bool = False, lang: str = "fa") -> InlineKeyboardMarkup:
    """Clean menu for regular customers (deals browsing only). Includes admin switch if authenticated."""
    rows = [
        [
            InlineKeyboardButton(t("btn_top_deals", lang), callback_data="menu:top"),
            InlineKeyboardButton(t("btn_outlet", lang), callback_data="menu:outlet"),
        ],
        [
            InlineKeyboardButton(t("btn_categories", lang), callback_data="menu:categories"),
            InlineKeyboardButton(t("btn_search", lang), callback_data="menu:search"),
        ],
        [
            InlineKeyboardButton(t("btn_storefront", lang), callback_data="menu:storefront"),
            InlineKeyboardButton(t("btn_refresh_deals", lang), callback_data="menu:discounts"),
        ],
        [
            InlineKeyboardButton(t("btn_language", lang), callback_data="menu:language"),
        ],
    ]
    if is_admin:
        rows.append([
            InlineKeyboardButton(t("btn_switch_admin", lang), callback_data="admin:menu"),
        ])
    return InlineKeyboardMarkup(rows)


def _admin_customer_keyboard(lang: str = "fa") -> InlineKeyboardMarkup:
    """Deals browsing menu with a quick return button for administrators."""
    rows = [
        [
            InlineKeyboardButton(t("btn_top_deals", lang), callback_data="menu:top"),
            InlineKeyboardButton(t("btn_outlet", lang), callback_data="menu:outlet"),
        ],
        [
            InlineKeyboardButton(t("btn_categories", lang), callback_data="menu:categories"),
            InlineKeyboardButton(t("btn_search", lang), callback_data="menu:search"),
        ],
        [
            InlineKeyboardButton(t("btn_storefront", lang), callback_data="menu:storefront"),
            InlineKeyboardButton(t("btn_refresh_deals", lang), callback_data="menu:discounts"),
        ],
        [
            InlineKeyboardButton(t("btn_language", lang), callback_data="menu:language"),
        ],
        [
            InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin:menu"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


# ---------- admin in-bot menu builders ----------

def _admin_menu_text_and_markup(lang: str = "fa") -> tuple[str, InlineKeyboardMarkup, ParseMode]:
    stores = db.list_dynamic_stores(enabled_only=False)
    enabled_count = sum(1 for s in stores if s.get("enabled", 1))
    total_count = len(stores)
    mode = _publish_mode()

    text = t(
        "admin_dashboard_title",
        lang,
        enabled=enabled_count,
        total=total_count,
        mode=mode.title(),
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t("btn_admin_stores", lang), callback_data="admin:stores"),
            InlineKeyboardButton(t("btn_admin_add_guide", lang), callback_data="admin:add_guide"),
        ],
        [
            InlineKeyboardButton(t("btn_admin_scrape_all", lang), callback_data="admin:scrape_all"),
            InlineKeyboardButton(t("btn_admin_reload", lang), callback_data="admin:reload"),
        ],
        [
            InlineKeyboardButton(t("btn_admin_publishing", lang), callback_data="menu:publish"),
            InlineKeyboardButton(t("btn_admin_templates", lang), callback_data="admin:samples"),
        ],
        [
            InlineKeyboardButton(t("btn_admin_customer_view", lang), callback_data="admin:customer_view"),
        ],
    ])
    return text, kb, ParseMode.MARKDOWN_V2


def _build_stores_list_keyboard(lang: str = "fa") -> tuple[str, InlineKeyboardMarkup, ParseMode]:
    stores = db.list_dynamic_stores(enabled_only=False)
    if not stores:
        text = t("dynamic_stores_empty", lang)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("btn_admin_add_guide", lang), callback_data="admin:add_guide")],
            [InlineKeyboardButton(t("btn_admin_back_menu", lang), callback_data="admin:menu")],
        ])
        return text, kb, ParseMode.MARKDOWN_V2

    rows: list[list[InlineKeyboardButton]] = []
    for s in stores:
        status_icon = "🟢" if s.get("enabled", 1) else "🔴"
        label = f"{status_icon} {s['display_name']} ({s['code']})"
        rows.append([InlineKeyboardButton(label, callback_data=f"admin:store:{s['code']}")])

    rows.append([
        InlineKeyboardButton(t("btn_admin_add_guide", lang), callback_data="admin:add_guide"),
        InlineKeyboardButton(t("btn_admin_reload", lang), callback_data="admin:reload"),
    ])
    rows.append([InlineKeyboardButton(t("btn_admin_back_menu", lang), callback_data="admin:menu")])

    text = t("dynamic_stores_list_title", lang)
    return text, InlineKeyboardMarkup(rows), ParseMode.MARKDOWN_V2



def _build_store_detail(code: str, lang: str = "fa") -> tuple[str, InlineKeyboardMarkup, ParseMode]:
    store = db.get_dynamic_store(code)
    if not store:
        return (
            "Store not found\\.",
            InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_admin_back_stores", lang), callback_data="admin:stores")]]),
            ParseMode.MARKDOWN_V2,
        )

    is_enabled = bool(store.get("enabled", 1))
    status_str = "🟢 Enabled" if is_enabled else "🔴 Disabled"
    toggle_label = t("btn_store_disable", lang) if is_enabled else t("btn_store_enable", lang)

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
            InlineKeyboardButton(t("btn_store_delete", lang), callback_data=f"admin:del:{code}"),
        ],
        [InlineKeyboardButton(t("btn_admin_back_stores", lang), callback_data="admin:stores")],
    ])
    return text, kb, ParseMode.MARKDOWN_V2


# ---------- commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    First-touch entry point.
    If user has not chosen a language yet, prompt for language selection.
    Otherwise display the customer deals browsing menu by default.
    """
    chat_id = update.effective_chat.id
    lang_pref = db.get_language_pref(chat_id)
    if not lang_pref:
        await update.effective_message.reply_text(
            t("lang_picker_prompt", "fa"),
            reply_markup=_language_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    lang = lang_pref
    sf = _user_storefront(chat_id)
    is_admin = _is_admin(chat_id)
    user_name = update.effective_user.first_name or ("خریدار" if lang == "fa" else "Shopper")

    header = t("welcome_header", lang, name=escape_md_v2(user_name))
    sf_line = t("storefront_label", lang, storefront=escape_md_v2(_storefront_display(sf, lang)))
    role_line = f"\n{t('role_admin', lang)}" if is_admin else ""
    subtext = t("welcome_subtext", lang)

    await update.effective_message.reply_text(
        f"{header}\n{sf_line}{role_line}\n\n{subtext}",
        reply_markup=_customer_keyboard(is_admin=is_admin, lang=lang),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def language_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Language switcher command (/language or /lang)."""
    await update.effective_message.reply_text(
        t("lang_picker_prompt", "fa"),
        reply_markup=_language_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the in-bot admin control panel. Admins only."""
    chat_id = update.effective_chat.id
    lang = _user_lang(chat_id)
    if not _is_admin(chat_id):
        await update.effective_message.reply_text(
            t("admin_only_error", lang),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    text, kb, mode = _admin_menu_text_and_markup(lang)
    await update.effective_message.reply_text(
        text, reply_markup=kb, parse_mode=mode,
    )


# ---------- content helpers (shared by commands and menu callbacks) ----------

def _build_top(chat_id: int, outlet_only: bool = False) -> tuple[str, None, Optional[ParseMode]]:
    sf = _user_storefront(chat_id)
    lang = _user_lang(chat_id)
    products = db.top_discounts(
        sf, limit=config.DIGEST_TOP_N, outlet_only=outlet_only,
    )
    sf_name = _storefront_display(sf, lang)
    title = (
        t("outlet_deals_header", lang, storefront=sf_name)
        if outlet_only
        else t("top_deals_header", lang, storefront=sf_name)
    )
    empty = t("no_deals_found", lang)
    if not products:
        return empty, None, None
    return (
        format_product_list(products, title=title, empty_msg=empty, lang=lang),
        None,
        ParseMode.MARKDOWN_V2,
    )


def _category_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(r["name"], callback_data=f"cat:{r['id']}:0")]
        for r in rows
    ])


def _build_categories(chat_id: int) -> tuple[str, Optional[InlineKeyboardMarkup], Optional[ParseMode]]:
    sf = _user_storefront(chat_id)
    lang = _user_lang(chat_id)
    rows = db.list_top_categories(sf)
    if not rows:
        return (
            t("categories_empty", lang),
            None, None,
        )
    title = t("categories_title", lang)
    sf_name = _storefront_display(sf, lang)
    return (
        f"{title} — {escape_md_v2(sf_name)}",
        _category_keyboard(rows),
        ParseMode.MARKDOWN_V2,
    )


def _publishing_menu_text_and_markup(lang: str = "fa") -> tuple[str, InlineKeyboardMarkup, ParseMode]:
    mode = _publish_mode()
    interval = config.AUTO_PUBLISH_INTERVAL_MINUTES
    if mode == "auto":
        header = t("pub_auto_header", lang, interval=interval)
        toggle_row = [InlineKeyboardButton(
            t("btn_pub_switch_manual", lang), callback_data="pub:manual",
        )]
    else:
        header = t("pub_manual_header", lang)
        toggle_row = [InlineKeyboardButton(
            t("btn_pub_switch_auto", lang), callback_data="pub:auto",
        )]

    body = t("pub_menu_body", lang, header=header, interval=interval)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn_pub_now", lang), callback_data="pub:now")],
        toggle_row,
        [InlineKeyboardButton(t("btn_admin_back_menu", lang), callback_data="admin:menu")],
    ])
    return body, kb, ParseMode.MARKDOWN_V2


async def _send_publishing_menu(bot, chat_id: int) -> None:
    lang = _user_lang(chat_id)
    text, markup, mode = _publishing_menu_text_and_markup(lang)
    await bot.send_message(
        chat_id, text, reply_markup=markup, parse_mode=mode,
    )


def _build_storefront_picker(lang: str = "fa") -> tuple[str, InlineKeyboardMarkup, ParseMode]:
    kb = [
        [InlineKeyboardButton(
            _storefront_display(s.code, lang), callback_data=f"sf:{s.code}",
        )]
        for s in STOREFRONTS.values()
    ]
    prompt = t("storefront_picker_prompt", lang)
    return prompt, InlineKeyboardMarkup(kb), ParseMode.MARKDOWN_V2


# ---------- commands (thin wrappers around the builders) ----------

async def storefront_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _user_lang(update.effective_chat.id)
    text, markup, mode = _build_storefront_picker(lang)
    await update.effective_message.reply_text(text, reply_markup=markup, parse_mode=mode)


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
    lang = _user_lang(chat_id)
    sf = _user_storefront(chat_id)
    term = " ".join(context.args or []).strip()
    if not term:
        await update.effective_message.reply_text(
            t("search_usage", lang), parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    products = db.search_products(sf, term, limit=config.DIGEST_TOP_N)
    search_title = t("search_title", lang, term=escape_md_v2(term))
    empty_text = t("search_empty", lang, term=escape_md_v2(term))
    await update.effective_message.reply_text(
        format_product_list(
            products,
            title=search_title,
            empty_msg=empty_text,
            lang=lang,
        ),
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
    )


async def _execute_discounts(bot, chat_id: int):
    """Core logic to trigger an on-demand scrape and return top results."""
    sf = _user_storefront(chat_id)
    lang = _user_lang(chat_id)
    store = store_for_storefront(sf)
    await bot.send_message(
        chat_id=chat_id,
        text=t("fetching_deals", lang, store_name=escape_md_v2(store.display_name)),
        parse_mode=ParseMode.MARKDOWN_V2,
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
            except Exception:
                logger.exception("Scrape failed")
                await bot.send_message(
                    chat_id=chat_id,
                    text=t("scrape_failed", lang),
                    parse_mode=ParseMode.MARKDOWN_V2,
                )

    products = db.top_discounts(sf, limit=config.DIGEST_TOP_N)
    sf_name = _storefront_display(sf, lang)
    if not products:
        msg = t("no_qualifying_discounts", lang, store_name=escape_md_v2(store.display_name))
    else:
        msg = format_product_list(
            products,
            title=t("top_deals_header", lang, storefront=sf_name),
            empty_msg=t("no_deals_found", lang),
            lang=lang,
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
    chat_id = update.effective_chat.id
    lang = _user_lang(chat_id)
    db.add_subscriber(chat_id)
    await update.effective_message.reply_text(
        t("subscribed_success", lang, hour=config.DAILY_DIGEST_HOUR, minute=config.DAILY_DIGEST_MINUTE),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lang = _user_lang(chat_id)
    db.remove_subscriber(chat_id)
    await update.effective_message.reply_text(
        t("unsubscribed_success", lang),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def publish_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only. Flush every pending ≥N% product to the channel."""
    chat_id = update.effective_chat.id
    lang = _user_lang(chat_id)
    if not _is_admin(chat_id):
        await update.effective_message.reply_text(
            t("admin_only_error", lang),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if _channel_lock.locked():
        await update.effective_message.reply_text(
            t("pub_in_progress", lang),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    candidates = db.list_channel_candidates(config.CHANNEL_MIN_DISCOUNT_PCT)
    if not candidates:
        await update.effective_message.reply_text(
            t("pub_no_candidates", lang, pct=config.CHANNEL_MIN_DISCOUNT_PCT),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    await update.effective_message.reply_text(
        t("pub_starting", lang, count=len(candidates), pct=config.CHANNEL_MIN_DISCOUNT_PCT),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    try:
        async with _channel_lock:
            posted = await channel.post_qualifying_deals(context.bot)
    except Exception:
        logger.exception("publish failed")
        await update.effective_message.reply_text(
            t("pub_failed", lang),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    await update.effective_message.reply_text(
        t("pub_finished", lang, count=posted),
        parse_mode=ParseMode.MARKDOWN_V2,
    )




# ---------- Admin Store & Auth Commands ----------

async def addstore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: Add or update a dynamic store via JSON payload."""
    chat_id = update.effective_chat.id
    lang = _user_lang(chat_id)
    if not _is_admin(chat_id):
        await update.effective_message.reply_text(t("admin_only_error", lang), parse_mode=ParseMode.MARKDOWN_V2)
        return

    raw_text = update.effective_message.text or ""
    parts = raw_text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await update.effective_message.reply_text(
            t("addstore_usage", lang),
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
        t("store_saved_success", lang, code=escape_md_v2(code)),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def delstore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: Delete a dynamic store by code."""
    chat_id = update.effective_chat.id
    lang = _user_lang(chat_id)
    if not _is_admin(chat_id):
        await update.effective_message.reply_text(t("admin_only_error", lang), parse_mode=ParseMode.MARKDOWN_V2)
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            t("delstore_usage", lang),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    code = args[0].strip()
    if not db.get_dynamic_store(code):
        await update.effective_message.reply_text(
            t("store_not_found", lang, code=escape_md_v2(code)),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    db.delete_dynamic_store(code)
    refresh_dynamic_stores()
    await update.effective_message.reply_text(
        t("store_deleted_success", lang, code=escape_md_v2(code)),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def sample_store_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: Show copy-pasteable store configuration templates."""
    chat_id = update.effective_chat.id
    lang = _user_lang(chat_id)
    if not _is_admin(chat_id):
        await update.effective_message.reply_text(t("admin_only_error", lang), parse_mode=ParseMode.MARKDOWN_V2)
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


# Brute-force protection for /admin_login
_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_WINDOW_SECONDS = 900  # 15 minutes
_LOGIN_ATTEMPTS: dict[int, list[float]] = {}


def _check_login_rate_limit(chat_id: int) -> tuple[bool, int]:
    """Check if chat_id has exceeded login attempts.
    Returns (is_allowed, remaining_cooldown_seconds).
    """
    now = time.time()
    attempts = _LOGIN_ATTEMPTS.get(chat_id, [])
    valid_attempts = [t for t in attempts if now - t < _LOCKOUT_WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[chat_id] = valid_attempts
    if len(valid_attempts) >= _MAX_LOGIN_ATTEMPTS:
        oldest = valid_attempts[0]
        cooldown = int(_LOCKOUT_WINDOW_SECONDS - (now - oldest))
        return False, max(1, cooldown)
    return True, 0


def _record_failed_login(chat_id: int) -> None:
    now = time.time()
    attempts = _LOGIN_ATTEMPTS.get(chat_id, [])
    attempts.append(now)
    _LOGIN_ATTEMPTS[chat_id] = attempts


def _clear_login_attempts(chat_id: int) -> None:
    _LOGIN_ATTEMPTS.pop(chat_id, None)


async def admin_login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim admin status in chat using the configured admin password."""
    chat_id = update.effective_chat.id
    lang = _user_lang(chat_id)

    # 1. Purge the message containing the password from Telegram chat for security
    try:
        if update.effective_message:
            await update.effective_message.delete()
    except Exception as e:
        logger.debug(f"Could not delete admin login message: {e}")

    # 2. Check brute-force lockout
    allowed, cooldown = _check_login_rate_limit(chat_id)
    if not allowed:
        mins = (cooldown + 59) // 60
        lockout_msg = (
            f"⛔ *Too many failed attempts*\\.\n\n"
            f"Please wait *{mins} {'minutes' if mins > 1 else 'minute'}* before trying `/admin_login` again\\."
            if lang != "fa"
            else
            f"⛔ *تعداد تلاش‌های ناموفق بیش از حد مجاز است*\\.\n\n"
            f"لطفاً *{mins} دقیقه* صبر کنید و مجدداً تلاش نمایید\\."
        )
        await update.effective_chat.send_message(lockout_msg, parse_mode=ParseMode.MARKDOWN_V2)
        return

    args = context.args or []
    if not args:
        await update.effective_chat.send_message(
            t("admin_login_usage", lang), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    password = args[0].strip()

    # 3. Require strong admin password (min 8 chars) if configured
    admin_pw = (config.ADMIN_PASSWORD or "").strip()
    if not admin_pw or len(admin_pw) < 8:
        logger.warning(
            "ADMIN_PASSWORD is empty or too short (< 8 chars). In-chat /admin_login is disabled. Use ADMIN_CHAT_IDS."
        )
        disabled_msg = (
            "⚠️ In\\-chat admin password login is disabled\\.\n"
            "Please configure a strong `ADMIN_PASSWORD` (min 8 chars) or use `ADMIN_CHAT_IDS`\\."
        )
        await update.effective_chat.send_message(disabled_msg, parse_mode=ParseMode.MARKDOWN_V2)
        return

    # 4. Constant-time comparison to prevent timing attacks
    if hmac.compare_digest(password, admin_pw):
        _clear_login_attempts(chat_id)
        admin_user = db.get_user_by_email("admin@bot.local")
        if not admin_user:
            admin_id = db.create_user("admin@bot.local", "in_bot_admin", role="admin")
        else:
            admin_id = admin_user["id"]
        db.link_chat_to_user(chat_id, admin_id)

        # Instantly transform to Admin View!
        text, kb, mode = _admin_menu_text_and_markup(lang)
        user_name = update.effective_user.first_name or ("مدیر" if lang == "fa" else "Admin")
        await update.effective_chat.send_message(
            f"{t('admin_login_success', lang)}\\n\\n"
            f"*Welcome, Administrator {escape_md_v2(user_name)}*\\n\\n" + text,
            reply_markup=kb,
            parse_mode=mode,
        )
    else:
        _record_failed_login(chat_id)
        remaining_attempts = max(0, _MAX_LOGIN_ATTEMPTS - len(_LOGIN_ATTEMPTS.get(chat_id, [])))
        warn = (
            f"\\n\\n_Remaining attempts: {remaining_attempts}_"
            if lang != "fa"
            else f"\\n\\n_تلاش‌های باقیمانده: {remaining_attempts}_"
        )
        await update.effective_chat.send_message(
            f"{t('admin_login_invalid', lang)}{warn if remaining_attempts > 0 else ''}",
            parse_mode=ParseMode.MARKDOWN_V2 if remaining_attempts > 0 else None
        )


async def admin_logout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Demote current chat back to regular customer mode."""
    chat_id = update.effective_chat.id
    lang = _user_lang(chat_id)
    from db.sqlite_backend import _conn
    with _conn() as c:
        c.execute("DELETE FROM chat_links WHERE chat_id = ?", (chat_id,))
    await update.effective_message.reply_text(
        t("admin_logged_out", lang),
        reply_markup=_customer_keyboard(is_admin=False, lang=lang),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------- callback query (buttons) ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    chat_id = q.message.chat_id
    lang = _user_lang(chat_id)

    if data.startswith("lang:"):
        chosen = data[len("lang:"):]
        if chosen in ("fa", "en"):
            db.set_language_pref(chat_id, chosen)
            sf = _user_storefront(chat_id)
            is_admin = _is_admin(chat_id)
            user_name = q.from_user.first_name or ("خریدار" if chosen == "fa" else "Shopper")

            ack_text = t("lang_changed", chosen)
            header = t("welcome_header", chosen, name=escape_md_v2(user_name))
            sf_line = t("storefront_label", chosen, storefront=escape_md_v2(_storefront_display(sf, chosen)))
            role_line = f"\n{t('role_admin', chosen)}" if is_admin else ""
            subtext = t("welcome_subtext", chosen)

            full_text = f"{ack_text}\n\n{header}\n{sf_line}{role_line}\n\n{subtext}"
            await q.edit_message_text(
                full_text,
                reply_markup=_customer_keyboard(is_admin=is_admin, lang=chosen),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        return

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
            msg = t("storefront_switched", lang, storefront=escape_md_v2(_storefront_display(code, lang)))
            await q.edit_message_text(
                msg,
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
            await q.edit_message_text(t("categories_empty", lang))
            return
        await q.edit_message_text(
            t("categories_title", lang),
            reply_markup=_category_keyboard(rows),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return


async def _handle_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int, action: str) -> None:
    bot = context.bot
    lang = _user_lang(chat_id)

    if action == "top":
        text, markup, mode = _build_top(chat_id)
    elif action == "outlet":
        text, markup, mode = _build_top(chat_id, outlet_only=True)
    elif action == "categories":
        text, markup, mode = _build_categories(chat_id)
    elif action == "storefront":
        text, markup, mode = _build_storefront_picker(lang)
    elif action == "language":
        await bot.send_message(
            chat_id,
            t("lang_picker_prompt", "fa"),
            reply_markup=_language_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    elif action == "discounts":
        await _execute_discounts(bot, chat_id)
        return
    elif action == "search":
        await bot.send_message(
            chat_id,
            t("search_usage", lang),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    elif action == "publish":
        if not _is_admin(chat_id):
            await bot.send_message(chat_id, t("admin_only_error", lang), parse_mode=ParseMode.MARKDOWN_V2)
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
    lang = _user_lang(chat_id)
    if not _is_admin(chat_id):
        await query.answer(t("admin_only_error", lang), show_alert=True)
        return

    if action == "now":
        if _channel_lock.locked():
            await query.answer(t("pub_in_progress", lang), show_alert=True)
            return

        candidates = db.list_channel_candidates(config.CHANNEL_MIN_DISCOUNT_PCT)
        if not candidates:
            await query.answer(
                t("pub_no_candidates", lang, pct=config.CHANNEL_MIN_DISCOUNT_PCT),
                show_alert=True,
            )
            return

        await query.edit_message_text(
            t("pub_starting", lang, count=len(candidates), pct=config.CHANNEL_MIN_DISCOUNT_PCT)
        )
        try:
            async with _channel_lock:
                posted = await channel.post_qualifying_deals(bot)
        except Exception:
            logger.exception("publish (button) failed")
            await bot.send_message(chat_id, t("pub_failed", lang))
            return
        await bot.send_message(
            chat_id, t("pub_finished", lang, count=posted),
        )
        return

    if action in _PUBLISH_MODES:
        _set_publish_mode(action)
        logger.info("Publish mode set to %s by chat %s", action, chat_id)
        text, markup, mode = _publishing_menu_text_and_markup(lang)
        await query.edit_message_text(
            text, reply_markup=markup, parse_mode=mode,
        )
        return

    logger.warning("unknown publish action %r", action)


async def _handle_admin_action(
    context: ContextTypes.DEFAULT_TYPE, query, chat_id: int, action: str,
) -> None:
    lang = _user_lang(chat_id)
    if not _is_admin(chat_id):
        await query.answer(t("admin_only_error", lang), show_alert=True)
        return

    if action == "menu":
        text, kb, mode = _admin_menu_text_and_markup(lang)
        await query.edit_message_text(text, reply_markup=kb, parse_mode=mode)
        return

    if action == "stores":
        text, kb, mode = _build_stores_list_keyboard(lang)
        await query.edit_message_text(text, reply_markup=kb, parse_mode=mode)
        return

    if action == "scrape_all":
        await query.answer(t("scrape_all_starting", lang), show_alert=True)
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
            await context.bot.send_message(chat_id, t("scrape_all_done", lang))
        asyncio.create_task(_run_scrape_all())
        return

    if action.startswith("store:"):
        code = action[len("store:"):]
        text, kb, mode = _build_store_detail(code, lang)
        await query.edit_message_text(text, reply_markup=kb, parse_mode=mode)
        return

    if action.startswith("toggle:"):
        code = action[len("toggle:"):]
        store = db.get_dynamic_store(code)
        if store:
            new_state = not bool(store.get("enabled", 1))
            db.set_dynamic_store_enabled(code, new_state)
            refresh_dynamic_stores()
            text, kb, mode = _build_store_detail(code, lang)
            await query.edit_message_text(text, reply_markup=kb, parse_mode=mode)
        return

    if action.startswith("del:"):
        code = action[len("del:"):]
        db.delete_dynamic_store(code)
        refresh_dynamic_stores()
        await query.answer(f"Store '{code}' deleted.", show_alert=True)
        text, kb, mode = _build_stores_list_keyboard(lang)
        await query.edit_message_text(text, reply_markup=kb, parse_mode=mode)
        return

    if action == "reload":
        loaded = refresh_dynamic_stores()
        await query.answer(t("registry_reloaded", lang), show_alert=True)
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
            [InlineKeyboardButton(t("btn_admin_templates", lang), callback_data="admin:samples")],
            [InlineKeyboardButton(t("btn_admin_back_menu", lang), callback_data="admin:menu")],
        ])
        await query.edit_message_text(guide_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN_V2)
        return

    if action == "samples":
        await sample_store_cmd(query, context)
        return

    if action == "customer_view":
        sf = _user_storefront(chat_id)
        text = t("customer_view_preview_text", lang, storefront=escape_md_v2(_storefront_display(sf, lang)))
        await query.edit_message_text(
            text,
            reply_markup=_admin_customer_keyboard(lang=lang),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if action == "back_main":
        text, kb, mode = _admin_menu_text_and_markup(lang)
        await query.edit_message_text(
            text,
            reply_markup=kb,
            parse_mode=mode,
        )
        return


async def _render_category(query, cat_id: int, page: int):
    chat_id = query.message.chat_id
    lang = _user_lang(chat_id)
    offset = page * config.PAGE_SIZE
    products = db.products_in_category(cat_id, offset=offset, limit=config.PAGE_SIZE)
    total = db.count_products_in_category(cat_id)
    cat = db.get_category(cat_id)
    title = cat["breadcrumb"] if cat else ("دسته‌بندی" if lang == "fa" else "Category")

    if total == 0:
        await query.edit_message_text(
            t("category_no_products", lang, title=escape_md_v2(title)),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    page_label = t("page_label", lang, page=page + 1)
    body = format_product_list(products, title=f"{title} ({page_label})", lang=lang)

    total_pages = (total + config.PAGE_SIZE - 1) // config.PAGE_SIZE
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(t("btn_prev", lang), callback_data=f"cat:{cat_id}:{page - 1}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton(t("btn_next", lang), callback_data=f"cat:{cat_id}:{page + 1}"))
    kb_rows = []
    if nav_row:
        kb_rows.append(nav_row)
    kb_rows.append([InlineKeyboardButton(t("btn_back_categories", lang), callback_data="catnav:root")])
    reply_markup = InlineKeyboardMarkup(kb_rows)

    await query.edit_message_text(
        body,
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )


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
        lang = _user_lang(chat_id)
        sf = sub["storefront_pref"]
        products = db.top_discounts(sf, limit=config.DIGEST_TOP_N)
        title = t("daily_digest_header", lang, storefront=_storefront_display(sf, lang))
        empty = t("no_deals_found", lang)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=format_product_list(
                    products,
                    title=title,
                    empty_msg=empty,
                    lang=lang,
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
    app.add_handler(CommandHandler("language", language_cmd))
    app.add_handler(CommandHandler("lang", language_cmd))
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
    app.add_handler(CommandHandler("admin_logout", admin_logout_cmd))
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
