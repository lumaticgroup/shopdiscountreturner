"""
Centralized bilingual localization module (Persian 'fa' & English 'en').
Provides fluent, natural Persian and English translations for all bot UI,
buttons, commands, messages, formatters, and admin interfaces.
"""

from typing import Any

DEFAULT_LANGUAGE = "fa"

STRINGS: dict[str, dict[str, str]] = {
    # -------------------------------------------------------------
    # Language Selection & Onboarding
    # -------------------------------------------------------------
    "lang_picker_prompt": {
        "fa": "🌐 *لطفاً زبان مورد نظر خود را انتخاب کنید:*\n\n_Please select your language:_",
        "en": "🌐 *Please select your language:*\n\n_لطفاً زبان مورد نظر خود را انتخاب کنید:_",
    },
    "btn_lang_fa": {
        "fa": "🇮🇷 فارسی",
        "en": "🇮🇷 فارسی",
    },
    "btn_lang_en": {
        "fa": "🇬🇧 English",
        "en": "🇬🇧 English",
    },
    "lang_changed": {
        "fa": "✅ زبان با موفقیت روی *فارسی* تنظیم شد\\.",
        "en": "✅ Language has been set to *English*\\.",
    },

    # -------------------------------------------------------------
    # Customer Menu Buttons
    # -------------------------------------------------------------
    "btn_top_deals": {
        "fa": "🔥 تخفیف‌های داغ",
        "en": "🔥 Top Deals",
    },
    "btn_outlet": {
        "fa": "🏷 حراج و اوت‌لت",
        "en": "🍾 Outlet",
    },
    "btn_categories": {
        "fa": "📁 دسته‌بندی‌ها",
        "en": "📁 Categories",
    },
    "btn_search": {
        "fa": "🔍 جستجوی کالا",
        "en": "🔍 Search",
    },
    "btn_storefront": {
        "fa": "🏬 انتخاب فروشگاه",
        "en": "🏬 Storefront",
    },
    "btn_refresh_deals": {
        "fa": "🔄 به‌روزرسانی تخفیف‌ها",
        "en": "🔄 Refresh Deals",
    },
    "btn_language": {
        "fa": "🌐 تغییر زبان",
        "en": "🌐 Change Language",
    },
    "btn_switch_admin": {
        "fa": "⚙️ ورود به پنل مدیریت",
        "en": "⚙️ Switch to Admin Dashboard",
    },
    "btn_back_admin": {
        "fa": "⚙️ بازگشت به پنل مدیریت",
        "en": "⚙️ Back to Admin Dashboard",
    },
    "btn_customer_view": {
        "fa": "👁 مشاهده نمای مشتریان",
        "en": "👁 Customer Deals View",
    },

    # -------------------------------------------------------------
    # Customer Welcome & Status
    # -------------------------------------------------------------
    "welcome_header": {
        "fa": "*به ربات تخفیف‌یاب خوش آمدید، {name}*",
        "en": "*Welcome, {name}*",
    },
    "storefront_label": {
        "fa": "فروشگاه فعال: *{storefront}*",
        "en": "Storefront: *{storefront}*",
    },
    "role_admin": {
        "fa": "_نقش: 👑 مدیر سیستم_",
        "en": "_Role: 👑 Administrator_",
    },
    "welcome_subtext": {
        "fa": "از گزینه‌های زیر برای مشاهده جدیدترین تخفیف‌ها و حراجی‌ها استفاده کنید\\.",
        "en": "Discover the latest discounted products and outlet deals below\\.",
    },

    # -------------------------------------------------------------
    # Navigation & Pagination Buttons
    # -------------------------------------------------------------
    "btn_prev": {
        "fa": "⬅ قبلی",
        "en": "⬅ Prev",
    },
    "btn_next": {
        "fa": "بعدی ➡",
        "en": "Next ➡",
    },
    "btn_back_categories": {
        "fa": "↩ بازگشت به دسته‌ها",
        "en": "↩ Categories",
    },
    "page_label": {
        "fa": "صفحه {page}",
        "en": "page {page}",
    },

    # -------------------------------------------------------------
    # Storefront Selection
    # -------------------------------------------------------------
    "storefront_picker_prompt": {
        "fa": "🏬 *انتخاب فروشگاه فعال*\n\nفروشگاهی که مایلید تخفیف‌های آن را مشاهده کنید انتخاب نمایید:",
        "en": "🏬 *Pick a Storefront*\n\nChoose the store you want to browse discounts for:",
    },
    "storefront_switched": {
        "fa": "✅ فروشگاه با موفقیت روی *{storefront}* تنظیم شد\\.",
        "en": "✅ Storefront set to *{storefront}*\\.",
    },
    "sf_trendyol_tr": {
        "fa": "ترندیول ترکیه (لیر)",
        "en": "Trendyol Turkey (TL)",
    },
    "sf_trendyol_uae": {
        "fa": "ترندیول امارات (درهم)",
        "en": "Trendyol UAE (AED)",
    },
    "sf_shein_tr": {
        "fa": "شین ترکیه (لیر)",
        "en": "Shein Turkey (TL)",
    },
    "sf_shein_uae": {
        "fa": "شین امارات (درهم)",
        "en": "Shein UAE (AED)",
    },

    # -------------------------------------------------------------
    # Refresh Deals & Scraper Feedback
    # -------------------------------------------------------------
    "fetching_deals": {
        "fa": "در حال دریافت جدیدترین تخفیف‌ها از *{store_name}*، لطفاً چند لحظه صبر کنید…",
        "en": "Fetching the latest deals from *{store_name}*, this may take a few moments…",
    },
    "rate_limited_wait": {
        "fa": "⏳ لطفاً کمی صبر کنید — تخفیف‌ها {seconds} ثانیه پیش به‌روزرسانی شده‌اند\\.",
        "en": "⏳ Too quick — refreshed {seconds}s ago\\. Please wait a moment before refreshing again\\.",
    },
    "scrape_failed": {
        "fa": "❌ دریافت تخفیف‌ها با خطا مواجه شد\\. لطفاً لحظاتی بعد مجدداً تلاش کنید\\.",
        "en": "❌ Refresh failed — could not fetch live deals\\. Please try again shortly\\.",
    },
    "no_deals_found": {
        "fa": "هنوز کالای تخفیف‌داری برای این بخش ثبت نشده است\\.",
        "en": "No discounted products found yet\\.",
    },
    "no_qualifying_discounts": {
        "fa": "در حال حاضر تخفیف بالای ۳۰٪ در *{store_name}* یافت نشد\\. لطفاً بعداً بررسی فرمایید\\.",
        "en": "No products found with ≥30% discount on *{store_name}* yet\\. Check back soon\\.",
    },

    # -------------------------------------------------------------
    # Search
    # -------------------------------------------------------------
    "search_usage": {
        "fa": "برای جستجوی کالا یا برند، دستور زیر را ارسال کنید:\n`/search لباس` یا `/search zara`",
        "en": "Send `/search <term>` — e.g. `/search dress` or `/search zara`\\.",
    },
    "search_title": {
        "fa": "نتایج جستجو برای: *{term}*",
        "en": "Search results for: *{term}*",
    },
    "search_empty": {
        "fa": "متأسفانه کالای تخفیف‌داری برای عبارت _{term}_ یافت نشد\\.",
        "en": "No discounted matches for _{term}_ yet\\.",
    },

    # -------------------------------------------------------------
    # Categories
    # -------------------------------------------------------------
    "categories_title": {
        "fa": "📁 *دسته‌بندی‌های تخفیف‌دار*",
        "en": "📁 *Discounted Categories*",
    },
    "categories_empty": {
        "fa": "هنوز دسته‌بندی‌ای بارگذاری نشده است\\.",
        "en": "No categories available yet\\.",
    },
    "category_no_products": {
        "fa": "در حال حاضر کالای تخفیف‌داری در دسته *{title}* موجود نیست\\.",
        "en": "No discounted products in *{title}* yet\\.",
    },
    "items_count_suffix": {
        "fa": "کالا",
        "en": "items",
    },

    # -------------------------------------------------------------
    # Subscriptions & Daily Digest
    # -------------------------------------------------------------
    "subscribed_success": {
        "fa": "✅ با موفقیت در گزارش روزانه تخفیف‌ها عضو شدید\\.\nارسال هر روز ساعت *{hour:02d}:{minute:02d}* (به وقت سرور)\\.",
        "en": "✅ Subscribed to the daily deals digest\\.\nDelivery every day at *{hour:02d}:{minute:02d}* (server time)\\.",
    },
    "unsubscribed_success": {
        "fa": "✅ عضویت شما در گزارش روزانه لغو شد\\.",
        "en": "✅ Unsubscribed from the daily digest\\.",
    },
    "daily_digest_header": {
        "fa": "🌅 *گزارش برترین تخفیف‌های امروز* ({storefront})\n\n",
        "en": "🌅 *Today's Top Deals Digest* ({storefront})\n\n",
    },

    # -------------------------------------------------------------
    # Formatters & Links
    # -------------------------------------------------------------
    "link_view_site": {
        "fa": "مشاهده در سایت",
        "en": "View on Site",
    },
    "link_buy_on_store": {
        "fa": "خرید از {store}",
        "en": "Buy on {store}",
    },
    "outlet_badge": {
        "fa": "🏷 *\\-{pct}% حراج اوت‌لت*",
        "en": "🏷 *\\-{pct}% Outlet*",
    },
    "deal_badge": {
        "fa": "🔥 *\\-{pct}%*",
        "en": "🔥 *\\-{pct}%*",
    },
    "top_deals_header": {
        "fa": "برترین تخفیف‌های امروز ({storefront})",
        "en": "Top discounts today ({storefront})",
    },
    "outlet_deals_header": {
        "fa": "حراجی‌ها و کالاهای اوت‌لت ({storefront})",
        "en": "Outlet & Clearance deals ({storefront})",
    },

    # -------------------------------------------------------------
    # Admin Interface & Actions
    # -------------------------------------------------------------
    "admin_dashboard_title": {
        "fa": (
            "👑 *داشبورد مدیریت ربات*\n\n"
            "🏬 فروشگاه‌های فعال: *{enabled}/{total}*\n"
            "📢 وضعیت انتشار در کانال: *{mode}*\n\n"
            "یک عملیات مدیریتی را انتخاب نمایید:"
        ),
        "en": (
            "👑 *Administrator Dashboard*\n\n"
            "🏬 Dynamic Stores: *{enabled}/{total}* active\n"
            "📢 Channel Publishing: *{mode}*\n\n"
            "Choose an administrative management action below:"
        ),
    },
    "btn_admin_stores": {
        "fa": "🏬 مدیریت فروشگاه‌ها",
        "en": "🏬 Manage Stores",
    },
    "btn_admin_add_guide": {
        "fa": "➕ راهنمای افزودن فروشگاه",
        "en": "➕ Add Store Guide",
    },
    "btn_admin_scrape_all": {
        "fa": "⚡ دریافت تخفیف همه فروشگاه‌ها",
        "en": "⚡ Scrape All",
    },
    "btn_admin_reload": {
        "fa": "🔄 بارگذاری مجدد رجیستری",
        "en": "🔄 Reload Registry",
    },
    "btn_admin_publishing": {
        "fa": "📢 تنظیمات انتشار کانال",
        "en": "📢 Publishing Menu",
    },
    "btn_admin_templates": {
        "fa": "📋 قالب‌های تنظیمات JSON",
        "en": "📋 Config Templates",
    },
    "btn_admin_back_stores": {
        "fa": "↩ بازگشت به فروشگاه‌ها",
        "en": "↩ Back to Stores",
    },
    "btn_admin_back_menu": {
        "fa": "↩ منوی مدیریت",
        "en": "↩ Admin Menu",
    },
    "btn_store_enable": {
        "fa": "🟢 فعال‌سازی فروشگاه",
        "en": "🟢 Enable Store",
    },
    "btn_store_disable": {
        "fa": "🔴 غیرفعال‌سازی فروشگاه",
        "en": "🔴 Disable Store",
    },
    "btn_store_delete": {
        "fa": "🗑 حذف فروشگاه",
        "en": "🗑 Delete Store",
    },

    # Admin Login & Status
    "admin_only_error": {
        "fa": "⛔ *این بخش مخصوص مدیران است\\.*\nبرای احراز هویت از دستور `/admin_login <رمز_عبور>` استفاده نمایید\\.",
        "en": "⛔ *Admin only\\.*\nIf you have the admin password, use `/admin_login <password>` to authenticate\\.",
    },
    "admin_login_usage": {
        "fa": "نحوه استفاده: `/admin_login <رمز_عبور>`",
        "en": "Usage: `/admin_login <password>`",
    },
    "admin_login_success": {
        "fa": "👑 *حالت مدیریت با موفقیت فعال شد\\!*",
        "en": "👑 *Admin Mode Activated\\!*",
    },
    "admin_login_invalid": {
        "fa": "❌ رمز عبور مدیریت اشتباه است\\.",
        "en": "❌ Incorrect admin password\\.",
    },
    "admin_logged_out": {
        "fa": "🔒 *از حالت مدیریت خارج شدید\\.*\n\nاکنون در نمای معمولی کاربری قرار دارید\\.",
        "en": "🔒 *Logged out of Admin Mode\\.*\n\nYou are now in regular customer view\\.",
    },

    # Admin Channel Publishing
    "pub_auto_header": {
        "fa": "🔁 *خودکار* — هر {interval} دقیقه",
        "en": "🔁 *Automatic* — every {interval} min",
    },
    "pub_manual_header": {
        "fa": "🖐 *دستی*",
        "en": "🖐 *Manual*",
    },
    "btn_pub_switch_manual": {
        "fa": "⏸ تغییر به حالت دستی",
        "en": "⏸ Switch to Manual",
    },
    "btn_pub_switch_auto": {
        "fa": "▶ تغییر به حالت خودکار",
        "en": "▶ Switch to Automatic",
    },
    "btn_pub_now": {
        "fa": "📤 انتشار فوری در کانال",
        "en": "📤 Publish now",
    },
    "pub_menu_body": {
        "fa": (
            "وضعیت انتشار: {header}\n\n"
            "*دستی* — تا زمانی که روی *انتشار فوری* نزنید، پستی در کانال ارسال نمی‌شود\\.\n"
            "*خودکار* — ربات هر {interval} دقیقه فروشگاه‌ها را بررسی و تخفیف‌های ویژه جدید را در کانال ارسال می‌کند\\.\n\n"
            "می‌توانید در هر دو حالت برای ارسال فوری روی *انتشار فوری* بزنید\\."
        ),
        "en": (
            "Publishing mode: {header}\n\n"
            "*Manual* — nothing is posted to the channel until you tap *Publish now*\\.\n"
            "*Automatic* — every {interval} minutes the bot refreshes every storefront and posts the latest qualifying deals to the channel\\.\n\n"
            "Tap *Publish now* to drain the queue immediately in either mode\\."
        ),
    },
    "pub_in_progress": {
        "fa": "⏳ ارسال تخفیف‌ها به کانال در حال حاضر در حال انجام است\\.",
        "en": "⏳ Channel broadcast is already in progress\\. Please wait a moment\\.",
    },
    "pub_no_candidates": {
        "fa": "ℹ️ تخفیف جدیدی برای ارسال وجود ندارد\\. تمام تخفیف‌های بالای {pct}٪ قبلاً در کانال ارسال شده‌اند\\.",
        "en": "ℹ️ No new deals pending to publish\\. All deals with ≥{pct}% discount have already been posted\\.",
    },
    "pub_starting": {
        "fa": "📤 در حال ارسال {count} کالای تخفیف‌دار ویژه (بالای {pct}٪) به کانال…",
        "en": "📤 Publishing {count} qualifying deal(s) (≥{pct}%) to the channel…",
    },
    "pub_finished": {
        "fa": "✅ پایان انتشار\\! تعداد {count} کالا در کانال تلگرام ارسال شد\\.",
        "en": "✅ Finished\\! Posted {count} product(s) to the channel\\.",
    },
    "pub_failed": {
        "fa": "❌ ارسال به کانال با خطا مواجه شد — لطفاً لاگ سرور را بررسی کنید\\.",
        "en": "❌ Publish failed — please check server logs\\.",
    },

    # Admin Scrape All
    "scrape_all_starting": {
        "fa": "🚀 فرآیند دریافت تخفیف برای تمام فروشگاه‌ها در پس‌زمینه آغاز شد…",
        "en": "🚀 Starting scrape across all storefronts in background…",
    },
    "scrape_all_done": {
        "fa": "✅ دریافت تخفیف‌های تمام فروشگاه‌ها با موفقیت پایان یافت\\!",
        "en": "✅ Background scrape of all storefronts completed\\!",
    },
    "registry_reloaded": {
        "fa": "🔄 رجیستری فروشگاه‌ها با موفقیت به‌روزرسانی شد\\.",
        "en": "🔄 Scraper registry reloaded successfully\\.",
    },

    # Customer View Preview
    "customer_view_preview_text": {
        "fa": "👁 *پیش‌نمایش منوی تخفیف‌های مشتریان*\nفروشگاه: *{storefront}*\n\nاین دقیقاً همان نمایی است که خریداران در ربات مشاهده می‌کنند\\.",
        "en": "👁 *Customer Deals View Preview*\nStorefront: *{storefront}*\n\nThis is the exact deals menu regular customers see in the bot\\.",
    },

    # Dynamic Stores Management
    "dynamic_stores_empty": {
        "fa": "🏬 *فروشگاه‌های پویا*\n\nهنوز فروشگاه پویایی ثبت نشده است\\.\nبرای افزودن از دستور `/addstore <JSON>` یا راهنمای افزودن استفاده نمایید\\.",
        "en": "🏬 *Dynamic Stores*\n\nNo dynamic stores registered yet\\.\nUse `/addstore <JSON>` or check `/sample_store` to add one\\.",
    },
    "dynamic_stores_list_title": {
        "fa": "🏬 *لیست فروشگاه‌های پویا*\n\nبرای مشاهده تنظیمات یا تغییر وضعیت فعال/غیرفعال، فروشگاه مورد نظر را انتخاب کنید:",
        "en": "🏬 *Dynamic Stores List*\n\nSelect a store below to view settings or toggle state:",
    },
    "addstore_usage": {
        "fa": "نحوه استفاده: `/addstore <JSON>`\n\nتنظیمات کامل فروشگاه را با فرمت JSON ارسال کنید\\.\nبرای مشاهده قالب‌ها از دستور `/sample_store` استفاده فرمایید\\.",
        "en": "Usage: `/addstore <JSON>`\n\nSend the complete JSON configuration object for the dynamic store\\.\nUse `/sample_store` to view templates\\.",
    },
    "store_saved_success": {
        "fa": "✅ فروشگاه *{code}* با موفقیت ذخیره و در سیستم بارگذاری شد\\!",
        "en": "✅ Store *{code}* saved and loaded into scraper registry\\!",
    },
    "delstore_usage": {
        "fa": "نحوه استفاده: `/delstore <کد_فروشگاه>`",
        "en": "Usage: `/delstore <store_code>`",
    },
    "store_not_found": {
        "fa": "فروشگاه '{code}' یافت نشد\\.",
        "en": "Store '{code}' not found\\.",
    },
    "store_deleted_success": {
        "fa": "🗑 فروشگاه *{code}* با موفقیت حذف شد\\.",
        "en": "🗑 Store *{code}* deleted from dynamic stores\\.",
    },

}


def t(key: str, lang: str = "fa", **kwargs: Any) -> str:
    """
    Retrieve localized string by key and language with formatted parameters.
    Falls back to English if the key is not translated for the given language.
    """
    selected_lang = lang if lang in ("fa", "en") else DEFAULT_LANGUAGE
    entry = STRINGS.get(key)
    if not entry:
        return key

    template = entry.get(selected_lang) or entry.get("en") or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template
