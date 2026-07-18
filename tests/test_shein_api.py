"""
Unit tests for the Shein item → ScrapedProduct mapper. Fixture items mirror
the gbRawData goods shape; refresh them from a `scripts.inspect_page
--storefront shein_tr` key dump when Shein's schema drifts.
"""
from scraper.stores.shein.api import _to_product
from scraper.stores.shein.storefronts import STOREFRONTS

SF_TR = STOREFRONTS["shein_tr"]
SF_UAE = STOREFRONTS["shein_uae"]


def _item(**overrides) -> dict:
    base = {
        "goods_id": "12345678",
        "goods_sn": "sw2207154623331050",
        "goods_name": "Solid Ribbed Knit Crop Tee",
        "goods_url_name": "Solid Ribbed Knit Crop Tee",
        "cat_id": "1738",
        "goods_img": "//img.ltwebstatic.com/images3_pi/2022/07/15/xyz.jpg",
        "brand_name": "SHEIN BASICS",
        "retailPrice": {"amount": "199.00", "amountWithSymbol": "₺199.00"},
        "salePrice": {"amount": "99.50", "amountWithSymbol": "₺99.50"},
        "unit_discount": 50,
    }
    base.update(overrides)
    return base


def test_maps_discounted_item():
    p = _to_product(_item(), SF_TR)
    assert p is not None
    assert p.product_id == "12345678"
    assert p.storefront == "shein_tr"
    assert p.price == 99.50
    assert p.original_price == 199.00
    assert p.discount_pct == 50
    assert p.currency == "TL"
    assert p.brand == "SHEIN BASICS"


def test_uae_storefront_currency():
    p = _to_product(_item(), SF_UAE)
    assert p.storefront == "shein_uae"
    assert p.currency == "AED"


def test_builds_detail_url_from_slug_and_ids():
    p = _to_product(_item(), SF_TR)
    assert p.url == "https://tr.shein.com/Solid-Ribbed-Knit-Crop-Tee-p-12345678-cat-1738.html"


def test_detail_url_without_cat_id():
    p = _to_product(_item(cat_id=None), SF_TR)
    assert p.url == "https://tr.shein.com/Solid-Ribbed-Knit-Crop-Tee-p-12345678.html"


def test_absolutises_protocol_relative_image():
    p = _to_product(_item(), SF_TR)
    assert p.image_url == "https://img.ltwebstatic.com/images3_pi/2022/07/15/xyz.jpg"


def test_missing_image_falls_back_to_none():
    p = _to_product(_item(goods_img=None), SF_TR)
    assert p is not None
    assert p.image_url is None


def test_computes_discount_when_unit_discount_missing():
    p = _to_product(_item(unit_discount=None), SF_TR)
    assert p.discount_pct == 50


def test_reconstructs_was_price_from_discount():
    # Some payloads carry retail == sale but still flag the markdown.
    p = _to_product(
        _item(retailPrice={"amount": "99.50"}, unit_discount=50), SF_TR,
    )
    assert p is not None
    assert p.original_price == 199.00


def test_drops_undiscounted_item():
    item = _item(
        retailPrice={"amount": "99.50"},
        salePrice={"amount": "99.50"},
        unit_discount=0,
    )
    assert _to_product(item, SF_TR) is None


def test_drops_item_without_id_or_name():
    assert _to_product(_item(goods_id=None, goods_sn=None), SF_TR) is None
    assert _to_product(_item(goods_name=None), SF_TR) is None
