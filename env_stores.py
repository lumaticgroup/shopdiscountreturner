"""
`.env`-declared stores. Any env var whose name starts with `STORE_` is
parsed as a JSON spec and upserted into the `dynamic_stores` table at
startup, before the scraper registry is refreshed.

Spec shape (see .env.example for a full example):

    {
      "code": "myshop",
      "display_name": "My Shop",
      "flow_type": "api",              # or "header"
      "base_url": "https://myshop.com",
      "currency": "USD",
      "storefront_code": "myshop_us",
      "config": {                       # goes into dynamic_stores.config_json
        "list_url": "...",
        "response_items_path": "$.items[*]",
        "fields": {"product_id": "$.id", ...},
        ...
      }
    }

Malformed entries are logged and skipped — a bad env var never crashes the
bot.
"""

import json
import logging
import os
from typing import Iterable, Optional

logger = logging.getLogger("env_stores")

REQUIRED_TOP_LEVEL = (
    "code", "display_name", "flow_type", "base_url",
    "currency", "storefront_code", "config",
)
ALLOWED_FLOW_TYPES = {"api", "header"}


def load_env_stores(environ: Optional[dict] = None) -> list[dict]:
    """
    Return one spec dict per valid `STORE_*` env var.

    Each dict is shaped for direct feeding to `db.upsert_dynamic_store`:
        {code, display_name, flow_type, base_url, currency,
         storefront_code, config_json (str)}
    """
    env = environ if environ is not None else os.environ
    specs: list[dict] = []
    for key, value in env.items():
        if not key.startswith("STORE_"):
            continue
        if not value or not value.strip():
            continue
        spec = _parse_one(key, value)
        if spec is not None:
            specs.append(spec)
    return specs


def _parse_one(env_key: str, raw: str) -> Optional[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("%s is not valid JSON — skipping (%s)", env_key, e)
        return None

    if not isinstance(data, dict):
        logger.warning("%s must be a JSON object — skipping", env_key)
        return None

    missing = [k for k in REQUIRED_TOP_LEVEL if k not in data]
    if missing:
        logger.warning(
            "%s missing required key(s) %s — skipping", env_key, missing,
        )
        return None

    flow_type = data["flow_type"]
    if flow_type not in ALLOWED_FLOW_TYPES:
        logger.warning(
            "%s has flow_type=%r; must be one of %s — skipping",
            env_key, flow_type, sorted(ALLOWED_FLOW_TYPES),
        )
        return None

    config = data["config"]
    if not isinstance(config, dict):
        logger.warning("%s: 'config' must be a JSON object — skipping", env_key)
        return None
    if "list_url" not in config or "fields" not in config:
        logger.warning(
            "%s: config must include 'list_url' and 'fields' — skipping",
            env_key,
        )
        return None

    return {
        "code": str(data["code"]),
        "display_name": str(data["display_name"]),
        "flow_type": flow_type,
        "base_url": str(data["base_url"]),
        "currency": str(data["currency"]),
        "storefront_code": str(data["storefront_code"]),
        "config_json": json.dumps(config),
    }


def summarise(specs: Iterable[dict]) -> str:
    codes = sorted(s["code"] for s in specs)
    return f"{len(codes)} store(s): {codes}" if codes else "no env stores"
