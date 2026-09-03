"""bot/texts.py — HTML xavfsizligi va karta matni.

Nega HTML escaping alohida tekshiriladi: postavshik nomlarida `&`, `<` kabi
belgilar uchrasa Telegram butun xabarni rad etadi va karta umuman ochilmaydi.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from povtor_bot.bot import texts
from povtor_bot.core.models import STATUS_NOT_FOUND, STATUS_PENDING, STATUS_TAKEN


TODAY = date(2026, 8, 27)


def row(**overrides) -> dict:
    base = {
        "detected_date": TODAY.isoformat(),
        "arrived_date": (TODAY - timedelta(days=2)).isoformat(),
        "sku": "39666", "color": "Белый", "shop_name": "ANDALUS",
        "product_name": "Рубашка с дл/р", "subcategory": "Рубашка с дл/р",
        "supplier": "ABUSAXIY 8-22 M64", "price_uzs": 145000,
        "base_qty": 5, "sold_qty": 4, "percent": 80.0, "grade": "ishonchli",
        "days_to_50": 2, "superseded_at": None, "window_days": 5,
        "kind": "Однотонный", "brand": "Millionaire", "material": "Хлопок",
        "recommended_qty": 10, "status": STATUS_PENDING, "transfer_hint": "",
    }
    base.update(overrides)
    return base


class TestCardHeader:
    """Sarlavha ikkita bir xil nomli artikulni ajrata olishi kerak.

    Billz'da `product_name` model nomi emas, tur nomi: 956 artikulga atigi
    84 xil nom to'g'ri keladi. Brendsiz Salvatini va Loro Piana kedilari
    kartada bir xil ko'rinardi.
    """

    def test_brand_follows_the_name(self) -> None:
        text = texts.card_caption(
            [row(product_name="Кеды-Casual", brand="Salvatini")], today=TODAY
        )
        assert "Кеды-Casual · Salvatini" in text

    def test_two_articles_differ_by_brand(self) -> None:
        bittasi = texts.card_caption(
            [row(sku="50058", product_name="Кеды-Casual", brand="Salvatini")], today=TODAY
        )
        boshqasi = texts.card_caption(
            [row(sku="50051", product_name="Кеды-Casual", brand="Loro Piana")], today=TODAY
        )
        assert bittasi.splitlines()[0] != boshqasi.splitlines()[0]

    def test_no_brand_leaves_the_name_alone(self) -> None:
        text = texts.card_caption([row(product_name="Кеды-Casual", brand="")], today=TODAY)
        assert "<b>Кеды-Casual</b>" in text

    def test_kind_and_material_line(self) -> None:
        text = texts.card_caption(
            [row(kind="Шнурок", material="Комбинация · Замш/Кожа")], today=TODAY
        )
        assert "Tur: Шнурок · Комбинация · Замш/Кожа" in text

    def test_kind_alone(self) -> None:
        text = texts.card_caption([row(kind="Шнурок", material="")], today=TODAY)
        assert "Tur: Шнурок" in text

    def test_no_kind_no_material_no_line(self) -> None:
        text = texts.card_caption([row(kind="", material="")], today=TODAY)
        assert "Tur:" not in text

    def test_subcategory_equal_to_name_is_dropped(self) -> None:
        """Podkategoriya nom bilan aynan bir xil bo'lsa takrorlash foydasiz."""
        text = texts.card_caption(
            [row(product_name="Кеды-Casual", subcategory="Кеды-Casual")], today=TODAY
        )
        assert "Bo'lim:" not in text

    def test_subcategory_shown_when_it_differs(self) -> None:
        text = texts.card_caption(
            [row(product_name="Рубашка класс.дл/р", subcategory="Рубашка с дл/р")],
            today=TODAY,
        )
        assert "Bo'lim: Рубашка с дл/р" in text

    def test_missing_columns_do_not_break_old_rows(self) -> None:
        """Migratsiyagacha yozilgan qatorlarda brand/material ustuni yo'q."""
        eski = row()
        eski.pop("brand", None)
        eski.pop("material", None)
        eski.pop("kind", None)
        text = texts.card_caption([eski], today=TODAY)
        assert "39666" in text and "Tur:" not in text

    def test_brand_is_escaped(self) -> None:
        text = texts.card_caption([row(brand="A & B")], today=TODAY)
        assert "A &amp; B" in text


class TestEscaping:
    def test_ampersand_and_tags_are_escaped(self) -> None:
        text = texts.card_caption([row(supplier="A & B <script>")], today=TODAY)
        assert "&amp;" in text and "&lt;script&gt;" in text
        assert "<script>" not in text

    def test_markdown_characters_pass_through_safely(self) -> None:
        """`_`, `-`, `*` HTML rejimida oddiy belgi — hech nima buzilmaydi."""
        text = texts.card_caption([row(supplier="ABUSAXIY 8-22 M64")], today=TODAY)
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
        assert "145 000 so'm" in texts.card_caption([row()], today=TODAY)

    def test_lists_every_shop_and_color(self) -> None:
        text = texts.card_caption([
            row(shop_name="ANDALUS", color="Белый"),
            row(shop_name="BERUNIY", color="Синий"),
        ], today=TODAY)
        assert "ANDALUS" in text and "BERUNIY" in text
        assert "Белый" in text and "Синий" in text

    def test_pending_item_has_no_answer_line(self) -> None:
        assert "OLINDI" not in texts.card_caption([row()], today=TODAY)

    def test_answered_item_shows_status(self) -> None:
        text = texts.card_caption([row(status=STATUS_TAKEN)], today=TODAY)
        assert "OLINDI" in text and "✅" in text

    def test_not_found_shows_transfer_hint(self) -> None:
        text = texts.card_caption([
            row(status=STATUS_NOT_FOUND, transfer_hint="Transfer qilsa bo'ladi — BERUNIY: 4 dona")
        ], today=TODAY)
        assert "BOZORDA YO'Q" in text and "BERUNIY: 4 dona" in text

    def test_percent_has_no_trailing_zero(self) -> None:
        assert "(80%)" in texts.card_caption([row(percent=80.0)], today=TODAY)
        assert "(66.7%)" in texts.card_caption([row(percent=66.7)], today=TODAY)

    def test_shows_arrival_date_and_speed(self) -> None:
        """Buyer bozorda: qachon kelgan, qanchasi ketgan, qanchalik tez."""
        text = texts.card_caption(
            [row(arrived_date="2026-08-22", base_qty=5, sold_qty=4,
                 percent=80.0, days_to_50=2)],
            today=TODAY,
        )
        assert "22-avg keldi: 5 dona" in text     # qachon va nechta keldi
        assert "5 kunda 4 sotildi" in text        # qancha vaqtda qancha ketdi
        assert "(80%)" in text
        assert "50%ga 2-kunda" in text            # tezlik

    def test_same_day_arrival(self) -> None:
        text = texts.card_caption(
            [row(arrived_date=TODAY.isoformat())], today=TODAY
        )
        assert "shu kuni" in text

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


class TestAgeLabel:
    """Bandning yoshi.

    Javob berilmagan band menyuda TURAVERADI — ertaga ham, bir hafta keyin
    ham. Menejer uning eskiligini ko'rib turishi kerak: eski band endi
    dolzarb bo'lmasligi mumkin.
    """

    @pytest.mark.parametrize(
        "detected, expected",
        [
            ("2026-08-27", ""),                # bugun — hech nima yozilmaydi
            ("2026-08-26", "kecha"),
            ("2026-08-25", "2 kun oldin"),
            ("2026-08-22", "5 kun oldin"),
        ],
    )
    def test_label(self, detected: str, expected: str) -> None:
        assert texts.age_label(detected, TODAY) == expected

    def test_stale_is_decided_by_ARRIVAL_not_detection(self) -> None:
        """Statistika partiya oynadan chiqqanda muzlaydi, aniqlangan kunda emas.

        Band KECHA aniqlangan bo'lishi, lekin partiyasi bir hafta oldin
        kelgan bo'lishi mumkin — uning raqamlari allaqachon eskirgan.
        Real holat: 104 banddan 82 tasi aynan shunday edi.
        """
        # kecha aniqlangan, lekin partiya 6 kunlik -> ESKIRGAN.
        # Kun soni takrorlanmaydi — kelgan sana kartada allaqachon bor.
        assert texts.age_label(
            "2026-08-26", TODAY, stale_after_days=5, arrived="2026-08-21"
        ) == "⚠️ eskirgan"
        # kecha aniqlangan, partiya 2 kunlik -> toza
        assert texts.age_label(
            "2026-08-26", TODAY, stale_after_days=5, arrived="2026-08-25"
        ) == "kecha"

    def test_today_but_stale_still_warns(self) -> None:
        assert texts.age_label(
            "2026-08-27", TODAY, stale_after_days=5, arrived="2026-08-20"
        ) == "⚠️ eskirgan"

    def test_no_flag_without_threshold(self) -> None:
        assert texts.age_label("2026-08-01", TODAY) == "26 kun oldin"
        assert texts.age_label("2026-08-01", TODAY, arrived="2026-01-01") == "26 kun oldin"

    def test_future_or_broken_date(self) -> None:
        assert texts.age_label("2026-09-01", TODAY) == ""
        assert texts.age_label("", TODAY) == ""
        assert texts.age_label("aniqmas", TODAY) == ""
        # buzilgan arrived ogohlantirish bermaydi, lekin yiqilmaydi ham
        assert texts.age_label(
            "2026-08-25", TODAY, stale_after_days=5, arrived="aniqmas"
        ) == "2 kun oldin"

    def test_card_shows_age_only_for_old_items(self) -> None:
        text = texts.card_caption([
            row(shop_name="ANDALUS", detected_date="2026-08-27"),
            row(shop_name="BERUNIY", detected_date="2026-08-24"),
        ], today=TODAY)
        andalus, beruniy = text.split("ANDALUS")[1], text.split("BERUNIY")[1]
        assert "kun oldin" not in andalus.split("BERUNIY")[0]   # bugungi — toza
        assert "3 kun oldin" in beruniy                          # eski — belgilangan

    def test_card_flags_stale_item(self) -> None:
        text = texts.card_caption(
            [row(detected_date="2026-08-26", arrived_date="2026-08-19")],
            today=TODAY, stale_after_days=5,
        )
        assert "⚠️" in text and "eskirgan" in text



class TestStaleUsesTheRowWindow:
    """"Eskirgan" belgisi bandning O'Z oynasiga solishtiriladi.

    Menejer 10 kunlik tekshiruv qilsa, 7 kunlik band eskirgan EMAS — u aynan
    shu oyna uchun topilgan. Umumiy sozlamaga (5 kun) solishtirish uni
    noto'g'ri belgilagan bo'lardi.
    """

    def test_wide_window_row_is_not_stale(self) -> None:
        matn = texts.card_caption(
            [row(arrived_date="2026-08-20", window_days=10)],
            today=TODAY, stale_after_days=5,
        )
        assert "⚠️" not in matn

    def test_narrow_window_row_is_stale(self) -> None:
        matn = texts.card_caption(
            [row(arrived_date="2026-08-20", window_days=3)],
            today=TODAY, stale_after_days=5,
        )
        assert "⚠️ eskirgan" in matn

    def test_missing_column_falls_back(self) -> None:
        """Migratsiyadan oldingi qatorlarda ustun bo'lmasligi mumkin."""
        eski = row(arrived_date="2026-08-20")
        eski.pop("window_days", None)
        assert "⚠️ eskirgan" in texts.card_caption(
            [eski], today=TODAY, stale_after_days=5
        )


class TestReportShowsTheRule:
    class Result:
        def __init__(self, **kw) -> None:
            self.__dict__.update(
                {"ok": True, "total_found": 5, "new_count": 1, "stock_rows": 0,
                 "usd_rate": 0.0, "error": ""} | kw
            )

    def test_rule_is_shown_when_given(self) -> None:
        matn = texts.check_report(self.Result(), days=7, percent=60)
        assert "oyna 7 kun · chegara 60%" in matn

    def test_omitted_when_not_given(self) -> None:
        assert "oyna" not in texts.check_report(self.Result())
