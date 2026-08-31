"""
Bootstrap the first admin.

Two ways to use this:
  1. Set BOOTSTRAP_ADMIN_EMAIL/PASSWORD in .env — bot.py will seed on startup.
  2. Run standalone: `python -m scripts.bootstrap_admin`.

Idempotent: does nothing if an admin already exists, or if the email is
already taken. Prints what it did (or didn't).
"""

import logging
import sys

from passlib.hash import bcrypt

import config
import db

logger = logging.getLogger("bootstrap_admin")


def bootstrap_admin_if_needed() -> str:
    """
    Ensure the env-declared admin exists AND matches the env password.

    Env is the source of truth: on every startup we either create the admin
    or re-sync its password/role from BOOTSTRAP_ADMIN_PASSWORD. This lets an
    operator recover a forgotten password by changing the env var and
    restarting, without touching the DB.

    Returns one of: 'created', 'password_synced', 'up_to_date',
    'skipped_no_env'.
    """
    if not config.BOOTSTRAP_ADMIN_EMAIL or not config.BOOTSTRAP_ADMIN_PASSWORD:
        return "skipped_no_env"

    existing = db.get_user_by_email(config.BOOTSTRAP_ADMIN_EMAIL)
    pwd_hash = bcrypt.hash(config.BOOTSTRAP_ADMIN_PASSWORD)

    if existing is None:
        db.create_user(config.BOOTSTRAP_ADMIN_EMAIL, pwd_hash, role="admin")
        return "created"

    changed = False
    if existing["role"] != "admin":
        db.set_user_role(existing["id"], "admin")
        changed = True
    if not bcrypt.verify(config.BOOTSTRAP_ADMIN_PASSWORD, existing["password_hash"]):
        db.set_user_password(existing["id"], pwd_hash)
        changed = True

    return "password_synced" if changed else "up_to_date"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_db()
    result = bootstrap_admin_if_needed()

    if result == "created":
        print(f"OK — admin {config.BOOTSTRAP_ADMIN_EMAIL!r} created.")
    elif result == "password_synced":
        print(f"OK — admin {config.BOOTSTRAP_ADMIN_EMAIL!r} re-synced from env.")
    elif result == "up_to_date":
        print("OK — admin already in sync with env, nothing to do.")
    elif result == "skipped_no_env":
        print(
            "SKIPPED — set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD "
            "in .env first."
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
