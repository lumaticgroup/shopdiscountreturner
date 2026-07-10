"""
Bootstrap the first admin.

Two ways to use this:
  1. Set BOOTSTRAP_ADMIN_EMAIL/PASSWORD in .env — bot.py will seed on startup.
  2. Run standalone: `python -m scripts.bootstrap_admin`.

Idempotent: does nothing if an admin already exists, or if the email is
already taken. Prints what it did (or didn't).
"""
from __future__ import annotations

import logging
import sys

from passlib.hash import bcrypt

import config
import db

logger = logging.getLogger("bootstrap_admin")


def bootstrap_admin_if_needed() -> str:
    """
    Returns one of: 'created', 'admin_exists', 'skipped_no_env',
    'email_taken'. Safe to call every startup.
    """
    if not config.BOOTSTRAP_ADMIN_EMAIL or not config.BOOTSTRAP_ADMIN_PASSWORD:
        return "skipped_no_env"

    if db.count_admins() > 0:
        return "admin_exists"

    existing = db.get_user_by_email(config.BOOTSTRAP_ADMIN_EMAIL)
    if existing:
        # Someone signed up with that email as a customer first — promote.
        if existing["role"] != "admin":
            db.set_user_role(existing["id"], "admin")
            return "created"
        return "admin_exists"

    pwd_hash = bcrypt.hash(config.BOOTSTRAP_ADMIN_PASSWORD)
    db.create_user(config.BOOTSTRAP_ADMIN_EMAIL, pwd_hash, role="admin")
    return "created"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_db()
    result = bootstrap_admin_if_needed()

    if result == "created":
        print(f"OK — admin {config.BOOTSTRAP_ADMIN_EMAIL!r} created.")
    elif result == "admin_exists":
        print("OK — an admin already exists, nothing to do.")
    elif result == "email_taken":
        print(
            f"ERROR — email {config.BOOTSTRAP_ADMIN_EMAIL!r} is already used "
            "by a non-admin account. Promote them manually with SQL or a "
            "future /api/admin/users route.",
            file=sys.stderr,
        )
        sys.exit(1)
    elif result == "skipped_no_env":
        print(
            "SKIPPED — set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD "
            "in .env first."
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
