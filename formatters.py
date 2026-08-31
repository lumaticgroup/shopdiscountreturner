"""
Telegram message rendering. All user-facing text uses MarkdownV2 — this is
the ONLY module that produces it, and every user-derived string flows
through `escape_md_v2`.

Telegram's MarkdownV2 requires escaping these chars in general text:
    _ * [ ] ( ) ~ ` > # + - = | { } . !
Inside `[link text](url)`, the URL must escape `)` and `\\`.
"""

from typing import Iterable

from locales import t

_MDV2_SPECIAL = set(r"_*[]()~`>#+-=|{}.!\\")


def escape_md_v2(text: str) -> str:
    if not text:
        return ""
    return "".join("\\" + c if c in _MDV2_SPECIAL else c for c in text)


def escape_url(url: str) -> str:
    return url.replace("\\", "\\\\").replace(")", "\\)")


def _price_line(product: dict) -> str:
    currency = product.get("currency") or ""
    price = product.get("price")
    orig = product.get("original_price")
    if price is None:
        return escape_md_v2("?")
    price_str = f"{price:.2f} {currency}".strip()
    if orig and orig > price:
        return f"~{escape_md_v2(f'{orig:.2f}')}~ *{escape_md_v2(price_str)}*"
    return f"*{escape_md_v2(price_str)}*"


def format_product(product: dict, lang: str = "fa") -> str:
    """One product as a Telegram MarkdownV2 block."""
    name = (product.get("name") or "Unknown")[:80]
    breadcrumb = product.get("category_breadcrumb") or ""
    discount = product.get("discount_pct") or 0
    url = product.get("url") or ""

    header = f"*\\-{discount}%* {escape_md_v2(name)}"
    price = _price_line(product)
    trail_bits = []
    if breadcrumb:
        trail_bits.append(escape_md_v2(breadcrumb))
    link_label = escape_md_v2(t("link_view_site", lang))
    trail_bits.append(f"[{link_label}]({escape_url(url)})")
    trail = " · ".join(trail_bits)

    return f"{header}\n{price} · {trail}"


def format_product_list(
    products: list[dict],
    title: str = "",
    empty_msg: str = "",
    lang: str = "fa",
) -> str:
    if not products:
        return empty_msg or t("no_deals_found", lang)
    body = "\n\n".join(format_product(p, lang=lang) for p in products)
    if title:
        return f"*{escape_md_v2(title)}*\n\n{body}"
    return body


def format_channel_caption(row: dict, store_display_name: str, lang: str = "fa") -> str:
    """
    MarkdownV2 caption for one channel post. Every user-derived string is
    escaped; static template chars (>, !, ., etc.) are pre-escaped inline.
    """
    name = (row.get("name") or "Unknown")[:80]
    brand = row.get("brand") or ""
    breadcrumb = row.get("category_breadcrumb") or ""
    currency = row.get("currency") or ""
    price = row.get("price")
    orig = row.get("original_price")
    pct = int(row.get("discount_pct") or 0)
    url = row.get("url") or ""

    was = f"{orig:.2f} {currency}".strip() if orig else ""
    now = f"{price:.2f} {currency}".strip() if price else ""
    outlet = bool(row.get("is_outlet"))

    head = t("outlet_badge", lang, pct=pct) if outlet else t("deal_badge", lang, pct=pct)
    lines = [head, f"*{escape_md_v2(name)}*"]
    if brand:
        lines.append(escape_md_v2(brand))
    lines.append("")
    if was and now:
        lines.append(f"~{escape_md_v2(was)}~ → *{escape_md_v2(now)}*")
    elif now:
        lines.append(f"*{escape_md_v2(now)}*")
    lines.append("")
    if breadcrumb:
        lines.append(f"📁 {escape_md_v2(breadcrumb)}")
    lines.append(f"🏬 {escape_md_v2(store_display_name)}")
    lines.append("")
    buy_text = escape_md_v2(t("link_buy_on_store", lang, store=store_display_name))
    lines.append(f"[{buy_text}]({escape_url(url)})")
    return "\n".join(lines)


def format_categories(rows: Iterable[dict], lang: str = "fa") -> str:
    suffix = escape_md_v2(t("items_count_suffix", lang))
    lines = [
        f"• *{escape_md_v2(r['name'])}* — {r.get('product_count', 0)} {suffix}"
        for r in rows
    ]
    return "\n".join(lines) if lines else t("categories_empty", lang)


def format_help(storefront_display: str, lang: str = "fa") -> str:
    if lang == "fa":
        lines = [
            f"*ربات تخفیف‌یاب* — فروشگاه فعال: *{escape_md_v2(storefront_display)}*",
            "",
            "/top — برترین تخفیف‌های امروز",
            "/outlet — حراجی‌ها و کالاهای اوت‌لت",
            "/categories — دسته‌بندی‌های کالا",
            "/search \\<عبارت\\> — جستجوی کالا یا برند",
            "/storefront — تغییر فروشگاه فعال",
            "/language — تغییر زبان ربات",
            "/subscribe — عضویت در گزارش روزانه",
            "/unsubscribe — لغو عضویت در گزارش روزانه",
        ]
    else:
        lines = [
            f"*Discount bot* — storefront: *{escape_md_v2(storefront_display)}*",
            "",
            "/top — biggest discounts today",
            "/outlet — outlet & clearance",
            "/categories — browse by category",
            "/search \\<term\\> — search by name or brand",
            "/storefront — switch storefront",
            "/language — change language",
            "/subscribe — daily digest",
            "/unsubscribe — stop the digest",
        ]
    return "\n".join(lines)

