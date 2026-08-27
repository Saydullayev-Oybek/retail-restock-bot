"""billz/gateway.py — Billz JSON'ini domen modellariga o'girish.

Nega bu testlar kerak: Billz javob maydonlari hujjatdan farq qilishi mumkin va
biz "bir nechta nomni sinash" strategiyasini tanladik. Testlar shu strategiya
haqiqatan ishlashini va yetishmagan maydon istisno tashlamasligini qotiradi.
"""

from __future__ import annotations

from datetime import date

import pytest

from povtor_bot.billz import gateway as gw
from povtor_bot.core.models import ProductInfo


class TestParseDate:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("2025-11-30 17:40:15", date(2025, 11, 30)),
            ("2024-10-09T14:29:01+05:00", date(2024, 10, 9)),
            ("2025-11-30", date(2025, 11, 30)),
            (date(2026, 8, 20), date(2026, 8, 20)),
        ],
    )
    def test_known_formats(self, raw: object, expected: date) -> None:
        assert gw.parse_date(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "aniqmas", "30.11.2025"])
    def test_unparseable_returns_none(self, raw: object) -> None:
        assert gw.parse_date(raw) is None


class TestExtractColor:
    """Rang manbai — HAQIQIY Billz javobidan olingan shakl.

    Bu akkauntda `product_attributes` HAMMA joyda bo'sh, rang esa
    `custom_fields` ichida "Цвет" nomi bilan turadi (probe bilan tasdiqlangan).
    """

    def test_from_custom_fields(self) -> None:
        row = {
            "product_attributes": [],
            "custom_fields": [
                {"custom_field_name": "Подкатегория", "custom_field_value": "Рубашка с дл/р"},
                {"custom_field_name": "Цвет", "custom_field_value": "Т.Синий"},
                {"custom_field_name": "Пол", "custom_field_value": "MEN"},
            ],
        }
        assert gw.extract_color(row) == "Т.Синий"

    def test_custom_field_name_case_insensitive(self) -> None:
        assert gw.extract_color(
            {"custom_fields": [{"custom_field_name": "ЦВЕТ", "custom_field_value": "Белый"}]}
        ) == "Белый"

    def test_custom_fields_win_over_attributes(self) -> None:
        row = {
            "custom_fields": [{"custom_field_name": "Цвет", "custom_field_value": "Синий"}],
            "product_attributes": [
                {"attribute_name": "Цвет", "attribute_value": "ESKI"}
            ],
        }
        assert gw.extract_color(row) == "Синий"

    def test_empty_custom_field_value(self) -> None:
        assert gw.extract_color(
            {"custom_fields": [{"custom_field_name": "Цвет", "custom_field_value": ""}]}
        ) == ""

    def test_from_attribute_list(self) -> None:
        row = {"product_attributes": [
            {"attribute_name": "Размер", "attribute_value": "42"},
            {"attribute_name": "Цвет", "attribute_value": "Т.Синий"},
        ]}
        assert gw.extract_color(row) == "Т.Синий"

    def test_attribute_name_case_insensitive(self) -> None:
        assert gw.extract_color(
            {"product_attributes": [{"attribute_name": "ЦВЕТ", "attribute_value": "Белый"}]}
        ) == "Белый"

    def test_from_flat_string_with_label(self) -> None:
        assert gw.extract_color({"product_attribute": "Цвет: Белый"}) == "Белый"

    def test_from_flat_string_without_label(self) -> None:
        assert gw.extract_color({"product_attribute": "Белый"}) == "Белый"

    def test_missing_color_is_empty(self) -> None:
        assert gw.extract_color({"product_attributes": []}) == ""
        assert gw.extract_color({}) == ""


class TestCustomFields:
    def test_subcategory_and_kind(self) -> None:
        """Excel'dagi "Podkategoriya" va "Tur" ustunlari shu maydonlardan keladi."""
        row = {"custom_fields": [
            {"custom_field_name": "Подкатегория", "custom_field_value": "Американка"},
            {"custom_field_name": "Вид", "custom_field_value": "Полоска"},
        ]}
        assert gw.custom_field(row, gw.SUBCATEGORY_FIELD_NAMES) == "Американка"
        assert gw.custom_field(row, gw.KIND_FIELD_NAMES) == "Полоска"

    def test_falls_back_to_system_name(self) -> None:
        row = {"custom_fields": [
            {"custom_field_system_name": "Цвет", "custom_field_value": "Бордовый"},
        ]}
        assert gw.custom_field(row, gw.COLOR_FIELD_NAMES) == "Бордовый"

    def test_missing_field(self) -> None:
        assert gw.custom_field({"custom_fields": []}, gw.COLOR_FIELD_NAMES) == ""
        assert gw.custom_field({}, gw.COLOR_FIELD_NAMES) == ""


class TestProductInfoMapping:
    """Haqiqiy /v2/products qatori -> ProductInfo."""

    ROW = {
        "id": "pid-1",
        "sku": "39666",
        "name": "Рубашка с дл/р|Однотонный",
        "main_image_url": "9633672a-69d8-4663-8ffa-bcb9d3e90a34.jpg",
        "product_attributes": [],
        "custom_fields": [
            {"custom_field_name": "Цвет", "custom_field_value": "Белый"},
            {"custom_field_name": "Подкатегория", "custom_field_value": "Рубашка с дл/р"},
            {"custom_field_name": "Вид", "custom_field_value": "Однотонный"},
        ],
        "categories": [{"name": "Плечевые одежды", "parent_id": ""}],
        "suppliers": [{"name": "Sharof M255"}],
        "shop_prices": [{"supply_price": 0, "supply_currency": ""}],
    }

    def test_maps_every_field(self) -> None:
        info = gw._product_info(self.ROW)
        assert info is not None
        assert info.sku == "39666"
        assert info.color == "Белый"
        assert info.subcategory == "Рубашка с дл/р"
        assert info.kind == "Однотонный"
        assert info.category_group == "Плечевые одежды"
        assert info.supplier == "Sharof M255"
        # Billz faqat fayl nomini beradi — to'liq manzil emas
        assert info.image_file == "9633672a-69d8-4663-8ffa-bcb9d3e90a34.jpg"

    def test_zero_supply_price_is_not_invented(self) -> None:
        """Bu akkauntda /v2/products dagi supply_price 0 — o'ylab topmaymiz."""
        info = gw._product_info(self.ROW)
        assert info is not None and info.supply_price == 0.0

    def test_row_without_sku_is_dropped(self) -> None:
        assert gw._product_info({"name": "x"}) is None


class TestCategoryLevels:
    def test_level_fields_win(self) -> None:
        row = {"level_1": ["Obuv"], "level_2": ["Лоферы"], "categories_path": ["Boshqa"]}
        assert gw.category_levels(row) == ("Obuv", "Лоферы")

    def test_falls_back_to_path(self) -> None:
        assert gw.category_levels({"categories_path": ["Obuv", "Мокасины"]}) == (
            "Obuv", "Мокасины",
        )

    def test_falls_back_to_categories_objects(self) -> None:
        row = {"categories": [{"name": "Poyasnaya"}, {"name": "Брюки на резинке"}]}
        assert gw.category_levels(row) == ("Poyasnaya", "Брюки на резинке")

    def test_empty_row(self) -> None:
        assert gw.category_levels({}) == ("", "")


class TestProductHelpers:
    def test_main_image_prefers_explicit_url(self) -> None:
        assert gw._main_image({"main_image_url": "https://cdn/x.jpg"}) == "https://cdn/x.jpg"

    def test_main_image_prefers_is_main_photo(self) -> None:
        row = {"photos": [
            {"photo_url": "https://cdn/2.jpg", "is_main": False},
            {"photo_url": "https://cdn/1.jpg", "is_main": True},
        ]}
        assert gw._main_image(row) == "https://cdn/1.jpg"

    def test_main_image_absent(self) -> None:
        assert gw._main_image({"main_image_url": "", "photos": []}) == ""

    def test_supply_price_skips_zero_entries(self) -> None:
        row = {"shop_prices": [
            {"supply_price": 0, "supply_currency": "UZS"},
            {"supply_price": 20000, "supply_currency": "uzs"},
        ]}
        assert gw._supply_price(row) == (20000.0, "UZS")

    def test_supply_price_keeps_foreign_currency(self) -> None:
        row = {"shop_prices": [{"supply_price": 12.5, "supply_currency": "USD"}]}
        assert gw._supply_price(row) == (12.5, "USD")

    def test_supply_price_missing(self) -> None:
        assert gw._supply_price({}) == (0.0, "UZS")

    def test_first_supplier_from_objects(self) -> None:
        assert gw._first_supplier({"suppliers": [{"name": "Sharof M255"}]}) == "Sharof M255"

    def test_stock_by_shop(self) -> None:
        row = {"shop_measurement_values": [
            {"shop_id": "a", "active_measurement_value": 5},
            {"shop_id": "b", "active_measurement_value": "3"},
            {"active_measurement_value": 9},          # shop_id yo'q — tashlanadi
        ]}
        assert gw._stock_by_shop(row) == {"a": 5, "b": 3}


class TestRowsOf:
    def test_unwraps_data_envelope(self) -> None:
        payload = {"code": 200, "data": {"rows": [{"a": 1}]}}
        assert gw._rows_of(payload, "rows") == [{"a": 1}]

    def test_finds_first_list_when_key_unknown(self) -> None:
        assert gw._rows_of({"whatever": [{"a": 1}]}, "rows") == [{"a": 1}]

    def test_empty_payload(self) -> None:
        assert gw._rows_of({}, "rows") == []


class TestIndexProducts:
    def test_prefers_more_complete_duplicate(self) -> None:
        thin = ProductInfo(sku="1", color="Белый")
        rich = ProductInfo(
            sku="1", color="Белый", image_file="u.jpg", supply_price=10,
            supplier="S", category_group="Obuv",
        )
        assert gw.index_products([thin, rich])[("1", "Белый")] is rich
        assert gw.index_products([rich, thin])[("1", "Белый")] is rich


class TestPagination:
    """Sahifalash — jim ma'lumot yo'qotishning eng ehtimolli joyi."""

    class StubClient:
        """Har chaqiruvda navbatdagi sahifani qaytaradi."""

        def __init__(self, pages: list[list[dict]]) -> None:
            self.pages = pages
            self.requested: list[dict] = []

        async def get(self, path: str, params: dict) -> dict:
            self.requested.append(dict(params))
            index = params["page"] - 1
            rows = self.pages[index] if index < len(self.pages) else []
            return {"rows": rows, "count": len(rows)}

    def _gateway(self, pages: list[list[dict]], page_limit: int = 500,
                 concurrency: int = 1):
        """concurrency=1 — sahifalash mantig'ini aniq tekshirish uchun.

        Guruh-guruh so'ralganda oxirida bir necha ortiqcha (bo'sh) so'rov
        ketadi, shuning uchun so'rovlar SONINI tekshiradigan testlar ketma-ket
        rejimda yoziladi. Parallel rejim alohida sinaladi.
        """
        client = self.StubClient(pages)
        return (
            gw.BillzGateway(client, page_limit=page_limit, concurrency=concurrency),
            client,
        )  # type: ignore[arg-type]

    async def test_configured_page_limit_is_used(self) -> None:
        """Sahifa hajmi .env dan keladi — Billz vaqtni har so'rovga sarflaydi,
        shuning uchun kattaroq sahifa tekshiruvni tezlashtiradi."""
        gateway, client = self._gateway([[{"i": 1}], []], page_limit=2000)
        await gateway._paginate("/x", {}, "rows")
        assert all(p["limit"] == 2000 for p in client.requested)

    async def test_explicit_limit_overrides_config(self) -> None:
        gateway, client = self._gateway([[{"i": 1}], []], page_limit=2000)
        await gateway._paginate("/x", {}, "rows", limit=50)
        assert all(p["limit"] == 50 for p in client.requested)

    async def test_collects_every_page(self) -> None:
        pages = [[{"i": i} for i in range(3)], [{"i": 3}, {"i": 4}, {"i": 5}], [{"i": 6}]]
        gateway, _ = self._gateway(pages)
        rows = await gateway._paginate("/x", {}, "rows", limit=3)
        assert [r["i"] for r in rows] == [0, 1, 2, 3, 4, 5, 6]

    async def test_short_page_in_the_middle_does_not_stop(self) -> None:
        """⭐ Billz o'rtadagi sahifada kam qator qaytarishi mumkin.

        Real ishga tushirishda aynan shu jim ma'lumot yo'qotishga olib keldi:
        bir tekshiruv 91 nomzod topdi, keyingisi xuddi shu ma'lumotda 7 ta.
        """
        gateway, client = self._gateway([
            [{"i": i} for i in range(10)],     # to'la
            [{"i": 100}, {"i": 101}],          # QISQA — lekin oxirgisi emas
            [{"i": i} for i in range(200, 210)],
            [],                                 # haqiqiy oxiri
        ])
        rows = await gateway._paginate("/x", {}, "rows", limit=10)
        assert len(rows) == 22                 # qisqa sahifada to'xtamadi
        assert [p["page"] for p in client.requested] == [1, 2, 3, 4]

    async def test_last_short_page_then_empty(self) -> None:
        gateway, client = self._gateway([
            [{"i": i} for i in range(10)], [{"i": 10}], [],
        ])
        rows = await gateway._paginate("/x", {}, "rows", limit=10)
        assert len(rows) == 11
        assert len(client.requested) == 3      # bo'sh sahifa bilan tasdiqlandi

    async def test_stops_on_empty_first_page(self) -> None:
        gateway, client = self._gateway([[]])
        assert await gateway._paginate("/x", {}, "rows", limit=10) == []
        assert len(client.requested) == 1

    async def test_concurrent_batches_keep_order_and_stop_correctly(self) -> None:
        """Parallel so'ralganda ham tartib va to'xtash nuqtasi to'g'ri bo'lsin."""
        pages = [[{"i": p * 10 + j} for j in range(10)] for p in range(7)] + [[]]
        gateway, client = self._gateway(pages, page_limit=10, concurrency=4)
        rows = await gateway._paginate("/x", {}, "rows")
        assert [r["i"] for r in rows] == [p * 10 + j for p in range(7) for j in range(10)]

    async def test_pages_after_the_empty_one_are_discarded(self) -> None:
        """Bo'shlikdan keyin kelgan sahifa e'tiborga olinmaydi.

        Guruhda 4 ta sahifa birga so'raladi; agar 2-si bo'sh bo'lsa, 3 va 4
        chi qanday javob qaytarsa ham ma'lumot tugagan hisoblanadi.
        """
        pages = [[{"i": 1}], [], [{"i": 99}], [{"i": 98}]]
        gateway, _ = self._gateway(pages, page_limit=10, concurrency=4)
        rows = await gateway._paginate("/x", {}, "rows")
        assert [r["i"] for r in rows] == [1]

    async def test_concurrency_does_not_lose_the_tail(self) -> None:
        """Sahifalar soni guruh hajmiga bo'linmasa ham hammasi o'qiladi."""
        pages = [[{"i": p}] for p in range(9)] + [[]]
        gateway, _ = self._gateway(pages, page_limit=1, concurrency=4)
        rows = await gateway._paginate("/x", {}, "rows")
        assert [r["i"] for r in rows] == list(range(9))

    async def test_exact_multiple_stops_at_empty_page(self) -> None:
        """Oxirgi sahifa aynan to'la bo'lsa — bo'sh sahifa to'xtatadi."""
        gateway, client = self._gateway([
            [{"i": i} for i in range(5)], [{"i": i} for i in range(5, 10)],
        ])
        rows = await gateway._paginate("/x", {}, "rows", limit=5)
        assert len(rows) == 10
        assert [p["page"] for p in client.requested] == [1, 2, 3]

    async def test_server_capped_pages_are_all_read(self) -> None:
        """500 so'raldi, server 100 qaytardi — hammasi o'qilishi kerak."""
        gateway, client = self._gateway([
            [{"i": i} for i in range(100)],
            [{"i": i} for i in range(100, 200)],
            [{"i": 200}],
            [],
        ])
        rows = await gateway._paginate("/v2/products", {}, "rows", limit=500)
        assert len(rows) == 201
        assert [p["page"] for p in client.requested] == [1, 2, 3, 4]

    async def test_params_are_preserved_across_pages(self) -> None:
        gateway, client = self._gateway([[{"i": 0}], []])
        await gateway._paginate("/x", {"shop_ids": "a,b", "currency": "UZS"}, "rows", limit=1)
        assert all(p["shop_ids"] == "a,b" and p["currency"] == "UZS" for p in client.requested)
