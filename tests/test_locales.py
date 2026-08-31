"""
Tests for centralized bilingual localization (locales.py), formatters, and DB language persistence.
"""
from locales import t, STRINGS
import formatters
import db


def test_locales_keys_exist_for_both_fa_and_en():
    for key, trans in STRINGS.items():
        assert "fa" in trans, f"Missing Persian (fa) translation for key '{key}'"
        assert "en" in trans, f"Missing English (en) translation for key '{key}'"
        assert len(trans["fa"].strip()) > 0
        assert len(trans["en"].strip()) > 0


def test_t_function_retrieval_and_formatting():
    # Test simple key
    assert t("btn_top_deals", "fa") == "🔥 تخفیف‌های داغ"
    assert t("btn_top_deals", "en") == "🔥 Top Deals"

    # Test formatted parameters
    fa_welcome = t("welcome_header", "fa", name="مهدی")
    assert "مهدی" in fa_welcome

    en_welcome = t("welcome_header", "en", name="Mahdi")
    assert "Mahdi" in en_welcome


def test_format_product_persian_and_english():
    prod = {
        "name": "پیراهن زنانه",
        "url": "https://example.com/p/1",
        "brand": "Zara",
        "price": 100.0,
        "original_price": 200.0,
        "discount_pct": 50,
        "currency": "TL",
        "category_breadcrumb": "پوشاک > زنانه",
    }
    out_fa = formatters.format_product(prod, lang="fa")
    assert "مشاهده در سایت" in out_fa

    out_en = formatters.format_product(prod, lang="en")
    assert "View on Site" in out_en


def test_db_language_preference():
    db.init_db()
    test_chat_id = 999888777
    
    # Set Persian
    db.set_language_pref(test_chat_id, "fa")
    assert db.get_language_pref(test_chat_id) == "fa"

    # Switch to English
    db.set_language_pref(test_chat_id, "en")
    assert db.get_language_pref(test_chat_id) == "en"
