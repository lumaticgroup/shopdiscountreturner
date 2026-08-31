"""
Storage facade. Bot and scraper import from here — swapping SQLite for
Turso (libsql) later means changing only which backend module we re-export.
"""
from .sqlite_backend import (
    add_subscriber,
    count_admins,
    count_products_in_category,
    counts_by_storefront,
    create_session,
    create_user,
    delete_dynamic_store,
    get_category,
    get_dynamic_store,
    get_language_pref,
    get_products_by_ids,
    get_scrape_cursor,
    get_session,
    get_setting,
    get_storefront_pref,
    get_user,
    get_user_by_email,
    init_db,
    link_chat_to_user,
    list_channel_candidates,
    list_child_categories,
    list_dynamic_stores,
    list_subscribers,
    list_top_categories,
    products_in_category,
    record_channel_post,
    remove_subscriber,
    revoke_session,
    role_for_chat,
    search_products,
    set_dynamic_store_enabled,
    set_language_pref,
    set_scrape_cursor,
    set_setting,
    set_storefront_pref,
    set_user_password,
    set_user_role,
    sync_storefronts,
    top_discounts,
    upsert_category,
    upsert_dynamic_store,
    upsert_products,
    user_for_chat,
)

__all__ = [
    # core
    "init_db",
    "sync_storefronts",
    # categories
    "upsert_category",
    "list_top_categories",
    "list_child_categories",
    "get_category",
    # products
    "upsert_products",
    "top_discounts",
    "products_in_category",
    "count_products_in_category",
    "search_products",
    "get_products_by_ids",
    # subscribers / prefs
    "add_subscriber",
    "remove_subscriber",
    "list_subscribers",
    "get_storefront_pref",
    "set_storefront_pref",
    "get_language_pref",
    "set_language_pref",

    # diagnostics
    "counts_by_storefront",
    # channel firehose
    "list_channel_candidates",
    "record_channel_post",
    # scrape cursors
    "get_scrape_cursor",
    "set_scrape_cursor",
    # runtime settings (key/value)
    "get_setting",
    "set_setting",
    # users / auth
    "create_user",
    "get_user_by_email",
    "get_user",
    "set_user_password",
    "set_user_role",
    "count_admins",
    # sessions
    "create_session",
    "get_session",
    "revoke_session",
    # chat links
    "link_chat_to_user",
    "user_for_chat",
    "role_for_chat",
    # dynamic stores
    "list_dynamic_stores",
    "get_dynamic_store",
    "upsert_dynamic_store",
    "delete_dynamic_store",
    "set_dynamic_store_enabled",
]

