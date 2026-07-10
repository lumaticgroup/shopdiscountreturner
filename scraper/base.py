"""
Multi-store scraper contract.

Every supported store lives under `scraper/stores/<store_code>/` and exposes
a subclass of `StoreScraper` at module-level. The registry in
`scraper.stores` auto-discovers them.

A `Storefront` here is the generic view every store must present — a country
variant with its own code, display name, currency, and the owning store's
code. Stores are free to subclass it with vendor-specific fields (see
`scraper/stores/trendyol/storefronts.py: TrendyolStorefront`).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class Storefront:
    code: str            # unique across ALL stores: "tr", "gulf", "amazon_de", …
    display_name: str
    currency: str
    store_code: str      # points at owning StoreScraper.code


class StoreScraper(ABC):
    code: ClassVar[str]              # "trendyol", "amazon", …
    display_name: ClassVar[str]

    @property
    @abstractmethod
    def storefronts(self) -> list[Storefront]:
        """Every country variant this store supports."""

    @abstractmethod
    async def scrape(
        self,
        storefront_code: str,
        *,
        max_pages_per_category: int,
        concurrency: int,
        dry_run: bool = False,
    ) -> dict:
        """
        Full scrape of one storefront. Returns a summary dict shaped:
            {"categories": int, "products": int, "dry_run": bool, ...}
        """
