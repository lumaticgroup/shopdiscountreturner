CREATE TABLE IF NOT EXISTS storefronts (
    code TEXT PRIMARY KEY,
    base_url TEXT NOT NULL,
    currency TEXT NOT NULL,
    display_name TEXT NOT NULL
);

-- Static seed for fresh DBs. init_db() then syncs this table from the
-- store registry (sync_storefronts), so dynamic/env stores get rows too.
INSERT OR IGNORE INTO storefronts (code, base_url, currency, display_name) VALUES
    ('trendyol_tr',  'https://www.trendyol.com',    'TL',  'Trendyol Turkey'),
    ('trendyol_uae', 'https://www.trendyol.com/en', 'AED', 'Trendyol UAE'),
    ('shein_tr',     'https://tr.shein.com',        'TL',  'Shein Turkey'),
    ('shein_uae',    'https://ar.shein.com',        'AED', 'Shein UAE');

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    storefront TEXT NOT NULL REFERENCES storefronts(code),
    parent_id INTEGER REFERENCES categories(id),
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    breadcrumb TEXT NOT NULL,
    depth INTEGER NOT NULL,
    UNIQUE (storefront, breadcrumb)
);
CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_categories_storefront ON categories(storefront, depth);

CREATE TABLE IF NOT EXISTS products (
    product_id TEXT NOT NULL,
    storefront TEXT NOT NULL REFERENCES storefronts(code),
    name TEXT NOT NULL,
    brand TEXT,
    url TEXT NOT NULL,
    image_url TEXT,
    price REAL,
    original_price REAL,
    discount_pct INTEGER,
    currency TEXT NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    is_outlet INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (storefront, product_id)
);
CREATE INDEX IF NOT EXISTS idx_products_discount ON products(storefront, discount_pct DESC);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id, discount_pct DESC);
CREATE INDEX IF NOT EXISTS idx_products_last_seen ON products(last_seen);
CREATE INDEX IF NOT EXISTS idx_products_outlet ON products(storefront, is_outlet, discount_pct DESC);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    storefront TEXT NOT NULL,
    product_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    price REAL,
    discount_pct INTEGER,
    UNIQUE (storefront, product_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_history_product ON price_history(storefront, product_id, snapshot_date);

CREATE TABLE IF NOT EXISTS subscribers (
    chat_id INTEGER PRIMARY KEY,
    storefront_pref TEXT DEFAULT 'trendyol_tr' REFERENCES storefronts(code),
    subscribed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_prefs (
    chat_id INTEGER PRIMARY KEY,
    storefront_pref TEXT NOT NULL DEFAULT 'trendyol_tr' REFERENCES storefronts(code)
);

-- Tracks products already posted to the ≥N% Telegram channel firehose so
-- we don't repost. A product reposts only when its discount_pct strictly
-- increases past the last posted value.
CREATE TABLE IF NOT EXISTS channel_posts (
    storefront TEXT NOT NULL,
    product_id TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    discount_pct INTEGER NOT NULL,
    price REAL,
    message_id INTEGER,
    PRIMARY KEY (storefront, product_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_posts_posted_at
    ON channel_posts(posted_at);

-- Small key/value store for admin-managed runtime settings that need to
-- persist across restarts (e.g. channel publish_mode = manual|auto).
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Per-storefront resume point for scrapers that get cut short by rate
-- limiting (Shein's /risk/action/limit). Stores the breadcrumb of the
-- category where the run was blocked; the next run rotates its category
-- list to start there. Cleared after a full clean pass.
CREATE TABLE IF NOT EXISTS scrape_cursors (
    storefront TEXT PRIMARY KEY,
    next_breadcrumb TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Two-role auth: admin (manages stores) vs customer (browses deals).
-- Passwords are bcrypt-hashed. Email is the login identifier.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','customer')) DEFAULT 'customer',
    created_at TEXT NOT NULL
);

-- JWT jti store — lets us revoke tokens server-side without waiting for expiry.
CREATE TABLE IF NOT EXISTS sessions (
    token_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Links a Telegram chat_id to a bot user account. Populated by the Mini App
-- after the initData handshake succeeds. Bot handlers use this to look up
-- who's logged in from just the chat_id.
CREATE TABLE IF NOT EXISTS chat_links (
    chat_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    linked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_links_user ON chat_links(user_id);

-- Stores added at runtime by admins through the Mini App. Loaded into the
-- scraper registry at startup (and reloaded on CRUD). flow_type picks which
-- generic implementation runs: 'api' → GenericAPIStore, 'header' → GenericHeaderStore.
CREATE TABLE IF NOT EXISTS dynamic_stores (
    code TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    flow_type TEXT NOT NULL CHECK(flow_type IN ('api','header')),
    base_url TEXT NOT NULL,
    currency TEXT NOT NULL,
    storefront_code TEXT NOT NULL UNIQUE,
    config_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL
);
