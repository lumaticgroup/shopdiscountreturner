"""
Trendyol's country variants. Drives Trendyol's international API by
sending a specific `storefrontId` (both as a cookie and as a query param),
which selects the country's inventory + currency regardless of the
request's source IP. See README for the country ID map.
"""
from dataclasses import dataclass

from scraper.base import Storefront

_STORE_CODE = "trendyol"


@dataclass(frozen=True)
class TrendyolStorefront(Storefront):
    base_url: str = "https://www.trendyol.com"
    home_path: str = ""            # "" for TR, "/en" for UAE
    storefront_id: int = 1         # 1=TR, 36=UAE, 35=SA
    country_code: str = "TR"
    language: str = "tr"
    culture: str = "tr-TR"         # passed as query param to the API


STOREFRONTS: dict[str, TrendyolStorefront] = {
    "trendyol_tr": TrendyolStorefront(
        code="trendyol_tr",
        display_name="Trendyol Turkey",
        currency="TL",
        store_code=_STORE_CODE,
        base_url="https://www.trendyol.com",
        home_path="",
        storefront_id=1,
        country_code="TR",
        language="tr",
        culture="tr-TR",
    ),
    "trendyol_uae": TrendyolStorefront(
        code="trendyol_uae",
        display_name="Trendyol UAE",
        currency="AED",
        store_code=_STORE_CODE,
        base_url="https://www.trendyol.com",
        home_path="/en",
        storefront_id=36,
        country_code="AE",
        language="en",
        culture="en-AE",
    ),
}

# Pre-multi-store codes still present in old DBs and .env files. init_db()
# rewrites DB rows via this map; get() accepts them for stray callers.
LEGACY_CODES: dict[str, str] = {
    "tr": "trendyol_tr",
    "gulf": "trendyol_uae",
}


def get(code: str) -> TrendyolStorefront:
    code = LEGACY_CODES.get(code, code)
    if code not in STOREFRONTS:
        raise ValueError(f"Unknown Trendyol storefront '{code}'. Known: {list(STOREFRONTS)}")
    return STOREFRONTS[code]
