"""
SQLite backend. Public functions match the facade in db/__init__.py so a
swap to Turso (libsql) later is drop-in — only this file changes.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@contextmanager
def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA_PATH.read_text())


# ---------- Categories ----------

def upsert_category(
    storefront: str,
    name: str,
    slug: str,
    breadcrumb: str,
    depth: int,
    parent_id: Optional[int],
) -> int:
    with _conn() as c:
        row = c.execute(
            "SELECT id FROM categories WHERE storefront = ? AND breadcrumb = ?",
            (storefront, breadcrumb),
        ).fetchone()
        if row:
            return row["id"]
        cur = c.execute(
            """INSERT INTO categories (storefront, parent_id, name, slug, breadcrumb, depth)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (storefront, parent_id, name, slug, breadcrumb, depth),
        )
        return cur.lastrowid


def list_top_categories(storefront: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """SELECT c.id, c.name, c.breadcrumb,
                      (SELECT COUNT(*) FROM products p
                        WHERE p.category_id IN (
                            WITH RECURSIVE sub(id) AS (
                                SELECT c.id
                                UNION ALL
                                SELECT cc.id FROM categories cc JOIN sub ON cc.parent_id = sub.id
                            )
                            SELECT id FROM sub
                        ) AND p.discount_pct > 0) AS product_count
               FROM categories c
               WHERE c.storefront = ? AND c.depth = 0
               ORDER BY c.name""",
            (storefront,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_child_categories(parent_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, name, breadcrumb FROM categories WHERE parent_id = ? ORDER BY name",
            (parent_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_category(category_id: int) -> Optional[dict]:
    with _conn() as c:
        r = c.execute(
            "SELECT id, name, breadcrumb, parent_id, storefront FROM categories WHERE id = ?",
            (category_id,),
        ).fetchone()
        return dict(r) if r else None


# ---------- Products ----------

def upsert_products(products: list) -> None:
    """Upsert products; append to price_history only when price/discount changed."""
    if not products:
        return
    now = datetime.now(timezone.utc).isoformat()
    today = now[:10]
    with _conn() as c:
        for p in products:
            prev = c.execute(
                "SELECT price, discount_pct FROM products WHERE storefront = ? AND product_id = ?",
                (p.storefront, p.product_id),
            ).fetchone()
            c.execute(
                """INSERT INTO products
                       (product_id, storefront, name, brand, url, image_url,
                        price, original_price, discount_pct, currency,
                        category_id, is_outlet, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(storefront, product_id) DO UPDATE SET
                       name=excluded.name,
                       brand=excluded.brand,
                       url=excluded.url,
                       image_url=excluded.image_url,
                       price=excluded.price,
                       original_price=excluded.original_price,
                       discount_pct=excluded.discount_pct,
                       category_id=excluded.category_id,
                       is_outlet=excluded.is_outlet,
                       last_seen=excluded.last_seen""",
                (
                    p.product_id, p.storefront, p.name, p.brand, p.url, p.image_url,
                    p.price, p.original_price, p.discount_pct, p.currency,
                    p.category_id, 1 if p.is_outlet else 0, now, now,
                ),
            )
            changed = (
                prev is None
                or prev["price"] != p.price
                or prev["discount_pct"] != p.discount_pct
            )
            if changed:
                c.execute(
                    """INSERT OR IGNORE INTO price_history
                           (storefront, product_id, snapshot_date, price, discount_pct)
                       VALUES (?, ?, ?, ?, ?)""",
                    (p.storefront, p.product_id, today, p.price, p.discount_pct),
                )


def _rows_to_products(rows) -> list[dict]:
    return [dict(r) for r in rows]


def top_discounts(storefront: str, limit: int = 20, outlet_only: bool = False) -> list[dict]:
    q = """SELECT p.*, c.breadcrumb AS category_breadcrumb
             FROM products p
             LEFT JOIN categories c ON c.id = p.category_id
            WHERE p.storefront = ? AND p.discount_pct > 0"""
    params: list = [storefront]
    if outlet_only:
        q += " AND p.is_outlet = 1"
    q += " ORDER BY p.discount_pct DESC, p.last_seen DESC LIMIT ?"
    params.append(limit)
    with _conn() as c:
        return _rows_to_products(c.execute(q, params).fetchall())


def products_in_category(category_id: int, offset: int = 0, limit: int = 10) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """WITH RECURSIVE sub(id) AS (
                    SELECT ?
                    UNION ALL
                    SELECT c.id FROM categories c JOIN sub ON c.parent_id = sub.id
               )
               SELECT p.*, cat.breadcrumb AS category_breadcrumb
                 FROM products p
                 LEFT JOIN categories cat ON cat.id = p.category_id
                WHERE p.category_id IN (SELECT id FROM sub) AND p.discount_pct > 0
                ORDER BY p.discount_pct DESC LIMIT ? OFFSET ?""",
            (category_id, limit, offset),
        ).fetchall()
        return _rows_to_products(rows)


def count_products_in_category(category_id: int) -> int:
    with _conn() as c:
        r = c.execute(
            """WITH RECURSIVE sub(id) AS (
                    SELECT ?
                    UNION ALL
                    SELECT c.id FROM categories c JOIN sub ON c.parent_id = sub.id
               )
               SELECT COUNT(*) AS n FROM products
                WHERE category_id IN (SELECT id FROM sub) AND discount_pct > 0""",
            (category_id,),
        ).fetchone()
        return r["n"]


def search_products(storefront: str, term: str, limit: int = 20) -> list[dict]:
    like = f"%{term.lower()}%"
    with _conn() as c:
        rows = c.execute(
            """SELECT p.*, c.breadcrumb AS category_breadcrumb
                 FROM products p
                 LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.storefront = ? AND p.discount_pct > 0
                  AND (LOWER(p.name) LIKE ? OR LOWER(p.brand) LIKE ?)
                ORDER BY p.discount_pct DESC LIMIT ?""",
            (storefront, like, like, limit),
        ).fetchall()
        return _rows_to_products(rows)


# ---------- Subscribers / prefs ----------

def add_subscriber(chat_id: int) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO subscribers (chat_id, subscribed_at) VALUES (?, ?)",
            (chat_id, datetime.now(timezone.utc).isoformat()),
        )


def remove_subscriber(chat_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))


def list_subscribers() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """SELECT s.chat_id,
                      COALESCE(u.storefront_pref, s.storefront_pref, 'tr') AS storefront_pref
                 FROM subscribers s
                 LEFT JOIN user_prefs u ON u.chat_id = s.chat_id""",
        ).fetchall()
        return [dict(r) for r in rows]


def get_storefront_pref(chat_id: int) -> str:
    with _conn() as c:
        r = c.execute(
            "SELECT storefront_pref FROM user_prefs WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return r["storefront_pref"] if r else "tr"


def set_storefront_pref(chat_id: int, storefront: str) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO user_prefs (chat_id, storefront_pref) VALUES (?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET storefront_pref = excluded.storefront_pref""",
            (chat_id, storefront),
        )


# ---------- Channel firehose ----------

def list_channel_candidates(min_discount_pct: int) -> list[dict]:
    """
    Products qualifying for a channel post: discount ≥ threshold AND either
    never posted, or now at a strictly higher discount than last posted.
    """
    with _conn() as c:
        rows = c.execute(
            """SELECT p.storefront, p.product_id, p.name, p.brand, p.url,
                      p.image_url, p.price, p.original_price, p.discount_pct,
                      p.currency, cat.breadcrumb AS category_breadcrumb
                 FROM products p
                 LEFT JOIN categories cat ON cat.id = p.category_id
                 LEFT JOIN channel_posts cp
                        ON cp.storefront = p.storefront
                       AND cp.product_id = p.product_id
                WHERE p.discount_pct >= ?
                  AND (cp.product_id IS NULL OR p.discount_pct > cp.discount_pct)
                ORDER BY p.discount_pct DESC""",
            (min_discount_pct,),
        ).fetchall()
        return [dict(r) for r in rows]


def record_channel_post(
    storefront: str,
    product_id: str,
    discount_pct: int,
    price: Optional[float],
    message_id: Optional[int],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """INSERT INTO channel_posts
                   (storefront, product_id, posted_at, discount_pct, price, message_id)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(storefront, product_id) DO UPDATE SET
                   posted_at = excluded.posted_at,
                   discount_pct = excluded.discount_pct,
                   price = excluded.price,
                   message_id = excluded.message_id""",
            (storefront, product_id, now, discount_pct, price, message_id),
        )


# ---------- Users / auth ----------

def create_user(email: str, password_hash: str, role: str = "customer") -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (email.lower(), password_hash, role, now),
        )
        return cur.lastrowid


def get_user_by_email(email: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute(
            "SELECT id, email, password_hash, role, created_at FROM users WHERE email = ?",
            (email.lower(),),
        ).fetchone()
        return dict(r) if r else None


def get_user(user_id: int) -> Optional[dict]:
    with _conn() as c:
        r = c.execute(
            "SELECT id, email, role, created_at FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        return dict(r) if r else None


def set_user_role(user_id: int, role: str) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def count_admins() -> int:
    with _conn() as c:
        r = c.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'").fetchone()
        return r["n"]


# ---------- Sessions ----------

def create_session(token_id: str, user_id: int, expires_at: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """INSERT INTO sessions (token_id, user_id, created_at, expires_at, revoked)
               VALUES (?, ?, ?, ?, 0)""",
            (token_id, user_id, now, expires_at),
        )


def get_session(token_id: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute(
            "SELECT token_id, user_id, expires_at, revoked FROM sessions WHERE token_id = ?",
            (token_id,),
        ).fetchone()
        return dict(r) if r else None


def revoke_session(token_id: str) -> None:
    with _conn() as c:
        c.execute("UPDATE sessions SET revoked = 1 WHERE token_id = ?", (token_id,))


# ---------- Chat ↔ user link ----------

def link_chat_to_user(chat_id: int, user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """INSERT INTO chat_links (chat_id, user_id, linked_at) VALUES (?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   user_id = excluded.user_id,
                   linked_at = excluded.linked_at""",
            (chat_id, user_id, now),
        )


def user_for_chat(chat_id: int) -> Optional[dict]:
    with _conn() as c:
        r = c.execute(
            """SELECT u.id, u.email, u.role
                 FROM chat_links cl
                 JOIN users u ON u.id = cl.user_id
                WHERE cl.chat_id = ?""",
            (chat_id,),
        ).fetchone()
        return dict(r) if r else None


def role_for_chat(chat_id: int) -> Optional[str]:
    user = user_for_chat(chat_id)
    return user["role"] if user else None


# ---------- Dynamic stores ----------

def list_dynamic_stores(enabled_only: bool = True) -> list[dict]:
    q = """SELECT code, display_name, flow_type, base_url, currency,
                  storefront_code, config_json, enabled, created_by, updated_at
             FROM dynamic_stores"""
    if enabled_only:
        q += " WHERE enabled = 1"
    q += " ORDER BY code"
    with _conn() as c:
        return [dict(r) for r in c.execute(q).fetchall()]


def get_dynamic_store(code: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute(
            """SELECT code, display_name, flow_type, base_url, currency,
                      storefront_code, config_json, enabled, created_by, updated_at
                 FROM dynamic_stores WHERE code = ?""",
            (code,),
        ).fetchone()
        return dict(r) if r else None


def upsert_dynamic_store(
    code: str,
    display_name: str,
    flow_type: str,
    base_url: str,
    currency: str,
    storefront_code: str,
    config_json: str,
    enabled: bool = True,
    created_by: Optional[int] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """INSERT INTO dynamic_stores
                   (code, display_name, flow_type, base_url, currency,
                    storefront_code, config_json, enabled, created_by, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET
                   display_name = excluded.display_name,
                   flow_type = excluded.flow_type,
                   base_url = excluded.base_url,
                   currency = excluded.currency,
                   storefront_code = excluded.storefront_code,
                   config_json = excluded.config_json,
                   enabled = excluded.enabled,
                   updated_at = excluded.updated_at""",
            (code, display_name, flow_type, base_url, currency,
             storefront_code, config_json, 1 if enabled else 0, created_by, now),
        )


def delete_dynamic_store(code: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM dynamic_stores WHERE code = ?", (code,))


# ---------- Product batch fetch (send-to-channel) ----------

def get_products_by_ids(pairs: list[tuple[str, str]]) -> list[dict]:
    """
    Batch fetch (storefront, product_id) → product rows. Preserves the input
    order so the channel post ordering matches what the user just saw.
    """
    if not pairs:
        return []
    placeholders = ",".join(["(?, ?)"] * len(pairs))
    flat: list = []
    for sf, pid in pairs:
        flat.extend([sf, pid])
    with _conn() as c:
        rows = c.execute(
            f"""SELECT p.*, cat.breadcrumb AS category_breadcrumb
                  FROM products p
                  LEFT JOIN categories cat ON cat.id = p.category_id
                 WHERE (p.storefront, p.product_id) IN ({placeholders})""",
            flat,
        ).fetchall()
    by_key = {(r["storefront"], r["product_id"]): dict(r) for r in rows}
    return [by_key[k] for k in pairs if k in by_key]


# ---------- Diagnostics ----------

def counts_by_storefront() -> dict:
    with _conn() as c:
        rows = c.execute(
            """SELECT storefront,
                      COUNT(*) AS total,
                      SUM(CASE WHEN discount_pct > 0 THEN 1 ELSE 0 END) AS discounted,
                      SUM(CASE WHEN is_outlet = 1 THEN 1 ELSE 0 END) AS outlet
                 FROM products GROUP BY storefront"""
        ).fetchall()
        return {r["storefront"]: dict(r) for r in rows}
