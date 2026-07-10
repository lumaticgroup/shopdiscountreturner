"""
Password hashing, JWT signing/verification, and Telegram WebApp initData
HMAC validation.

Design choices:
  - bcrypt via passlib (safer than SHA + salt, well-vetted).
  - JWT HS256 signed with WEBAPP_SECRET. Every token has a `jti` also written
    to the `sessions` table so we can revoke server-side without waiting for
    expiry.
  - Telegram initData follows the official spec at
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    — HMAC_SHA256 with a secret derived from the bot token; verify signature
    matches the `hash` param.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl

import jwt
from passlib.hash import bcrypt

import config


# ---------- password hashing ----------

def hash_password(plain: str) -> str:
    return bcrypt.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.verify(plain, hashed)
    except Exception:
        return False


# ---------- JWT ----------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def issue_jwt(user_id: int, role: str) -> tuple[str, str, datetime]:
    """
    Returns (token, jti, expires_at). Caller writes the jti to the sessions
    table so it can be revoked.
    """
    if not config.WEBAPP_SECRET:
        raise RuntimeError(
            "WEBAPP_SECRET is unset — cannot sign JWTs. Set it in .env."
        )
    jti = secrets.token_urlsafe(24)
    now = _now_utc()
    expires_at = now + timedelta(hours=config.SESSION_TTL_HOURS)
    payload = {
        "sub": str(user_id),
        "role": role,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, config.WEBAPP_SECRET, algorithm="HS256")
    return token, jti, expires_at


def decode_jwt(token: str) -> Optional[dict]:
    """
    Returns the payload dict on success, None on invalid/expired.
    Does NOT check server-side revocation — that's the deps layer's job.
    """
    if not config.WEBAPP_SECRET:
        return None
    try:
        return jwt.decode(token, config.WEBAPP_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ---------- Telegram WebApp initData verification ----------

def verify_init_data(init_data_raw: str, max_age_seconds: int = 24 * 3600) -> Optional[dict]:
    """
    Verify an initData string coming from Telegram.WebApp.initData.

    Steps per spec:
      1. Parse as querystring, extract `hash` field.
      2. Sort remaining k=v pairs alphabetically, join with '\n'.
      3. secret = HMAC_SHA256(key=b"WebAppData", msg=bot_token)
      4. computed = HMAC_SHA256(key=secret, msg=data_check_string).hexdigest()
      5. Compare with received hash (constant-time).
      6. Reject if auth_date is older than max_age_seconds.

    Returns a dict of the parsed fields (including 'user' JSON-decoded) on
    success, or None on any failure. Never raises.
    """
    if not init_data_raw or not config.TELEGRAM_BOT_TOKEN:
        return None

    try:
        pairs = dict(parse_qsl(init_data_raw, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(
        b"WebAppData",
        config.TELEGRAM_BOT_TOKEN.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    computed = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        return None

    auth_date_str = pairs.get("auth_date")
    if auth_date_str:
        try:
            auth_date = int(auth_date_str)
            if time.time() - auth_date > max_age_seconds:
                return None
        except ValueError:
            return None

    # Parse `user` — it's a JSON string per spec.
    import json
    if "user" in pairs:
        try:
            pairs["user"] = json.loads(pairs["user"])
        except json.JSONDecodeError:
            return None
    return pairs


def chat_id_from_init_data(parsed: dict) -> Optional[int]:
    """Convenience: extract the Telegram user id from verified initData."""
    user = parsed.get("user")
    if isinstance(user, dict) and "id" in user:
        try:
            return int(user["id"])
        except (TypeError, ValueError):
            return None
    return None


def _placeholder_used(_: Any) -> None:
    """Reserved for future use; keeps linter quiet."""
