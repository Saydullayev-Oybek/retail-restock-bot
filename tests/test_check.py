"""services/check.py — to'liq /tekshir sikli soxta Billz gateway bilan.

Nega soxta gateway: bu yerda tekshirilayotgan narsa Billz bilan muloqot emas
(u test_client.py da), balki ORKESTRATSIYA — qaysi transferlar hisobga olinadi,
kesh to'ldiriladimi, qoldiq snapshot yoziladimi, idempotentlik ishlaydimi.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from povtor_bot.config import Settings
from povtor_bot.core.models import ProductInfo, SalesRow, Shop, StockRow, TransferRow
from povtor_bot.db import repo
from povtor_bot.services import check as check_service

pytestmark = pytest.mark.usefixtures("database")

TODAY = date(2026, 8, 20)
WAREHOUSE = "sklad-uuid"


class FakeGateway:
    """BillzGateway ning test o'rnini bosuvchisi."""

    def __init__(self, **data) -> None:
        self.data = data
        self.calls: list[str] = []

    async def shops(self) -> list[Shop]:
        self.calls.append("shops")
        return self.data.get("shops", [
            Shop(WAREHOUSE, "SKLAD"),
            Shop("shop1", "ANDALUS"),
            Shop("shop2", "BERUNIY"),
        ])

    async def transfers(self, *, start, end, shop_ids) -> list[TransferRow]:
        self.calls.append("transfers")
        return self.data.get("transfers", [])

    async def sales(self, *, start, end, shop_ids) -> list[SalesRow]:
        self.calls.append("sales")
        return self.data.get("sales", [])

    async def stock(self, *, report_date, shop_ids) -> list[StockRow]:
        self.calls.append("stock")
        return self.data.get("stock", [])

    async def products(self, last_updated: str = "") -> list[ProductInfo]:
        self.calls.append("products")
        return self.data.get("products", [])

    async def products_by_sku(self, sku: str) -> list[ProductInfo]:
        """Katalog endi artikul bo'yicha o'qiladi (76 000+ tovarni tortmaslik uchun)."""
        self.calls.append(f"products_by_sku:{sku}")
        return [p for p in self.data.get("products", []) if p.sku == sku]

    async def usd_rate(self) -> float:
        self.calls.append("usd_rate")
        return self.data.get("usd_rate", 0.0)


def make_settings(**overrides) -> Settings:
    base = dict(
        bot_token="x", allowed_user_ids=[1], warehouse_shop_ids=[WAREHOUSE],
        billz_secret_token="s", allowed_category_groups=[],
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def a_product(sku="39666", color="Белый", group="Poyasnaya",
              product_id: str = "") -> ProductInfo:
    return ProductInfo(
        sku=sku, color=color, name=f"Tovar {sku}", category_group=group,
        subcategory="Рубашка с дл/р", kind="Однотонный", supplier="Sharof M255",
        product_id=product_id or f"pid-{sku}-{color}",
        image_file=f"{sku}.jpg", supply_price=90000, supply_currency="UZS",
    )


def a_transfer(shop, sku, color, day, qty, *, product_id=None, **kw) -> TransferRow:
    """SKLAD -> filial transferi (from_shop_id sukut bo'yicha WAREHOUSE)."""
    kw.setdefault("from_shop_id", WAREHOUSE)
    kw.setdefault("category_group", "Poyasnaya")
    kw.setdefault("product_id", product_id or f"pid-{sku}-{color}")
    return TransferRow(shop, sku, color, day, qty, **kw)


def a_sale(shop, sku, color, day, qty, *, product_id=None) -> SalesRow:
    return SalesRow(shop, sku, color, day, qty,
                    product_id=product_id or f"pid-{sku}-{color}")


class TestGuards:
    async def test_missing_warehouse_id_reports_error(self) -> None:
        result = await check_service.run_check(
            FakeGateway(), make_settings(warehouse_shop_ids=[]), today=TODAY
        )
        assert not result.ok and "WAREHOUSE_SHOP_IDS" in result.error

    async def test_no_filials_reports_error(self) -> None:
        gateway = FakeGateway(shops=[Shop(WAREHOUSE, "SKLAD")])
        result = await check_service.run_check(gateway, make_settings(), today=TODAY)
        assert not result.ok and "filial" in result.error.lower()

    async def test_billz_failure_is_reported_not_raised(self) -> None:
        """Billz yiqilsa bot ham yiqilmasligi kerak — menejer sababni ko'rsin."""
        class Broken(FakeGateway):
            async def transfers(self, **kwargs):
                raise RuntimeError("connection reset")

        result = await check_service.run_check(Broken(), make_settings(), today=TODAY)
        assert not result.ok and "connection reset" in result.error


class TestHappyPath:
    def _gateway(self) -> FakeGateway:
        return FakeGateway(
            transfers=[
                # SKLAD'ning o'ziga kelgan — nomzod emas
                a_transfer(WAREHOUSE, "39666", "Белый", TODAY, 5),
                # boshqa filialdan kelgan — "yangi partiya" emas
                a_transfer("shop1", "39666", "Белый", TODAY, 9, from_shop_id="shop2"),
                a_transfer("shop1", "39666", "Белый", TODAY - timedelta(days=2), 5),
            ],
            sales=[
                a_sale("shop1", "39666", "Белый", TODAY - timedelta(days=1), 3),
                a_sale("shop1", "39666", "Белый", TODAY, 2),
            ],
            stock=[
                StockRow("shop2", "39666", "Белый", 4),
                StockRow("shop1", "39666", "Белый", 0),
            ],
            products=[a_product()],
            usd_rate=12800.0,
        )

    async def test_detects_and_stores_candidate(self) -> None:
        result = await check_service.run_check(self._gateway(), make_settings(), today=TODAY)
        assert result.ok
        assert result.total_found == 1 and result.new_count == 1
        rows = await repo.card_items("39666", TODAY)
        assert rows[0]["shop_name"] == "ANDALUS"
        assert (rows[0]["base_qty"], rows[0]["sold_qty"], rows[0]["percent"]) == (5, 5, 100.0)
        assert rows[0]["grade"] == "ishonchli" and rows[0]["recommended_qty"] == 10

    async def test_ignores_transfers_into_warehouse(self) -> None:
        """SKLAD'ning o'ziga kelgan tovar nomzod emas — u hali filialga chiqmagan."""
        await check_service.run_check(self._gateway(), make_settings(), today=TODAY)
        shops = {r["shop_id"] for r in await repo.card_items("39666", TODAY)}
        assert shops == {"shop1"}
        assert WAREHOUSE not in shops

    async def test_ignores_filial_to_filial_transfers(self) -> None:
        """Filialdan filialga ko'chirish "yangi partiya keldi" degani emas.

        Real ma'lumotda bunday transferlar 10% ga yaqin — ular hisobga olinsa
        kelgan sana va Asos miqdori noto'g'ri chiqadi.
        """
        await check_service.run_check(self._gateway(), make_settings(), today=TODAY)
        rows = await repo.card_items("39666", TODAY)
        assert len(rows) == 1
        # shop2 dan bugun kelgan 9 dona emas, skladdan 2 kun oldin kelgan 5 dona
        assert rows[0]["base_qty"] == 5
        assert rows[0]["arrived_date"] == (TODAY - timedelta(days=2)).isoformat()

    async def test_second_run_adds_nothing(self) -> None:
        settings = make_settings()
        first = await check_service.run_check(self._gateway(), settings, today=TODAY)
        second = await check_service.run_check(self._gateway(), settings, today=TODAY)
        assert first.new_count == 1
        assert second.new_count == 0 and second.total_found == 1

    async def test_product_cache_is_populated(self) -> None:
        """Rasm va nom keshlanadi — karta ochilganda Billz'ga borilmaydi."""
        await check_service.run_check(self._gateway(), make_settings(), today=TODAY)
        cached = await repo.get_cached_product("39666", "Белый")
        assert cached["image_url"] == "39666.jpg"
        assert cached["supplier"] == "Sharof M255"
        assert cached["kind"] == "Однотонный"

    async def test_sku_is_fetched_from_catalog_once(self) -> None:
        """Katalog artikul bo'yicha o'qiladi va ikkinchi tekshiruvda qayta so'ralmaydi."""
        settings = make_settings()
        first = FakeGateway(**self._gateway().data)
        await check_service.run_check(first, settings, today=TODAY)
        assert "products_by_sku:39666" in first.calls

        second = FakeGateway(**self._gateway().data)
        await check_service.run_check(second, settings, today=TODAY)
        assert not any(c.startswith("products_by_sku") for c in second.calls)

    async def test_stock_snapshot_enables_transfer_hint(self) -> None:
        await check_service.run_check(self._gateway(), make_settings(), today=TODAY)
        rows = await repo.other_shops_with_stock("39666", "Белый", exclude_shop_id="shop1")
        assert [(r["shop_name"], r["quantity"]) for r in rows] == [("BERUNIY", 4)]

    async def test_zero_stock_rows_are_not_stored(self) -> None:
        await check_service.run_check(self._gateway(), make_settings(), today=TODAY)
        rows = await repo.other_shops_with_stock("39666", "Белый", exclude_shop_id="shop2")
        assert rows == []      # shop1 da qoldiq 0


class TestFilters:
    async def test_category_filter_excludes_other_groups(self) -> None:
        gateway = FakeGateway(
            transfers=[a_transfer("shop1", "1", "Белый", TODAY, 5,
                                  category_group="Аксессуар")],
            sales=[a_sale("shop1", "1", "Белый", TODAY, 5)],
            products=[a_product(sku="1", group="Аксессуар")],
        )
        settings = make_settings(
            allowed_category_groups=["Обувь", "Поясные одежды"]
        )
        result = await check_service.run_check(gateway, settings, today=TODAY)
        assert result.total_found == 0

    async def test_cyrillic_category_names_match(self) -> None:
        """Billz kategoriya nomlari KIRILL — .env dagi qiymat ham shunday bo'lishi kerak."""
        gateway = FakeGateway(
            transfers=[a_transfer("shop1", "1", "Белый", TODAY, 5,
                                  category_group="Верхняя одежда")],
            sales=[a_sale("shop1", "1", "Белый", TODAY, 5)],
            products=[a_product(sku="1", group="Верхняя одежда")],
        )
        settings = make_settings(allowed_category_groups=["Верхняя одежда", "Обувь"])
        result = await check_service.run_check(gateway, settings, today=TODAY)
        assert result.total_found == 1

    async def test_explicit_filial_list_is_respected(self) -> None:
        gateway = FakeGateway(
            transfers=[
                a_transfer("shop1", "1", "Белый", TODAY, 5),
                a_transfer("shop2", "1", "Белый", TODAY, 5),
            ],
            sales=[
                a_sale("shop1", "1", "Белый", TODAY, 5),
                a_sale("shop2", "1", "Белый", TODAY, 5),
            ],
            products=[a_product(sku="1")],
        )
        settings = make_settings(filial_shop_ids=["shop1"])
        result = await check_service.run_check(gateway, settings, today=TODAY)
        assert result.total_found == 1
        assert (await repo.card_items("1", TODAY))[0]["shop_id"] == "shop1"


class TestRecentArrivals:
    async def test_groups_by_product_across_shops(self) -> None:
        gateway = FakeGateway(transfers=[
            a_transfer("shop1", "40595", "Синий", TODAY, 6, product_name="Американка"),
            a_transfer("shop2", "40595", "Синий", TODAY, 4, product_name="Американка"),
        ])
        entries, error = await check_service.recent_arrivals(
            gateway, make_settings(), today=TODAY
        )
        assert error == "" and len(entries) == 1
        assert sorted(entries[0]["shops"]) == [("ANDALUS", 6), ("BERUNIY", 4)]

    async def test_already_announced_items_are_skipped(self) -> None:
        gateway = FakeGateway(transfers=[
            a_transfer("shop1", "40595", "Синий", TODAY, 6),
        ])
        settings = make_settings()
        entries, _ = await check_service.recent_arrivals(gateway, settings, today=TODAY)
        await repo.mark_announced(entries[0]["keys"])
        again, _ = await check_service.recent_arrivals(gateway, settings, today=TODAY)
        assert again == []

    async def test_transfers_into_warehouse_are_not_announced(self) -> None:
        gateway = FakeGateway(transfers=[
            a_transfer(WAREHOUSE, "40595", "Синий", TODAY, 6),
        ])
        entries, _ = await check_service.recent_arrivals(
            gateway, make_settings(), today=TODAY
        )
        assert entries == []


class TestAnnouncePrice:
    """E'londagi narx transfer hisobotidan kelishi kerak.

    Bu akkauntda katalogdagi supply_price 0 — faqat keshga tayansak e'londa
    narx har doim "—" chiqadi.
    """

    async def test_recent_arrivals_carries_unit_price(self) -> None:
        gateway = FakeGateway(transfers=[
            a_transfer("shop1", "40595", "Синий", TODAY, 6, unit_supply_price=145000.0),
        ])
        entries, _ = await check_service.recent_arrivals(
            gateway, make_settings(), today=TODAY
        )
        assert entries[0]["price_uzs"] == 145000

    async def test_announce_prefers_entry_price_over_cache(self) -> None:
        from povtor_bot.services import announce as announce_service

        await repo.cache_products([
            {"sku": "1", "color": "", "supply_price": 0, "supply_currency": ""},
        ])
        sent: list[str] = []

        class Bot:
            async def send_message(self, chat_id, text, **kw):
                sent.append(text)
                return None

            async def send_photo(self, *a, **kw):
                raise AssertionError("rasm yo'q edi")

        entry = {"sku": "1", "color": "", "name": "Ветровка",
                 "price_uzs": 260400, "shops": [("ANDALUS", 7)], "keys": []}
        await announce_service.announce(Bot(), 1, [entry], usd_rate=0.0)
        assert sent and "260 400 so'm" in sent[0]


class TestMultipleWarehouses:
    """Tarmoqda ikkita sklad bor: import skladi va sezon skladi.

    Qaysilari "yangi partiya" manbai hisoblanishi .env dagi ro'yxat bilan
    belgilanadi — filial<->filial ko'chirishlari esa hech qachon hisoblanmaydi.
    """

    SEASON = "sezon-sklad-uuid"

    def _gateway(self) -> FakeGateway:
        return FakeGateway(
            shops=[
                Shop(WAREHOUSE, "СКЛАД ПРИХОДА"),
                Shop(self.SEASON, "BUTTON СКЛАД MEN"),
                Shop("shop1", "ANDALUS"),
            ],
            transfers=[
                a_transfer("shop1", "import", "Белый", TODAY - timedelta(days=1), 5),
                a_transfer("shop1", "sezon", "Белый", TODAY - timedelta(days=1), 5,
                           from_shop_id=self.SEASON),
                a_transfer("shop1", "filial", "Белый", TODAY - timedelta(days=1), 5,
                           from_shop_id="shop2"),
            ],
            sales=[
                a_sale("shop1", "import", "Белый", TODAY, 5),
                a_sale("shop1", "sezon", "Белый", TODAY, 5),
                a_sale("shop1", "filial", "Белый", TODAY, 5),
            ],
            products=[
                a_product(sku="import"), a_product(sku="sezon"), a_product(sku="filial"),
            ],
        )

    async def test_season_warehouse_is_excluded_by_default(self) -> None:
        """Biznes qarori: sezon skladidan qaytgan tovar nomzod bo'lmaydi.

        POVTOR faqat YANGI kelgan tovar uchun — sezoni o'tgan kolleksiyani
        bozordan qayta topib bo'lmaydi, ya'ni "yana ol" tavsiyasi ma'nosiz.
        """
        settings = make_settings(warehouse_shop_ids=[WAREHOUSE], filial_shop_ids=["shop1"])
        await check_service.run_check(self._gateway(), settings, today=TODAY)
        skus = {r["sku"] for r in await repo.card_items("import", TODAY)}
        assert skus == {"import"}
        assert await repo.card_items("sezon", TODAY) == []
        assert await repo.card_items("filial", TODAY) == []

    async def test_second_warehouse_can_be_added(self) -> None:
        settings = make_settings(
            warehouse_shop_ids=[WAREHOUSE, self.SEASON], filial_shop_ids=["shop1"]
        )
        result = await check_service.run_check(self._gateway(), settings, today=TODAY)
        assert result.total_found == 2
        assert len(await repo.card_items("sezon", TODAY)) == 1
        # filial<->filial baribir hisoblanmaydi
        assert await repo.card_items("filial", TODAY) == []

    async def test_filials_default_to_everything_except_warehouses(self) -> None:
        """FILIAL_SHOP_IDS bo'sh bo'lsa — ro'yxatdagi skladlardan boshqa hammasi."""
        settings = make_settings(warehouse_shop_ids=[WAREHOUSE, self.SEASON])
        ids, _ = await check_service.resolve_filial_ids(self._gateway(), settings)
        assert ids == ["shop1"]


class TestStockRefreshCache:
    """Qoldiq hisoboti eng katta (sahifalarning ~57% i), lekin u faqat
    "boshqa filialda bormi?" savoliga javob beradi — bir necha soatlik
    eskilik zarar qilmaydi."""

    def _gateway(self) -> FakeGateway:
        return FakeGateway(
            transfers=[a_transfer("shop1", "1", "Белый", TODAY, 5)],
            sales=[a_sale("shop1", "1", "Белый", TODAY, 5)],
            stock=[StockRow("shop2", "1", "Белый", 4)],
            products=[a_product(sku="1")],
        )

    async def test_first_run_fetches_stock(self) -> None:
        gw = self._gateway()
        await check_service.run_check(gw, make_settings(), today=TODAY)
        assert "stock" in gw.calls
        assert await repo.stock_snapshot_rows() == 1

    async def test_second_run_skips_stock(self) -> None:
        settings = make_settings(stock_refresh_hours=6)
        await check_service.run_check(self._gateway(), settings, today=TODAY)

        gw2 = self._gateway()
        result = await check_service.run_check(gw2, settings, today=TODAY)
        assert "stock" not in gw2.calls
        # eski snapshot saqlanib qoladi — transfer taklifi ishlashda davom etadi
        assert await repo.stock_snapshot_rows() == 1
        assert result.stock_rows == 1

    async def test_transfer_hint_still_works_after_skip(self) -> None:
        settings = make_settings(stock_refresh_hours=6)
        await check_service.run_check(self._gateway(), settings, today=TODAY)
        await check_service.run_check(self._gateway(), settings, today=TODAY)
        rows = await repo.other_shops_with_stock("1", "Белый", exclude_shop_id="shop1")
        assert [(r["shop_name"], r["quantity"]) for r in rows] == [("BERUNIY", 4)]

    async def test_zero_hours_always_refreshes(self) -> None:
        settings = make_settings(stock_refresh_hours=0)
        await check_service.run_check(self._gateway(), settings, today=TODAY)
        gw2 = self._gateway()
        await check_service.run_check(gw2, settings, today=TODAY)
        assert "stock" in gw2.calls


class TestSupersedeInCheck:
    """/tekshir yangi partiya kelganini o'zi payqashi kerak."""

    async def test_new_arrival_closes_the_old_candidate(self) -> None:
        from povtor_bot.core.models import Candidate

        settings = make_settings()
        # 6 kun oldingi band bazada turibdi
        await repo.insert_candidates([Candidate(
            detected_date=TODAY - timedelta(days=2), shop_id="shop1",
            shop_name="ANDALUS", sku="1", color="Белый",
            arrived_date=TODAY - timedelta(days=6), base_qty=5, sold_qty=3,
            percent=60.0, days_to_50=4, grade="oddiy", recommended_qty=5,
            note="60% sotildi", category_group="Poyasnaya",
        )])
        assert await repo.open_count() == 1

        # Sklad bugun yubordi, lekin hali hech nima sotilmagan ->
        # yangi nomzod TOPILMAYDI, eskisi baribir yopilishi kerak
        gateway = FakeGateway(
            transfers=[a_transfer("shop1", "1", "Белый", TODAY, 20)],
            sales=[],
            products=[a_product(sku="1")],
        )
        result = await check_service.run_check(gateway, settings, today=TODAY)
        assert result.total_found == 0
        assert result.superseded == 1
        assert await repo.open_count() == 0

    async def test_both_batches_do_not_stack_up(self) -> None:
        """Yangi partiya ham nomzod bo'lsa, menyuda BITTA band qolishi kerak."""
        from povtor_bot.core.models import Candidate

        await repo.insert_candidates([Candidate(
            detected_date=TODAY - timedelta(days=2), shop_id="shop1",
            shop_name="ANDALUS", sku="1", color="Белый",
            arrived_date=TODAY - timedelta(days=6), base_qty=5, sold_qty=3,
            percent=60.0, days_to_50=4, grade="oddiy", recommended_qty=5,
            note="x", category_group="Poyasnaya",
        )])
        gateway = FakeGateway(
            transfers=[a_transfer("shop1", "1", "Белый", TODAY, 5)],
            sales=[a_sale("shop1", "1", "Белый", TODAY, 5)],
            products=[a_product(sku="1")],
        )
        result = await check_service.run_check(gateway, make_settings(), today=TODAY)
        assert result.total_found == 1
        assert await repo.open_count() == 1          # ikkitasi emas
        assert len(await repo.card_items("1")) == 2  # kartada ikkalasi ko'rinadi


class TestWindowOverride:
    """Menejer /tekshir da oyna va chegarani o'zgartira oladi.

    run_check imzosi o'zgarmagan — u sozlamalarni Settings dan oladi,
    shuning uchun o'zgartirilgan NUSXA uzatiladi.
    """

    def _gateway(self) -> FakeGateway:
        # Turli kunlarda kelgan uch partiya
        return FakeGateway(
            transfers=[
                a_transfer("shop1", "yangi", "Белый", TODAY - timedelta(days=2), 5),
                a_transfer("shop1", "orta", "Белый", TODAY - timedelta(days=5), 5),
                a_transfer("shop1", "eski", "Белый", TODAY - timedelta(days=8), 5),
            ],
            sales=[
                a_sale("shop1", "yangi", "Белый", TODAY, 3),
                a_sale("shop1", "orta", "Белый", TODAY, 3),
                a_sale("shop1", "eski", "Белый", TODAY, 3),
            ],
            products=[a_product(sku=s) for s in ("yangi", "orta", "eski")],
        )

    async def test_wider_window_finds_more(self) -> None:
        besh = make_settings(window_days=5)
        assert (await check_service.run_check(
            self._gateway(), besh, today=TODAY)).total_found == 2

    async def test_ten_days_reaches_the_old_batch(self) -> None:
        settings = make_settings(window_days=10)
        result = await check_service.run_check(self._gateway(), settings, today=TODAY)
        assert result.total_found == 3
        assert {r["sku"] for r in await repo.card_items("eski")} == {"eski"}

    async def test_higher_threshold_finds_fewer(self) -> None:
        # 5 dan 3 = 60%, ya'ni 70% chegara hech kimni o'tkazmaydi
        settings = make_settings(window_days=10, percent_threshold=70.0)
        result = await check_service.run_check(self._gateway(), settings, today=TODAY)
        assert result.total_found == 0

    async def test_window_is_stored_on_the_candidate(self) -> None:
        """Karta "eskirgan" belgisi shu qiymatga qaraydi, umumiy sozlamaga emas."""
        settings = make_settings(window_days=10)
        await check_service.run_check(self._gateway(), settings, today=TODAY)
        rows = await repo.card_items("eski")
        assert rows[0]["window_days"] == 10

    async def test_original_settings_are_not_mutated(self) -> None:
        asl = make_settings(window_days=5)
        nusxa = asl.model_copy(update={"window_days": 10})
        await check_service.run_check(self._gateway(), nusxa, today=TODAY)
        assert asl.window_days == 5


class TestRunScopedMenu:
    """/tekshir tugagach menyu aynan shu tekshiruv natijasini ko'rsatadi."""

    async def test_strict_rule_clears_the_menu(self) -> None:
        settings = make_settings()
        gateway = FakeGateway(
            transfers=[a_transfer("shop1", "1", "Белый", TODAY - timedelta(days=1), 5)],
            sales=[a_sale("shop1", "1", "Белый", TODAY, 3)],     # 60%
            products=[a_product(sku="1")],
        )
        # 50% chegara -> topiladi
        await check_service.run_check(gateway, settings, today=TODAY)
        assert await repo.open_count() == 1

        # 80% chegara -> topilmaydi, menyu bo'shashi kerak
        qattiq = settings.model_copy(update={"percent_threshold": 80.0})
        result = await check_service.run_check(gateway, qattiq, today=TODAY)
        assert result.total_found == 0
        assert await repo.open_count() == 0

        # band bazada saqlanib qoldi
        assert len(await repo.card_items("1")) == 1

    async def test_wider_rule_brings_items_back(self) -> None:
        settings = make_settings()
        gateway = FakeGateway(
            transfers=[a_transfer("shop1", "1", "Белый", TODAY - timedelta(days=1), 5)],
            sales=[a_sale("shop1", "1", "Белый", TODAY, 3)],
            products=[a_product(sku="1")],
        )
        await check_service.run_check(
            gateway, settings.model_copy(update={"percent_threshold": 80.0}), today=TODAY
        )
        assert await repo.open_count() == 0

        await check_service.run_check(gateway, settings, today=TODAY)
        assert await repo.open_count() == 1
