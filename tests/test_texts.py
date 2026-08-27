"""bot/texts.py — HTML xavfsizligi va karta matni.

Nega HTML escaping alohida tekshiriladi: postavshik nomlarida `&`, `<` kabi
belgilar uchrasa Telegram butun xabarni rad etadi va karta umuman ochilmaydi.
"""

from __future__ import annotations

import pytest

from povtor_bot.bot import texts
from povtor_bot.core.models import STATUS_NOT_FOUND, STATUS_PENDING, STATUS_TAKEN


def row(**overrides) -> dict:
    base = {
        "sku": "39666", "color": "Белый", "shop_name": "ANDALUS",
        "product_name": "Рубашка с дл/р", "subcategory": "Рубашка с дл/р",
        "supplier": "ABUSAXIY 8-22 M64", "price_uzs": 145000,
        "base_qty": 5, "sold_qty": 4, "percent": 80.0, "grade": "ishonchli",
        "recommended_qty": 10, "status": STATUS_PENDING, "transfer_hint": "",
    }
    base.update(overrides)
    return base


class TestEscaping:
    def test_ampersand_and_tags_are_escaped(self) -> None:
        text = texts.card_caption([row(supplier="A & B <script>")])
        assert "&amp;" in text and "&lt;script&gt;" in text
        assert "<script>" not in text

    def test_markdown_characters_pass_through_safely(self) -> None:
        """`_`, `-`, `*` HTML rejimida oddiy belgi — hech nima buzilmaydi."""
        text = texts.card_caption([row(supplier="ABUSAXIY 8-22 M64")])
        assert "ABUSAXIY 8-22 M64" in text

    def test_none_becomes_empty(self) -> None:
        assert texts.esc(None) == ""


class TestMoney:
    @pytest.mark.parametrize("value, expected", [
        (145000, "145 000 so'm"),
        (1_250_000, "1 250 000 so'm"),
        (0, "—"),
    ])
    def test_formatting(self, value: int, expected: str) -> None:
        assert texts.money(value) == expected


class TestCardCaption:
    def test_shows_price_in_uzs(self) -> None:
        assert "145 000 so'm" in texts.card_caption([row()])

    def test_lists_every_shop_and_color(self) -> None:
        text = texts.card_caption([
            row(shop_name="ANDALUS", color="Белый"),
            row(shop_name="BERUNIY", color="Синий"),
        ])
        assert "ANDALUS" in text and "BERUNIY" in text
        assert "Белый" in text and "Синий" in text

    def test_pending_item_has_no_answer_line(self) -> None:
        assert "OLINDI" not in texts.card_caption([row()])

    def test_answered_item_shows_status(self) -> None:
        text = texts.card_caption([row(status=STATUS_TAKEN)])
        assert "OLINDI" in text and "✅" in text

    def test_not_found_shows_transfer_hint(self) -> None:
        text = texts.card_caption([
            row(status=STATUS_NOT_FOUND, transfer_hint="Transfer qilsa bo'ladi — BERUNIY: 4 dona")
        ])
        assert "BOZORDA YO'Q" in text and "BERUNIY: 4 dona" in text

    def test_percent_has_no_trailing_zero(self) -> None:
        assert "(80%)" in texts.card_caption([row(percent=80.0)])
        assert "(66.7%)" in texts.card_caption([row(percent=66.7)])

    def test_empty_rows(self) -> None:
        assert texts.card_caption([]) == "Band topilmadi."


class TestArrivalMessage:
    def test_lists_shops_with_quantities(self) -> None:
        entry = {
            "sku": "40595", "color": "Т.Синий", "name": "Американка",
            "supplier": "Sardor M193", "shops": [("ANDALUS", 6), ("BERUNIY", 4)],
        }
        text = texts.arrival_message(entry, price_uzs=95000)
        assert "40595" in text and "Американка" in text
        assert "ANDALUS" in text and "6 dona" in text
        assert "BERUNIY" in text and "4 dona" in text
        assert "95 000 so'm" in text

    def test_missing_price_shows_dash(self) -> None:
        text = texts.arrival_message({"sku": "1", "shops": []}, price_uzs=0)
        assert "—" in text


class TestTransferHint:
    def test_joins_shops(self) -> None:
        rows = [
            {"shop_name": "MAGNIT", "quantity": 9},
            {"shop_name": "BERUNIY", "quantity": 4},
        ]
        assert texts.transfer_hint_text(rows) == (
            "Transfer qilsa bo'ladi — MAGNIT: 9 dona, BERUNIY: 4 dona"
        )

    def test_empty_when_nobody_has_it(self) -> None:
        assert texts.transfer_hint_text([]) == ""


class TestCheckReport:
    class Result:
        def __init__(self, **kw) -> None:
            self.__dict__.update(
                {"ok": True, "total_found": 0, "new_count": 0, "stock_rows": 0,
                 "usd_rate": 0.0, "error": ""} | kw
            )

    def test_error_is_shown(self) -> None:
        text = texts.check_report(self.Result(ok=False, error="Billz javob bermadi"))
        assert "Billz javob bermadi" in text and "⚠️" in text

    def test_success_shows_counts(self) -> None:
        text = texts.check_report(
            self.Result(total_found=128, new_count=12, stock_rows=900, usd_rate=12800)
        )
        assert "128" in text and "12" in text and "12800" in text

    def test_no_new_candidates_explains_why(self) -> None:
        text = texts.check_report(self.Result(total_found=5, new_count=0))
        assert "Yangi nomzod yo'q" in text
