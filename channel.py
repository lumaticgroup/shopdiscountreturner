"""
≥N% off Telegram channel firehose. Called after every scrape completes;
posts any qualifying product that hasn't been posted yet (or is now at a
deeper discount than when we last posted it).

De-dup lives in the `channel_posts` DB table; the poster is idempotent.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Bot
from telegram.constants import ParseMode

import config
import db
from formatters import format_channel_caption
from scraper.stores import store_for_storefront

logger = logging.getLogger("channel")


async def post_qualifying_deals(bot: Bot) -> int:
    """
    Post every qualifying product to the configured channel. Returns the
    number of successful posts. No-op if TELEGRAM_CHANNEL_ID is unset.
    """
    if not config.TELEGRAM_CHANNEL_ID:
        logger.info("TELEGRAM_CHANNEL_ID not set — skipping channel firehose")
        return 0

    candidates = db.list_channel_candidates(config.CHANNEL_MIN_DISCOUNT_PCT)
    if not candidates:
        logger.info("No qualifying candidates for channel")
        return 0

    delay = 60.0 / max(1, config.CHANNEL_POST_RATE_PER_MIN)
    posted = 0
    for row in candidates:
        try:
            store = store_for_storefront(row["storefront"])
            store_name = store.display_name
        except ValueError:
            store_name = row["storefront"]

        caption = format_channel_caption(row, store_name)
        image = row.get("image_url")
        message_id = None
        try:
            if image:
                msg = await bot.send_photo(
                    chat_id=config.TELEGRAM_CHANNEL_ID,
                    photo=image,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                msg = await bot.send_message(
                    chat_id=config.TELEGRAM_CHANNEL_ID,
                    text=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    disable_web_page_preview=False,
                )
            message_id = msg.message_id
        except Exception as e:
            logger.warning(
                "send_photo failed for %s/%s (%s) — falling back to text",
                row["storefront"], row["product_id"], e,
            )
            try:
                msg = await bot.send_message(
                    chat_id=config.TELEGRAM_CHANNEL_ID,
                    text=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    disable_web_page_preview=False,
                )
                message_id = msg.message_id
            except Exception:
                logger.exception(
                    "Channel post failed for %s/%s",
                    row["storefront"], row["product_id"],
                )
                await asyncio.sleep(delay)
                continue

        db.record_channel_post(
            storefront=row["storefront"],
            product_id=row["product_id"],
            discount_pct=int(row["discount_pct"] or 0),
            price=row.get("price"),
            message_id=message_id,
        )
        posted += 1
        await asyncio.sleep(delay)

    logger.info("Channel firehose: posted %d/%d candidates",
                posted, len(candidates))
    return posted
