"""
Telegram bot. Owns:
  - command handlers (/start /storefront /categories /top /outlet /search
    /subscribe /unsubscribe /discounts /publish /admin)
  - inline welcome menu shown after login (mirrors the commands as buttons)
  - inline-button callbacks (menu, category tree, pagination, storefront)
  - daily digest job (9am server local time by default)
  - a single scrape lock + short cache so concurrent /discounts calls don't
    stampede a fresh scrape
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import time as dt_time

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    WebAppInfo,
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
from scraper.stores import STOREFRONTS, get_storefront, store_for_storefront

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("trendyol_bot")

_scrape_lock = asyncio.Lock()
_last_scrape_at: dict[str, float] = {}


# ---------- storefront + role helpers ----------

def _user_storefront(chat_id: int) -> str:
    try:
        return db.get_storefront_pref(chat_id)
    except Exception:
        return config.DEFAULT_STOREFRONT


def _storefront_display(code: str) -> str:
    try:
        s = get_storefront(code)
        return f"{s.display_name} ({s.currency})"
    except Exception:
        return code


def _is_admin(chat_id: int) -> bool:
    return db.role_for_chat(chat_id) == "admin"


def _webapp_url(path: str = "/") -> str:
    base = (config.WEBAPP_BASE_URL or "").rstrip("/")
    if not base:
        return ""
    return base + path


# ---------- welcome menu (inline keyboard "box") ----------

def _welcome_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("🔥 Top", callback_data="menu:top"),
            InlineKeyboardButton("🍾 Outlet", callback_data="menu:outlet"),
        ],
        [
            InlineKeyboardButton("📁 Categories", callback_data="menu:categories"),
            InlineKeyboardButton("🔍 Search", callback_data="menu:search"),
        ],
        [
            InlineKeyboardButton("🏬 Storefront", callback_data="menu:storefront"),
            InlineKeyboardButton("📩 Subscribe", callback_data="menu:subscribe"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("📢 Publish", callback_data="menu:publish")])
        admin_url = _webapp_url("/admin.html")
        if admin_url:
            rows.append([InlineKeyboardButton(
                "⚙️ Add store", web_app=WebAppInfo(url=admin_url),
            )])
    return InlineKeyboardMarkup(rows)


def _welcome_text(email: str, storefront_display: str, is_admin: bool) -> str:
    role_line = "\n_Role: admin_" if is_admin else ""
    return (
        f"*Welcome, {escape_md_v2(email)}*\n"
        f"Storefront: *{escape_md_v2(storefront_display)}*"
        f"{role_line}\n\n"
        "Pick an option below\\."
    )


# ---------- commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    First-touch entry point.

    - Not linked yet → WebApp button opening the Mini App for login/signup.
      After the Mini App links the chat, subsequent /start calls hit the
      "already linked" branch.
    - Already linked → inline-keyboard "box" with every action as a button.
    """
    chat_id = update.effective_chat.id
    user = db.user_for_chat(chat_id)

    if user is None:
        url = _webapp_url("/")
        if not url:
            await update.effective_message.reply_text(
                "The Mini App isn't configured yet. Ask the admin to set "
                "WEBAPP_BASE_URL in .env."
            )
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Open app", web_app=WebAppInfo(url=url)),
        ]])
        await update.effective_message.reply_text(
            "Welcome to the Discount Bot.\n\n"
            "Tap the button below to sign up or log in. Once you're in, "
            "the bot will unlock browsing, search, and daily deals.",
            reply_markup=kb,
        )
        return

    sf = _user_storefront(chat_id)
    is_admin = user.get("role") == "admin"
    await update.effective_message.reply_text(
        _welcome_text(user["email"], _storefront_display(sf), is_admin),
        reply_markup=_welcome_keyboard(is_admin),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a WebApp button to the admin panel. Admins only."""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.effective_message.reply_text(
            "Admin only. Log in as an admin via /start."
        )
        return
    url = _webapp_url("/admin.html")
    if not url:
        await update.effective_message.reply_text(
            "WEBAPP_BASE_URL isn't set in .env."
        )
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Manage stores", web_app=WebAppInfo(url=url)),
    ]])
    await update.effective_message.reply_text(
        "Open the admin panel:", reply_markup=kb,
    )


# ---------- content helpers (shared by commands and menu callbacks) ----------
#
# Each returns (text, reply_markup, parse_mode). Callers decide whether to
# `reply_text` (command) or `send_message` (menu tap).

def _build_top(chat_id: int, outlet_only: bool = False) -> tuple[str, None, ParseMode | None]:
    sf = _user_storefront(chat_id)
    products = db.top_discounts(
        sf, limit=config.DIGEST_TOP_N, outlet_only=outlet_only,
    )
    title = "Outlet / clearance" if outlet_only else f"Top discounts — {_storefront_display(sf)}"
    empty = "No outlet items yet." if outlet_only else "No products yet — run /discounts to scrape now."
    if not products:
        return empty, None, None
    return (
        format_product_list(products, title=title),
        None,
        ParseMode.MARKDOWN_V2,
    )


def _build_categories(chat_id: int) -> tuple[str, InlineKeyboardMarkup | None, ParseMode | None]:
    sf = _user_storefront(chat_id)
    rows = db.list_top_categories(sf)
    if not rows:
        return (
            "No categories cached yet — run a scrape first with /discounts.",
            None, None,
        )
    return (
        f"*Categories* — {escape_md_v2(_storefront_display(sf))}",
        _category_keyboard(rows),
        ParseMode.MARKDOWN_V2,
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


async def discounts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger an on-demand scrape (rate-limited) then show top results."""
    chat_id = update.effective_chat.id
    sf = _user_storefront(chat_id)
    store = store_for_storefront(sf)
    await update.effective_message.reply_text(
        f"Scraping {store.display_name}, this may take a few minutes…"
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
                try:
                    await channel.post_qualifying_deals(context.bot)
                except Exception:
                    logger.exception("Channel firehose failed")
            except Exception:
                logger.exception("Scrape failed")
                await update.effective_message.reply_text(
                    "Scrape failed — check server logs. Showing whatever is already cached."
                )

    products = db.top_discounts(sf, limit=config.DIGEST_TOP_N)
    await update.effective_message.reply_text(
        format_product_list(
            products,
            title=f"Top discounts — {_storefront_display(sf)}",
            empty_msg="Nothing to show yet\\.",
        ),
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
    )


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
    await update.effective_message.reply_text("Publishing to channel…")
    try:
        posted = await channel.post_qualifying_deals(context.bot)
    except Exception:
        logger.exception("publish failed")
        await update.effective_message.reply_text(
            "Publish failed — check server logs."
        )
        return
    await update.effective_message.reply_text(
        f"Posted {posted} product(s) to the channel."
    )


# ---------- callback query (buttons) ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    chat_id = q.message.chat_id

    if data.startswith("menu:"):
        await _handle_menu(context, chat_id, data[len("menu:"):])
        return

    if data.startswith("sf:"):
        code = data[3:]
        if code in STOREFRONTS:
            db.set_storefront_pref(chat_id, code)
            await q.edit_message_text(
                f"Storefront set to *{escape_md_v2(_storefront_display(code))}*\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        return

    if data.startswith("cat:"):
        # cat:<category_id>:<page>
        parts = data.split(":")
        cat_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 0
        await _render_category(q, cat_id, page)
        return

    if data.startswith("catnav:"):
        # catnav:<parent_id or "root">
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
    elif action == "search":
        await bot.send_message(
            chat_id,
            "Send `/search <term>` — e.g. `/search elbise`.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    elif action == "subscribe":
        db.add_subscriber(chat_id)
        await bot.send_message(
            chat_id,
            f"Subscribed. Daily digest at "
            f"{config.DAILY_DIGEST_HOUR:02d}:{config.DAILY_DIGEST_MINUTE:02d} (server time).",
        )
        return
    elif action == "publish":
        if not _is_admin(chat_id):
            await bot.send_message(chat_id, "Admin only.")
            return
        await bot.send_message(chat_id, "Publishing to channel…")
        try:
            posted = await channel.post_qualifying_deals(bot)
        except Exception:
            logger.exception("publish (menu) failed")
            await bot.send_message(chat_id, "Publish failed — check server logs.")
            return
        await bot.send_message(chat_id, f"Posted {posted} product(s) to the channel.")
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


# ---------- daily digest ----------

async def daily_digest_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running daily digest job")
    # Refresh every registered storefront (whoever's subscribed will get their preferred one)
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
        # Telegram flood limit: don't exceed 30 msg/s
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
    app.add_handler(CallbackQueryHandler(on_callback))

    app.job_queue.run_daily(
        daily_digest_job,
        time=dt_time(hour=config.DAILY_DIGEST_HOUR, minute=config.DAILY_DIGEST_MINUTE),
        name="daily_discount_digest",
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


async def _run_bot_and_webapp() -> None:
    """
    Run PTB polling and the FastAPI (uvicorn) server together on one asyncio
    loop. Either failing takes the other down cleanly.
    """
    import uvicorn
    from scripts.bootstrap_admin import bootstrap_admin_if_needed
    from scraper.stores import refresh_dynamic_stores
    from web.app import create_app

    db.init_db()
    result = bootstrap_admin_if_needed()
    if result == "created":
        logger.info("Seeded first admin from BOOTSTRAP_ADMIN_EMAIL")
    elif result == "password_synced":
        logger.info("Re-synced admin password/role from env")

    _load_env_stores_into_db()
    refresh_dynamic_stores()

    app = _build_application()
    api = create_app(bot=app.bot)

    uvicorn_cfg = uvicorn.Config(
        api,
        host=config.WEBAPP_HOST,
        port=config.WEBAPP_PORT,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(uvicorn_cfg)

    logger.info(
        "Bot polling + FastAPI on http://%s:%d (WEBAPP_BASE_URL=%s)",
        config.WEBAPP_HOST, config.WEBAPP_PORT,
        config.WEBAPP_BASE_URL or "<unset>",
    )

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        await server.serve()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (see .env.example).")
    asyncio.run(_run_bot_and_webapp())


if __name__ == "__main__":
    main()
