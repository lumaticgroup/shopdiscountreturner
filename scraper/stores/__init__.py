"""
Store registry. Auto-discovers every sub-package under `scraper/stores/`
that exposes a module-level `store` attribute (an instance of
`StoreScraper`).

Adding a new store = drop a new folder with an `__init__.py` that ends in
`store = MyStore()`. Nothing else in the codebase changes.

Dynamic stores (declared via `.env` or the admin Mini App) live in the
`dynamic_stores` DB table and are loaded via `refresh_dynamic_stores()` —
they don't have a package on disk. Sub-packages whose name starts with
`_` are treated as private (holding shared implementation, not a store)
and skipped by discovery.
"""
from __future__ import annotations

import importlib
import json
import logging
import pkgutil
from typing import Iterator

from scraper.base import Storefront, StoreScraper

logger = logging.getLogger("scraper.stores")

STORES: dict[str, StoreScraper] = {}
STOREFRONTS: dict[str, Storefront] = {}

# Snapshot of statically-discovered stores. Anything in STORES not in
# _STATIC_CODES was added by `refresh_dynamic_stores`, so we know exactly
# what to drop on the next refresh.
_STATIC_CODES: frozenset[str] = frozenset()
_STATIC_STOREFRONTS: frozenset[str] = frozenset()


def _discover() -> None:
    for mod_info in pkgutil.iter_modules(__path__, prefix=__name__ + "."):
        if not mod_info.ispkg:
            continue
        # Private sub-packages (leading `_`) hold shared implementation, not
        # a store — see `_generic/` for the dynamic-store scrapers.
        leaf = mod_info.name.rsplit(".", 1)[-1]
        if leaf.startswith("_"):
            continue
        module = importlib.import_module(mod_info.name)
        store = getattr(module, "store", None)
        if store is None or not isinstance(store, StoreScraper):
            continue
        if store.code in STORES:
            raise RuntimeError(
                f"Duplicate store code {store.code!r} in {mod_info.name}"
            )
        STORES[store.code] = store
        for sf in store.storefronts:
            if sf.code in STOREFRONTS:
                raise RuntimeError(
                    f"Duplicate storefront code {sf.code!r} "
                    f"(second sighting in store {store.code!r})"
                )
            STOREFRONTS[sf.code] = sf


_discover()
_STATIC_CODES = frozenset(STORES.keys())
_STATIC_STOREFRONTS = frozenset(STOREFRONTS.keys())


def refresh_dynamic_stores() -> int:
    """
    Re-read `dynamic_stores` from the DB and rebuild the generic-store
    entries in STORES/STOREFRONTS. Static stores are untouched.

    Returns the number of dynamic stores loaded. Called at startup after
    `db.init_db()` and by the admin `POST /api/admin/reload` endpoint.
    """
    import db  # local import: avoids circular at module-load time
    from ._generic.api import GenericAPIStore
    from ._generic.header import GenericHeaderStore

    # Drop everything previously loaded dynamically, keeping static entries.
    for code in list(STORES.keys()):
        if code in _STATIC_CODES:
            continue
        store = STORES.pop(code)
        for sf in store.storefronts:
            STOREFRONTS.pop(sf.code, None)

    rows = db.list_dynamic_stores(enabled_only=True)
    loaded = 0

    for row in rows:
        code = row["code"]
        if code in _STATIC_CODES:
            logger.warning(
                "Dynamic store %r clashes with a static store — skipping",
                code,
            )
            continue

        try:
            config = json.loads(row["config_json"] or "{}")
        except json.JSONDecodeError as e:
            logger.warning(
                "Dynamic store %r: bad config_json (%s) — skipping",
                code, e,
            )
            continue

        cls = GenericHeaderStore if row["flow_type"] == "header" else GenericAPIStore
        try:
            store = cls(
                code=code,
                display_name=row["display_name"],
                storefront_code=row["storefront_code"],
                currency=row["currency"],
                base_url=row["base_url"],
                config=config,
            )
        except Exception:
            logger.exception("Dynamic store %r: failed to instantiate", code)
            continue

        sf_code = store.storefronts[0].code
        if sf_code in _STATIC_STOREFRONTS:
            logger.warning(
                "Dynamic store %r: storefront %r clashes with a static one — skipping",
                code, sf_code,
            )
            continue

        STORES[code] = store
        STOREFRONTS[sf_code] = store.storefronts[0]
        loaded += 1

    logger.info("Dynamic store refresh: %d loaded", loaded)
    return loaded


def get_store(code: str) -> StoreScraper:
    if code not in STORES:
        raise ValueError(f"Unknown store '{code}'. Known: {list(STORES)}")
    return STORES[code]


def get_storefront(code: str) -> Storefront:
    if code not in STOREFRONTS:
        raise ValueError(
            f"Unknown storefront '{code}'. Known: {list(STOREFRONTS)}"
        )
    return STOREFRONTS[code]


def store_for_storefront(sf_code: str) -> StoreScraper:
    sf = get_storefront(sf_code)
    return get_store(sf.store_code)


def all_storefronts() -> Iterator[Storefront]:
    return iter(STOREFRONTS.values())


__all__ = [
    "STORES",
    "STOREFRONTS",
    "get_store",
    "get_storefront",
    "store_for_storefront",
    "all_storefronts",
    "refresh_dynamic_stores",
]
