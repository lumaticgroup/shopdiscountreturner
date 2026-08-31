"""
MarkdownV2 escaping is the single most common way to silently break
Telegram messages. Pin the behavior here.
"""
from formatters import (
    escape_md_v2,
    escape_url,
    format_categories,
    format_product,
    format_product_list,
)


def test_escapes_all_special_chars():
    text = "hello_world *v2* [link](x) . ! ~ ` > # + - = | { } \\"
    out = escape_md_v2(text)
    assert "\\_" in out
    assert "\\*" in out
    assert "\\[" in out
    assert "\\." in out
    assert "\\!" in out


def test_leaves_plain_text_alone():
    assert escape_md_v2("hello world 123") == "hello world 123"


def test_empty_returns_empty():
    assert escape_md_v2("") == ""
    assert escape_md_v2(None) == ""


def test_escape_url_only_touches_paren_and_backslash():
    assert escape_url("https://a.com/x?y=1&z=(2)") == "https://a.com/x?y=1&z=(2\\)"


def _p(**overrides):
    base = {
        "name": "Test Product",
        "url": "https://www.trendyol.com/thing-p-1",
        "brand": "TestBrand",
        "price": 100.0,
        "original_price": 200.0,
        "discount_pct": 50,
        "currency": "TL",
        "category_breadcrumb": "Fashion > Women",
    }
    base.update(overrides)
    return base


def test_format_product_includes_discount_and_price():
    out = format_product(_p(), lang="en")
    assert "\\-50%" in out
    assert "TL" in out
    assert "Fashion" in out
    assert "View on Site" in out


def test_format_product_with_reserved_chars_in_name_does_not_break():
    out = format_product(_p(name="Elbise (siyah). #1!"))
    # Must escape parens, dot, exclamation, hash
    assert "\\(" in out
    assert "\\)" in out
    assert "\\." in out
    assert "\\!" in out
    assert "\\#" in out


def test_format_product_url_with_parens_is_escaped():
    out = format_product(_p(url="https://www.trendyol.com/x?a=(b)"))
    # Must still render a valid link — the closing paren gets escaped
    assert "(https://www.trendyol.com/x?a=(b\\))" in out


def test_format_product_list_empty_returns_msg():
    assert "No discounted" in format_product_list([], lang="en")
    assert "هنوز" in format_product_list([], lang="fa")



def test_format_categories():
    rows = [{"name": "Fashion", "product_count": 12}, {"name": "Home & (garden)", "product_count": 3}]
    out = format_categories(rows)
    assert "Fashion" in out
    assert "12" in out
    assert "\\(" in out  # parens in category name escaped
