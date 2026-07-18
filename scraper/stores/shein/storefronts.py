"""
Shein's country variants. Shein geo-splits by HOST, not path: tr.shein.com
(Turkey, TRY), ar.shein.com (Gulf — defaults to SAR, serves UAE in AED via
the `currency` cookie), us.shein.com (USD), etc.

The `currency` cookie is the only per-request pin we need, but Shein's
edge rewrites it based on IP just like Trendyol rewrites `storefrontId`,
so the store re-pins it before every listing navigation.
"""
from dataclasses import dataclass

from scraper.base import Storefront

_STORE_CODE = "shein"


@dataclass(frozen=True)
class SheinStorefront(Storefront):
    base_url: str = "https://tr.shein.com"
    # Value for Shein's `currency` cookie. May differ from the display
    # currency: the TR storefront shows "TL" (matching the trendyol_tr
    # storefront convention) but Shein's cookie wants ISO "TRY".
    currency_cookie: str = "TRY"
    language: str = "tr"

    def cookies(self) -> list[dict]:
        return [
            {"name": "currency", "value": self.currency_cookie,
             "domain": ".shein.com", "path": "/"},
            {"name": "language", "value": self.language,
             "domain": ".shein.com", "path": "/"},
        ]


STOREFRONTS: dict[str, SheinStorefront] = {
    "shein_tr": SheinStorefront(
        code="shein_tr",
        display_name="Shein Turkey",
        currency="TL",
        store_code=_STORE_CODE,
        base_url="https://tr.shein.com",
        currency_cookie="TRY",
        language="tr",
    ),
    "shein_uae": SheinStorefront(
        code="shein_uae",
        display_name="Shein UAE",
        currency="AED",
        store_code=_STORE_CODE,
        base_url="https://ar.shein.com",
        currency_cookie="AED",
        language="en",
    ),
}


def get(code: str) -> SheinStorefront:
    if code not in STOREFRONTS:
        raise ValueError(f"Unknown Shein storefront '{code}'. Known: {list(STOREFRONTS)}")
    return STOREFRONTS[code]
