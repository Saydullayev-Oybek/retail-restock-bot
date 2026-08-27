"""core/rules.py — haqiqiy POVTOR faylidan olingan 128 qatorli oltin dataset bilan.

Nega shu test eng muhim: qoida noto'g'ri bo'lsa bot ishlaydi, lekin NOTO'G'RI
tovarni tavsiya qiladi — bu xato jim ketadi. Dataset esa hozirgi qo'lda
ishlaydigan jarayonning haqiqiy natijasi, ya'ni "to'g'ri javob" etaloni.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from povtor_bot.core.models import GRADE_CONFIDENT, GRADE_NORMAL, ProductInfo, SalesRow, TransferRow
from povtor_bot.core import rules
from povtor_bot.core.rules import RuleConfig

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_rules.json").read_text(encoding="utf-8")
)
CFG = RuleConfig()


def _daily_reaching_50_on(base: int, sold: int, days_to_50: int) -> list[int]:
    """50% ga aynan `days_to_50`-kuni yetadigan kunlik sotuv qatorini yasaydi.

    Namuna faylda kunlik taqsimot saqlanmagan — faqat yakuniy raqamlar bor.
    Shuning uchun test uchun shu invariantga mos eng sodda qator quriladi:
    days_to_50 gacha 50% dan kam, o'sha kuni 50% dan oshadi, qolgani oxirgi kunda.
    """
    need = base * 0.5
    daily = [0] * (days_to_50 + 1)
    # days_to_50 dan oldingi kunlarga 50% dan kam bo'ladigan miqdor tarqatamiz
    before = 0
    if days_to_50 > 0:
        # 50% chegarasidan sal past qoladigan eng katta butun son
        before = max(0, min(sold - 1, int((need - 0.0001) // 1)))
        # oldingi kunlarga teng tarqatamiz
        for i in range(days_to_50):
            daily[i] = before // days_to_50
        daily[days_to_50 - 1] += before - (before // days_to_50) * days_to_50
    daily[days_to_50] = sold - sum(daily)
    assert sum(daily) == sold
    return daily


@pytest.mark.parametrize("row", GOLDEN, ids=lambda r: f"{r['base_qty']}/{r['sold_qty']}d{r['days_to_50']}")
def test_golden_dataset(row: dict) -> None:
    """Har bir haqiqiy qator uchun daraja, tavsiya va izoh mos kelishi kerak."""
    grade = rules.grade_of(row["days_to_50"], row["sold_qty"], row["percent"], CFG)
    assert grade == row["grade"]
    assert rules.recommended_qty(grade, CFG) == row["recommended_qty"]
    assert rules.round_percent(row["sold_qty"], row["base_qty"]) == row["percent"]
    assert (
        rules.build_note(row["percent"], grade, row["days_to_50"], row["sold_qty"], CFG)
        == row["note"]
    )


@pytest.mark.parametrize("row", GOLDEN, ids=lambda r: f"{r['base_qty']}/{r['sold_qty']}")
def test_evaluate_end_to_end(row: dict) -> None:
    """evaluate() ham xuddi shu natijani berishi kerak (kunlik qator orqali)."""
    daily = _daily_reaching_50_on(row["base_qty"], row["sold_qty"], row["days_to_50"])
    verdict = rules.evaluate(
        base_qty=row["base_qty"],
        daily_sales=daily,
        days_elapsed=len(daily) - 1,
        cfg=CFG,
    )
    assert verdict is not None, "haqiqiy fayldagi qator nomzod bo'lishi shart"
    assert verdict["grade"] == row["grade"]
    assert verdict["recommended_qty"] == row["recommended_qty"]
    assert verdict["percent"] == row["percent"]
    assert verdict["days_to_50"] == row["days_to_50"]
    assert verdict["note"] == row["note"]


# ─────────────────────── chegaraviy holatlar ───────────────────────


def test_below_threshold_is_not_candidate() -> None:
    assert rules.evaluate(base_qty=10, daily_sales=[4], days_elapsed=0, cfg=CFG) is None


def test_exactly_at_threshold_is_candidate() -> None:
    verdict = rules.evaluate(base_qty=10, daily_sales=[5], days_elapsed=0, cfg=CFG)
    assert verdict is not None and verdict["percent"] == 50.0


def test_outside_window_is_skipped() -> None:
    """Oynadan (5 kun) chiqib ketgan tovar endi 'tez sotilgan' hisoblanmaydi."""
    assert rules.evaluate(
        base_qty=10, daily_sales=[0, 0, 0, 0, 0, 0, 9], days_elapsed=6, cfg=CFG
    ) is None


def test_zero_base_is_skipped() -> None:
    assert rules.evaluate(base_qty=0, daily_sales=[3], days_elapsed=0, cfg=CFG) is None


def test_no_sales_is_skipped() -> None:
    assert rules.evaluate(base_qty=5, daily_sales=[0, 0], days_elapsed=1, cfg=CFG) is None


def test_confident_requires_minimum_volume() -> None:
    """kun<=3 bo'lsa ham sotilgan<4 bo'lsa 'oddiy' — namuna fayldagi asosiy nozik joy."""
    assert rules.grade_of(days_to_50=3, sold_qty=3, percent=60.0, cfg=CFG) == GRADE_NORMAL
    assert rules.grade_of(days_to_50=3, sold_qty=4, percent=66.7, cfg=CFG) == GRADE_CONFIDENT


def test_high_percent_is_confident_even_when_slow() -> None:
    """Sekin, lekin 80% dan ko'p sotilgan bo'lsa ham 'ishonchli' (asos 5 / sotilgan 4)."""
    assert rules.grade_of(days_to_50=4, sold_qty=4, percent=80.0, cfg=CFG) == GRADE_CONFIDENT
    # ...lekin foiz past bo'lsa sekinlik 'oddiy' qiladi
    assert rules.grade_of(days_to_50=4, sold_qty=9, percent=75.0, cfg=CFG) == GRADE_NORMAL


def test_min_sold_guard_applies_to_high_percent_too() -> None:
    """B varianti (default): 2 donadan 2 tasi sotilgani hali 'ishonchli' emas."""
    assert rules.grade_of(days_to_50=0, sold_qty=2, percent=100.0, cfg=CFG) == GRADE_NORMAL
    # A variantiga o'tilsa — o'tadi
    cfg_a = RuleConfig(high_percent_overrides_min_sold=True)
    assert rules.grade_of(days_to_50=0, sold_qty=2, percent=100.0, cfg=cfg_a) == GRADE_CONFIDENT


def test_days_to_reach_counts_from_arrival_day() -> None:
    assert rules.days_to_reach([5], base=10) == 0       # kelgan kuni
    assert rules.days_to_reach([2, 3], base=10) == 1
    assert rules.days_to_reach([1, 1], base=10) is None


def test_category_filter() -> None:
    cfg = RuleConfig(allowed_category_groups=("Obuv", "Poyasnaya"))
    assert rules.category_allowed("Obuv", cfg)
    assert rules.category_allowed("  obuv ", cfg)       # registr/probel muhim emas
    assert not rules.category_allowed("Aksessuar", cfg)
    assert rules.category_allowed("Nima bo'lsa ham", RuleConfig())  # ro'yxat bo'sh


def test_to_uzs_conversion() -> None:
    assert rules.to_uzs(1000, "UZS", usd_rate=12500) == 1000
    assert rules.to_uzs(10, "USD", usd_rate=12500) == 125000
    # Kurs noma'lum bo'lsa noto'g'ri raqam ko'rsatgandan ko'ra 0 (ya'ni "yashirish") afzal
    assert rules.to_uzs(10, "USD", usd_rate=0) == 0
    assert rules.to_uzs(0, "UZS", usd_rate=12500) == 0


# ─────────────────────── detect_candidates ───────────────────────

TODAY = date(2026, 8, 20)


def _product(sku: str, color: str, group: str = "Obuv") -> ProductInfo:
    return ProductInfo(
        sku=sku, color=color, name=f"Tovar {sku}", category_group=group,
        subcategory="Лоферы", kind="Однотонный", supplier="Sardor M193",
        supply_price=100.0, supply_currency="UZS",
    )


def _transfer(shop: str, sku: str, color: str, day, qty: int, **kw) -> TransferRow:
    """Testlarda transferni qisqa yozish uchun (kategoriya sukut bo'yicha Obuv)."""
    kw.setdefault("category_group", "Obuv")
    return TransferRow(shop, sku, color, day, qty, **kw)


def test_detect_uses_latest_arrival_only() -> None:
    """Bir tovar ikki marta kelgan bo'lsa — faqat OXIRGISI hisoblanadi."""
    transfers = [
        _transfer("shop1", "40595", "Синий", TODAY - timedelta(days=10), 20),
        _transfer("shop1", "40595", "Синий", TODAY - timedelta(days=2), 5),
    ]
    sales = [SalesRow("shop1", "40595", "Синий", TODAY - timedelta(days=1), 4)]
    result = rules.detect_candidates(
        today=TODAY, transfers=transfers, sales=sales,
        products={("40595", "Синий"): _product("40595", "Синий")},
        shop_names={"shop1": "ANDALUS"}, cfg=CFG,
    )
    assert len(result) == 1
    assert result[0].base_qty == 5            # 20 emas
    assert result[0].arrived_date == TODAY - timedelta(days=2)
    assert result[0].percent == 80.0
    assert result[0].shop_name == "ANDALUS"


def test_detect_filters_by_category() -> None:
    cfg = RuleConfig(allowed_category_groups=("Obuv",))
    transfers = [_transfer("shop1", "1", "Белый", TODAY - timedelta(days=1), 5,
                           category_group="Aksessuar")]
    sales = [SalesRow("shop1", "1", "Белый", TODAY, 5)]
    assert rules.detect_candidates(
        today=TODAY, transfers=transfers, sales=sales,
        products={("1", "Белый"): _product("1", "Белый", group="Aksessuar")},
        shop_names={}, cfg=cfg,
    ) == []


def test_detect_separates_colors_and_shops() -> None:
    """Bir artikulning har rangi va har filiali — alohida band."""
    transfers = [
        _transfer("shop1", "46043", "Белый", TODAY - timedelta(days=1), 6),
        _transfer("shop1", "46043", "Синий", TODAY - timedelta(days=1), 6),
        _transfer("shop2", "46043", "Белый", TODAY - timedelta(days=1), 6),
    ]
    sales = [
        SalesRow("shop1", "46043", "Белый", TODAY, 5),
        SalesRow("shop1", "46043", "Синий", TODAY, 5),
        SalesRow("shop2", "46043", "Белый", TODAY, 5),
    ]
    products = {
        ("46043", "Белый"): _product("46043", "Белый"),
        ("46043", "Синий"): _product("46043", "Синий"),
    }
    result = rules.detect_candidates(
        today=TODAY, transfers=transfers, sales=sales, products=products,
        shop_names={"shop1": "ANDALUS", "shop2": "BERUNIY"}, cfg=CFG,
    )
    assert len(result) == 3
    assert {(c.shop_name, c.color) for c in result} == {
        ("ANDALUS", "Белый"), ("ANDALUS", "Синий"), ("BERUNIY", "Белый"),
    }


def test_detect_converts_price_to_uzs() -> None:
    transfers = [_transfer("shop1", "1", "Белый", TODAY, 5)]
    sales = [SalesRow("shop1", "1", "Белый", TODAY, 5)]
    info = ProductInfo(sku="1", color="Белый", category_group="Obuv",
                       supply_price=12.5, supply_currency="USD")
    result = rules.detect_candidates(
        today=TODAY, transfers=transfers, sales=sales,
        products={("1", "Белый"): info}, shop_names={}, cfg=CFG, usd_rate=12800,
    )
    assert result[0].price_uzs == 160000


class TestSameDayArrivalsAreSummed:
    """Bir (artikul + rang) Billz'da bir nechta product_id ga bo'linadi.

    O'lcham setkasi va sezon bo'yicha alohida variatsiyalar bo'ladi, va ular
    alohida transfer qatorlari bo'lib keladi. Menejer uchun esa bu BITTA qaror
    — shuning uchun bir kunda kelganlar qo'shiladi.
    """

    def test_quantities_of_the_same_day_are_added(self) -> None:
        day = TODAY - timedelta(days=1)
        transfers = [
            _transfer("shop1", "46043", "Белый", day, 4, product_id="pid-M-XL"),
            _transfer("shop1", "46043", "Белый", day, 2, product_id="pid-2XL-3XL"),
        ]
        sales = [SalesRow("shop1", "46043", "Белый", TODAY, 5)]
        result = rules.detect_candidates(
            today=TODAY, transfers=transfers, sales=sales,
            products={("46043", "Белый"): _product("46043", "Белый")},
            shop_names={"shop1": "ANDALUS"}, cfg=CFG,
        )
        assert len(result) == 1
        assert result[0].base_qty == 6            # 4 + 2, eng kattasi (4) emas
        assert result[0].percent == 83.3

    def test_earlier_day_does_not_add_to_latest(self) -> None:
        """Faqat OXIRGI kelgan kun qo'shiladi — undan oldingisi hisobga olinmaydi."""
        transfers = [
            _transfer("shop1", "1", "Белый", TODAY - timedelta(days=4), 20),
            _transfer("shop1", "1", "Белый", TODAY - timedelta(days=1), 3),
            _transfer("shop1", "1", "Белый", TODAY - timedelta(days=1), 2),
        ]
        sales = [SalesRow("shop1", "1", "Белый", TODAY, 5)]
        result = rules.detect_candidates(
            today=TODAY, transfers=transfers, sales=sales,
            products={("1", "Белый"): _product("1", "Белый")},
            shop_names={}, cfg=CFG,
        )
        assert result[0].base_qty == 5            # 3 + 2
        assert result[0].arrived_date == TODAY - timedelta(days=1)


class TestPriceFromTransfer:
    """Tannarx transfer hisobotidan olinadi — u allaqachon so'mda.

    Sabab: bu Billz akkauntida /v2/products dagi supply_price 0 keladi, transfer
    hisoboti esa display_currency=UZS bilan so'ralgani uchun ishonchli.
    """

    def test_unit_price_from_transfer_wins(self) -> None:
        transfers = [
            _transfer("shop1", "1", "Белый", TODAY, 5, unit_supply_price=145000.0)
        ]
        sales = [SalesRow("shop1", "1", "Белый", TODAY, 5)]
        info = ProductInfo(sku="1", color="Белый", category_group="Obuv",
                           supply_price=12.5, supply_currency="USD")
        result = rules.detect_candidates(
            today=TODAY, transfers=transfers, sales=sales,
            products={("1", "Белый"): info}, shop_names={}, cfg=CFG, usd_rate=12800,
        )
        assert result[0].price_uzs == 145000
        assert result[0].supply_currency == "UZS"

    def test_falls_back_to_catalog_when_transfer_has_no_price(self) -> None:
        transfers = [_transfer("shop1", "1", "Белый", TODAY, 5)]
        sales = [SalesRow("shop1", "1", "Белый", TODAY, 5)]
        info = ProductInfo(sku="1", color="Белый", category_group="Obuv",
                           supply_price=10, supply_currency="USD")
        result = rules.detect_candidates(
            today=TODAY, transfers=transfers, sales=sales,
            products={("1", "Белый"): info}, shop_names={}, cfg=CFG, usd_rate=12800,
        )
        assert result[0].price_uzs == 128000


class TestCategoryFromReport:
    """Kategoriya hisobotdan ham keladi — katalog o'qilmagan bo'lsa ham filtr ishlaydi."""

    def test_report_category_is_used_without_catalog(self) -> None:
        cfg = RuleConfig(allowed_category_groups=("Верхняя одежда",))
        transfers = [_transfer("shop1", "1", "", TODAY, 5,
                               category_group="Верхняя одежда")]
        sales = [SalesRow("shop1", "1", "", TODAY, 5)]
        result = rules.detect_candidates(
            today=TODAY, transfers=transfers, sales=sales,
            products={}, shop_names={}, cfg=cfg,       # katalog bo'sh
        )
        assert len(result) == 1
        assert result[0].category_group == "Верхняя одежда"

    def test_report_category_excludes_too(self) -> None:
        cfg = RuleConfig(allowed_category_groups=("Обувь",))
        transfers = [_transfer("shop1", "1", "", TODAY, 5,
                               category_group="Парфюмерия")]
        sales = [SalesRow("shop1", "1", "", TODAY, 5)]
        assert rules.detect_candidates(
            today=TODAY, transfers=transfers, sales=sales,
            products={}, shop_names={}, cfg=cfg,
        ) == []


class TestMinimumBatch:
    """Kichik "to'ldirish" transferlari nomzod bo'lmasligi kerak.

    Real Billz ma'lumotida 1-2 donalik transferlar ko'p: filialda tovar
    qolmaganda skladdan bittalab yuboriladi. Bunday partiyada "100% sotildi"
    statistik ma'noga ega emas — namuna POVTOR faylida Asos hech qachon 5 dan
    kichik emas (74/128 aynan 5).
    """

    @pytest.mark.parametrize("base", [1, 2, 3, 4])
    def test_small_batches_are_rejected(self, base: int) -> None:
        assert rules.evaluate(
            base_qty=base, daily_sales=[base], days_elapsed=0, cfg=CFG
        ) is None

    def test_five_is_accepted(self) -> None:
        assert rules.evaluate(
            base_qty=5, daily_sales=[5], days_elapsed=0, cfg=CFG
        ) is not None

    def test_threshold_is_configurable(self) -> None:
        cfg = RuleConfig(min_base_qty=1)
        assert rules.evaluate(
            base_qty=1, daily_sales=[1], days_elapsed=0, cfg=cfg
        ) is not None

    def test_detect_drops_small_arrivals(self) -> None:
        transfers = [
            _transfer("shop1", "big", "Белый", TODAY, 6),
            _transfer("shop1", "small", "Белый", TODAY, 2),
        ]
        sales = [
            SalesRow("shop1", "big", "Белый", TODAY, 5),
            SalesRow("shop1", "small", "Белый", TODAY, 2),
        ]
        products = {
            ("big", "Белый"): _product("big", "Белый"),
            ("small", "Белый"): _product("small", "Белый"),
        }
        result = rules.detect_candidates(
            today=TODAY, transfers=transfers, sales=sales, products=products,
            shop_names={}, cfg=CFG,
        )
        assert [c.sku for c in result] == ["big"]


class TestPercentIsCapped:
    """Filialda oldingi qoldiq bo'lsa sotuv partiyadan ko'p chiqadi.

    Real ma'lumotda "5 keldi, 6 sotildi = 120%" holatlari bor. Partiya bunday
    holatda to'g'ri maxraj emas; namuna faylda foiz hech qachon 100 dan oshmagan.
    """

    def test_percent_never_exceeds_hundred(self) -> None:
        assert rules.round_percent(sold=6, base=5) == 100.0
        assert rules.round_percent(sold=12, base=5) == 100.0

    def test_normal_case_unchanged(self) -> None:
        assert rules.round_percent(sold=4, base=5) == 80.0
        assert rules.round_percent(sold=4, base=6) == 66.7

    def test_evaluate_reports_capped_percent_but_true_sold(self) -> None:
        verdict = rules.evaluate(
            base_qty=5, daily_sales=[6], days_elapsed=0, cfg=CFG
        )
        assert verdict is not None
        assert verdict["percent"] == 100.0      # foiz cheklangan
        assert verdict["sold_qty"] == 6         # haqiqiy sotuv saqlanadi
        assert verdict["note"] == "100% sotildi"
