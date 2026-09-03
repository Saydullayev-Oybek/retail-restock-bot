"""db/repo.py — idempotentlik va poyga holatlari.

Nega aynan shu ikkisi: bot kuniga bir necha marta /tekshir ni ishlatadi
(qo'lda + cron), va bir kartani ikki menejer ko'rib turishi mumkin.
Ikkalasi ham "jim buziladigan" xatolar sinfiga kiradi.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from povtor_bot.core.models import STATUS_NOT_FOUND, STATUS_PENDING, STATUS_TAKEN
from povtor_bot.db import repo

from .conftest import TODAY, make_candidate

pytestmark = pytest.mark.usefixtures("database")


class TestInsertCandidates:
    async def test_inserts_new(self) -> None:
        assert await repo.insert_candidates([make_candidate()]) == 1
        assert await repo.open_count(TODAY) == 1

    async def test_second_run_same_day_adds_nothing(self) -> None:
        """/tekshir ni qayta ishlatish dublikat yaratmasligi kerak."""
        candidate = make_candidate()
        assert await repo.insert_candidates([candidate]) == 1
        assert await repo.insert_candidates([candidate]) == 0
        assert await repo.open_count(TODAY) == 1

    async def test_rerun_does_not_erase_answer(self) -> None:
        """Eng muhim: javob berilgan band qayta tekshiruvda 'pending' ga qaytmaydi."""
        await repo.insert_candidates([make_candidate()])
        rows = await repo.card_items("39666", TODAY)
        assert await repo.answer_candidate(rows[0]["id"], status=STATUS_TAKEN, user_id=1)

        await repo.insert_candidates([make_candidate()])
        rows = await repo.card_items("39666", TODAY)
        assert rows[0]["status"] == STATUS_TAKEN

    async def test_same_sku_different_color_and_shop_are_separate(self) -> None:
        await repo.insert_candidates([
            make_candidate(color="Белый"),
            make_candidate(color="Синий"),
            make_candidate(shop_id="shop2", shop_name="BERUNIY"),
        ])
        assert await repo.open_count(TODAY) == 3

    async def test_next_day_does_not_duplicate_the_same_batch(self) -> None:
        """Ertangi /tekshir o'sha partiyani QAYTA yozmasligi kerak.

        Partiya oyna ichida bir necha kun turadi va har kungi tekshiruv uni
        qayta topadi. Agar har safar yangi qator yaratilsa, menejer bir bandni
        bir necha marta ko'rib, bir necha marta buyurtma berib yuborardi.
        """
        await repo.insert_candidates([make_candidate()])
        yangi = await repo.insert_candidates([
            make_candidate(detected_date=TODAY + timedelta(days=1), sold_qty=5)
        ])
        assert yangi == 0
        rows = await repo.card_items("39666")
        assert len(rows) == 1

    async def test_new_arrival_creates_a_new_row(self) -> None:
        """Yangi partiya kelsa — bu yangi qaror, alohida qator."""
        await repo.insert_candidates([make_candidate()])
        yangi = await repo.insert_candidates([
            make_candidate(arrived_date=TODAY, detected_date=TODAY + timedelta(days=1))
        ])
        assert yangi == 1
        assert len(await repo.card_items("39666")) == 2

    async def test_pending_statistics_are_refreshed(self) -> None:
        """Javob berilmagan bandning sotuvi o'sib borishi kerak."""
        await repo.insert_candidates([make_candidate(sold_qty=3, percent=60.0)])
        await repo.insert_candidates([make_candidate(sold_qty=5, percent=100.0)])
        row = (await repo.card_items("39666"))[0]
        assert (row["sold_qty"], row["percent"]) == (5, 100.0)
        # birinchi ko'rsatilgan kun saqlanadi — kartadagi yosh shundan hisoblanadi
        assert row["detected_date"] == TODAY.isoformat()

    async def test_answered_statistics_are_frozen(self) -> None:
        """Javob berilgan band tegilmaydi — hisobot qaror paytidagi raqamni saqlaydi."""
        await repo.insert_candidates([make_candidate(sold_qty=3, percent=60.0)])
        rows = await repo.card_items("39666")
        await repo.answer_candidate(rows[0]["id"], status=STATUS_TAKEN, user_id=1)
        await repo.insert_candidates([make_candidate(sold_qty=5, percent=100.0)])
        row = (await repo.card_items("39666"))[0]
        assert row["status"] == STATUS_TAKEN
        assert (row["sold_qty"], row["percent"]) == (3, 60.0)

    async def test_empty_list(self) -> None:
        assert await repo.insert_candidates([]) == 0


class TestAnswer:
    async def _one_id(self) -> int:
        await repo.insert_candidates([make_candidate()])
        rows = await repo.card_items("39666", TODAY)
        return rows[0]["id"]

    async def test_taken_writes_status_and_user(self) -> None:
        cid = await self._one_id()
        assert await repo.answer_candidate(cid, status=STATUS_TAKEN, user_id=777)
        row = await repo.get_candidate(cid)
        assert row["status"] == STATUS_TAKEN
        assert row["answered_by"] == 777
        assert row["answered_at"] is not None

    async def test_not_found_stores_transfer_hint(self) -> None:
        cid = await self._one_id()
        await repo.answer_candidate(
            cid, status=STATUS_NOT_FOUND, user_id=1, transfer_hint="BERUNIY: 4 dona"
        )
        row = await repo.get_candidate(cid)
        assert row["status"] == STATUS_NOT_FOUND
        assert row["transfer_hint"] == "BERUNIY: 4 dona"

    async def test_second_answer_is_rejected(self) -> None:
        """Ikkinchi bosish False qaytaradi — birinchi javob kuchda qoladi."""
        cid = await self._one_id()
        assert await repo.answer_candidate(cid, status=STATUS_TAKEN, user_id=1)
        assert not await repo.answer_candidate(cid, status=STATUS_NOT_FOUND, user_id=2)
        row = await repo.get_candidate(cid)
        assert row["status"] == STATUS_TAKEN
        assert row["answered_by"] == 1

    async def test_concurrent_answers_write_exactly_one(self) -> None:
        """Ikki menejer bir vaqtda bossa — aynan bittasi yozadi."""
        cid = await self._one_id()
        results = await asyncio.gather(*[
            repo.answer_candidate(cid, status=STATUS_TAKEN, user_id=uid)
            for uid in range(1, 6)
        ])
        assert sum(results) == 1

    async def test_answer_writes_audit_event(self) -> None:
        cid = await self._one_id()
        await repo.answer_candidate(cid, status=STATUS_TAKEN, user_id=42)
        from povtor_bot.db.conn import db
        async with db().execute(
            "SELECT action, user_id FROM item_event WHERE candidate_id = ?", (cid,)
        ) as cursor:
            events = list(await cursor.fetchall())
        assert [(e["action"], e["user_id"]) for e in events] == [(STATUS_TAKEN, 42)]

    async def test_reset_returns_to_pending(self) -> None:
        cid = await self._one_id()
        await repo.answer_candidate(cid, status=STATUS_TAKEN, user_id=1)
        assert await repo.reset_candidate(cid, user_id=1)
        row = await repo.get_candidate(cid)
        assert row["status"] == STATUS_PENDING
        assert row["answered_by"] is None

    async def test_reset_of_pending_does_nothing(self) -> None:
        cid = await self._one_id()
        assert not await repo.reset_candidate(cid, user_id=1)

    async def test_unknown_status_raises(self) -> None:
        cid = await self._one_id()
        with pytest.raises(ValueError):
            await repo.answer_candidate(cid, status="nima_bu", user_id=1)


class TestCascadeMenu:
    async def _seed(self) -> None:
        await repo.insert_candidates([
            make_candidate(category_group="Obuv", supplier="Bektosh M291", sku="40722"),
            make_candidate(category_group="Obuv", supplier="Bektosh M291", sku="40722",
                           color="Синий"),
            make_candidate(category_group="Obuv", supplier="Sherzod New M401", sku="40688"),
            make_candidate(category_group="Poyasnaya", supplier="Sharof M255", sku="39666"),
        ])

    async def test_categories_carry_open_counts(self) -> None:
        await self._seed()
        rows = await repo.categories_with_open_counts(TODAY)
        assert {r["category_group"]: r["open_count"] for r in rows} == {
            "Obuv": 3, "Poyasnaya": 1,
        }

    async def test_answered_items_drop_out_of_counts(self) -> None:
        await self._seed()
        items = await repo.card_items("40722", TODAY)
        await repo.answer_candidate(items[0]["id"], status=STATUS_TAKEN, user_id=1)
        rows = await repo.categories_with_open_counts(TODAY)
        assert {r["category_group"]: r["open_count"] for r in rows}["Obuv"] == 2

    async def test_suppliers_scoped_to_category(self) -> None:
        await self._seed()
        rows = await repo.suppliers_with_open_counts("Obuv", TODAY)
        assert {r["supplier"] for r in rows} == {"Bektosh M291", "Sherzod New M401"}

    async def test_skus_scoped_to_supplier(self) -> None:
        await self._seed()
        rows = await repo.skus_with_open_counts("Obuv", "Bektosh M291", TODAY)
        assert [r["sku"] for r in rows] == ["40722"]
        assert rows[0]["open_count"] == 2
        assert rows[0]["total_qty"] == 20      # 2 x ishonchli(10)

    async def test_card_shows_answered_items_too(self) -> None:
        """Karta hal qilinganlarni ham ko'rsatadi — menejer nima qilganini ko'rsin."""
        await self._seed()
        items = await repo.card_items("40722", TODAY)
        await repo.answer_candidate(items[0]["id"], status=STATUS_TAKEN, user_id=1)
        assert len(await repo.card_items("40722", TODAY)) == 2


class TestStockSnapshot:
    async def test_replace_and_lookup(self) -> None:
        await repo.replace_stock_snapshot([
            ("shop1", "ANDALUS", "39666", "Белый", 0),
            ("shop2", "BERUNIY", "39666", "Белый", 4),
            ("shop3", "MAGNIT", "39666", "Белый", 9),
        ], TODAY)
        rows = await repo.other_shops_with_stock("39666", "Белый", exclude_shop_id="shop1")
        assert [(r["shop_name"], r["quantity"]) for r in rows] == [
            ("MAGNIT", 9), ("BERUNIY", 4),
        ]

    async def test_snapshot_replaces_previous(self) -> None:
        """Qoldiq nolga tushsa eski qator qolib ketmasligi kerak."""
        await repo.replace_stock_snapshot([("shop2", "BERUNIY", "1", "", 5)], TODAY)
        await repo.replace_stock_snapshot([], TODAY)
        assert await repo.other_shops_with_stock("1", "", "shop1") == []

    async def test_limit_is_respected(self) -> None:
        await repo.replace_stock_snapshot([
            (f"shop{i}", f"F{i}", "1", "", i) for i in range(1, 8)
        ], TODAY)
        assert len(await repo.other_shops_with_stock("1", "", "shopX", limit=3)) == 3


class TestProductCache:
    async def test_file_id_survives_billz_resync(self) -> None:
        """Telegram file_id Billz sinxronizatsiyasida o'chib ketmasligi shart."""
        await repo.cache_products([{"sku": "1", "color": "Белый", "image_url": "u1"}])
        await repo.set_file_id("1", "Белый", "AgACAgIAAx...")
        await repo.cache_products([{"sku": "1", "color": "Белый", "image_url": "u2"}])
        row = await repo.get_cached_product("1", "Белый")
        assert row["tg_file_id"] == "AgACAgIAAx..."
        assert row["image_url"] == "u2"

    async def test_empty_image_url_does_not_erase_existing(self) -> None:
        await repo.cache_products([{"sku": "1", "color": "", "image_url": "u1"}])
        await repo.cache_products([{"sku": "1", "color": "", "image_url": ""}])
        row = await repo.get_cached_product("1", "")
        assert row["image_url"] == "u1"

    async def test_image_missing_flag(self) -> None:
        await repo.mark_image_missing("2", "Синий")
        row = await repo.get_cached_product("2", "Синий")
        assert row["image_missing"] == 1
        # file_id kelsa flag tushadi
        await repo.set_file_id("2", "Синий", "fid")
        row = await repo.get_cached_product("2", "Синий")
        assert row["image_missing"] == 0


class TestRef:
    async def test_stable_id_for_same_value(self) -> None:
        first = await repo.ref_id("sup", "ABUSAXIY 8-22 M64")
        second = await repo.ref_id("sup", "ABUSAXIY 8-22 M64")
        assert first == second
        assert await repo.ref_value("sup", first) == "ABUSAXIY 8-22 M64"

    async def test_kinds_do_not_collide(self) -> None:
        cat = await repo.ref_id("cat", "Obuv")
        assert await repo.ref_value("sup", cat) is None

    async def test_unknown_ref(self) -> None:
        assert await repo.ref_value("cat", 99999) is None


class TestAnnounce:
    async def test_filters_already_announced(self) -> None:
        rows = [("2026-08-20", "shop1", "1", "Белый"), ("2026-08-20", "shop1", "2", "")]
        assert await repo.filter_unannounced(rows) == rows
        await repo.mark_announced(rows[:1])
        assert await repo.filter_unannounced(rows) == rows[1:]


class TestExport:
    async def test_only_answered_rows(self) -> None:
        """Export JAVOB BERILGAN kun bo'yicha — aniqlangan kun bo'yicha emas."""
        await repo.insert_candidates([
            make_candidate(sku="1"), make_candidate(sku="2"), make_candidate(sku="3"),
        ])
        for sku, status in (("1", STATUS_TAKEN), ("2", STATUS_NOT_FOUND)):
            rows = await repo.card_items(sku)
            await repo.answer_candidate(rows[0]["id"], status=status, user_id=1)
        # javob hozir yozildi, ya'ni bugungi hisobotga tushadi
        exported = await repo.answered_for_export(date.today())
        assert {r["sku"] for r in exported} == {"1", "2"}

    async def test_detection_day_does_not_decide_the_report(self) -> None:
        """Eski bandga bugun javob berilsa — u BUGUNGI hisobotga tushadi."""
        await repo.insert_candidates([
            make_candidate(sku="eski", detected_date=TODAY - timedelta(days=4))
        ])
        rows = await repo.card_items("eski")
        await repo.answer_candidate(rows[0]["id"], status=STATUS_TAKEN, user_id=1)
        exported = await repo.answered_for_export(date.today())
        assert [r["sku"] for r in exported] == ["eski"]


class TestSupersedeByNewArrival:
    """Sklad yangi partiya yuborsa eski band yopilishi kerak.

    Ilgari bot bandni faqat menejer javob berganda yopardi. Natijada ikkita
    xato holat bor edi:

      * yangi partiya ham nomzod bo'lsa -> menejer bitta filial+rangni IKKI
        MARTA ko'rib, ikki barobar buyurtma berib yuborishi mumkin edi;
      * yangi partiya nomzod bo'lmasa (hali sotilmagan) -> bot hali ham
        "yana N dona ol" deb turardi, sklad esa allaqachon yuborgan edi.
    """

    async def _eski(self, **kw):
        await repo.insert_candidates([
            make_candidate(arrived_date=TODAY - timedelta(days=6), **kw)
        ])
        return (await repo.card_items("39666"))[0]

    async def test_older_batch_is_closed(self) -> None:
        await self._eski()
        yopildi = await repo.supersede_by_new_arrivals(
            [("shop1", "39666", "Белый", TODAY.isoformat())]
        )
        assert yopildi == 1
        assert await repo.open_count() == 0

    async def test_closed_item_leaves_the_menu(self) -> None:
        await self._eski()
        await repo.supersede_by_new_arrivals(
            [("shop1", "39666", "Белый", TODAY.isoformat())]
        )
        assert await repo.categories_with_open_counts() == []

    async def test_closed_item_stays_in_the_database(self) -> None:
        """Band o'chirilmaydi — kartada va tarixda ko'rinib turadi."""
        row = await self._eski()
        await repo.supersede_by_new_arrivals(
            [("shop1", "39666", "Белый", TODAY.isoformat())]
        )
        saqlangan = await repo.get_candidate(row["id"])
        assert saqlangan is not None
        assert saqlangan["superseded_at"] == TODAY.isoformat()
        assert saqlangan["status"] == "pending"

    async def test_same_day_arrival_does_not_close(self) -> None:
        """Faqat KEYINGI partiya yopadi — o'sha kungisi emas."""
        await repo.insert_candidates([make_candidate(arrived_date=TODAY)])
        yopildi = await repo.supersede_by_new_arrivals(
            [("shop1", "39666", "Белый", TODAY.isoformat())]
        )
        assert yopildi == 0
        assert await repo.open_count() == 1

    async def test_answered_items_are_untouched(self) -> None:
        """Menejer javob bergan band tarixda o'z holicha qolishi kerak."""
        row = await self._eski()
        await repo.answer_candidate(row["id"], status=STATUS_TAKEN, user_id=1)
        yopildi = await repo.supersede_by_new_arrivals(
            [("shop1", "39666", "Белый", TODAY.isoformat())]
        )
        assert yopildi == 0
        saqlangan = await repo.get_candidate(row["id"])
        assert saqlangan["status"] == STATUS_TAKEN
        assert saqlangan["superseded_at"] is None

    async def test_other_colors_are_not_affected(self) -> None:
        await self._eski(color="Белый")
        await self._eski(color="Синий")
        await repo.supersede_by_new_arrivals(
            [("shop1", "39666", "Белый", TODAY.isoformat())]
        )
        assert await repo.open_count() == 1

    async def test_repeated_call_is_idempotent(self) -> None:
        await self._eski()
        arrivals = [("shop1", "39666", "Белый", TODAY.isoformat())]
        assert await repo.supersede_by_new_arrivals(arrivals) == 1
        assert await repo.supersede_by_new_arrivals(arrivals) == 0


class TestWindowDaysMigration:
    """`window_days` ustuni mavjud bazalarga qo'shiladi."""

    async def test_column_is_added_and_rows_survive(self, tmp_path) -> None:
        import sqlite3

        from povtor_bot.db import conn

        # Ustunsiz "eski" baza yasaymiz
        path = tmp_path / "eski.db"
        await conn.close()
        await conn.connect(str(path))
        await repo.insert_candidates([make_candidate()])
        await conn.close()

        raw = sqlite3.connect(path)
        raw.executescript("""
            CREATE TABLE tmp AS SELECT * FROM candidate;
            DROP TABLE candidate;
            CREATE TABLE candidate AS SELECT * FROM tmp;
            DROP TABLE tmp;
        """)
        raw.execute("ALTER TABLE candidate DROP COLUMN window_days")
        raw.commit()
        assert "window_days" not in {
            r[1] for r in raw.execute("PRAGMA table_info(candidate)")
        }
        raw.close()

        # Ulanish migratsiyani qo'llashi kerak
        await conn.connect(str(path))
        try:
            async with conn.db().execute("PRAGMA table_info(candidate)") as cur:
                cols = {r["name"] for r in await cur.fetchall()}
            async with conn.db().execute(
                "SELECT COUNT(*) AS n, MIN(window_days) AS w FROM candidate"
            ) as cur:
                row = await cur.fetchone()
        finally:
            await conn.close()

        assert "window_days" in cols
        assert row["n"] == 1 and row["w"] == 5     # sukut qiymat


class TestBrandMaterialMigration:
    """`brand` va `material` mavjud bazalarga qo'shiladi, ma'lumot yo'qolmaydi."""

    async def test_columns_are_added_and_rows_survive(self, tmp_path) -> None:
        import sqlite3

        from povtor_bot.db import conn

        path = tmp_path / "eski.db"
        await conn.close()
        await conn.connect(str(path))
        await repo.insert_candidates([make_candidate(sku="50058")])
        await conn.close()

        raw = sqlite3.connect(path)
        for table in ("candidate", "product_cache", "product_variant"):
            for column in ("brand", "material"):
                raw.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        raw.commit()
        raw.close()

        await conn.connect(str(path))
        try:
            cols = {}
            for table in ("candidate", "product_cache", "product_variant"):
                async with conn.db().execute(f"PRAGMA table_info({table})") as cur:
                    cols[table] = {r["name"] for r in await cur.fetchall()}
            async with conn.db().execute(
                "SELECT sku, brand, material FROM candidate"
            ) as cur:
                row = await cur.fetchone()
        finally:
            await conn.close()

        for table in ("candidate", "product_cache", "product_variant"):
            assert {"brand", "material"} <= cols[table], table
        assert row["sku"] == "50058"
        assert row["brand"] == "" and row["material"] == ""

    async def test_stored_and_read_back(self, database) -> None:
        await repo.insert_candidates(
            [make_candidate(brand="Salvatini", material="Комбинация · Замш/Кожа")]
        )
        rows = await repo.card_items("39666")
        assert rows[0]["brand"] == "Salvatini"
        assert rows[0]["material"] == "Комбинация · Замш/Кожа"


class TestImageResyncDataMigration:
    """Rasm manzili to'liq URL'ga o'tgani uchun artikullar bir marta qayta o'qiladi."""

    async def test_sku_sync_cleared_once(self, tmp_path) -> None:
        from povtor_bot.db import conn

        path = tmp_path / "resync.db"
        await conn.close()
        await conn.connect(str(path))
        await repo.mark_sku_synced("39666", 3)
        assert await repo.stale_skus(["39666"], 7) == []
        await conn.close()

        # Belgini olib tashlaymiz — migratsiya hali bajarilmagan holat
        async def _drop_flag() -> None:
            await conn.db().execute(
                "DELETE FROM kv WHERE key = 'resync_2026_09_full_image_url'"
            )
            await conn.db().commit()

        await conn.connect(str(path))
        await _drop_flag()
        await conn.close()

        await conn.connect(str(path))
        try:
            assert await repo.stale_skus(["39666"], 7) == ["39666"]
            # Ikkinchi ulanishda takrorlanmaydi
            await repo.mark_sku_synced("39666", 3)
        finally:
            await conn.close()

        await conn.connect(str(path))
        try:
            assert await repo.stale_skus(["39666"], 7) == []
        finally:
            await conn.close()


class TestMenuShowsOnlyTheLastRun:
    """Menyu OXIRGI tekshiruv natijasini ko'rsatadi.

    Menejer /tekshir da qoidani (oyna, chegara) o'zi tanlaydi — ro'yxat aynan
    shu qoidaga javob berishi kerak. Ilgari eski tekshiruvlar natijasi
    to'planib borardi: 3 kun / 70% bilan 0 ta topilsa ham menyuda 101 ta
    band turaverardi.
    """

    async def _run(self, *candidates):
        run_id = await repo.next_run_id()
        await repo.insert_candidates(list(candidates), run_id)
        await repo.finish_run(run_id)
        return run_id

    async def test_previous_run_disappears(self) -> None:
        await self._run(make_candidate(sku="eski"))
        assert await repo.open_count() == 1

        await self._run(make_candidate(sku="yangi"))
        assert await repo.open_count() == 1
        rows = await repo.categories_with_open_counts()
        assert sum(r["open_count"] for r in rows) == 1

    async def test_empty_run_empties_the_menu(self) -> None:
        """Hech nima topilmasa menyu ham bo'sh bo'lishi kerak."""
        await self._run(make_candidate(sku="1"), make_candidate(sku="2"))
        assert await repo.open_count() == 2

        await self._run()          # qattiq qoida, hech nima topilmadi
        assert await repo.open_count() == 0
        assert await repo.categories_with_open_counts() == []

    async def test_item_found_again_stays(self) -> None:
        """O'sha band qayta topilsa ro'yxatda qoladi."""
        await self._run(make_candidate(sold_qty=3, percent=60.0))
        await self._run(make_candidate(sold_qty=5, percent=100.0))
        assert await repo.open_count() == 1
        row = (await repo.card_items("39666"))[0]
        assert row["sold_qty"] == 5           # statistikasi ham yangilandi

    async def test_answered_items_are_kept_in_the_database(self) -> None:
        """Menyudan chiqqan band YO'QOLMAYDI — javoblar va tarix qoladi."""
        await self._run(make_candidate(sku="1"))
        rows = await repo.card_items("1")
        await repo.answer_candidate(rows[0]["id"], status=STATUS_TAKEN, user_id=1)

        await self._run(make_candidate(sku="2"))
        saqlangan = await repo.get_candidate(rows[0]["id"])
        assert saqlangan is not None and saqlangan["status"] == STATUS_TAKEN

    async def test_unanswered_item_returns_when_found_again(self) -> None:
        """Kengroq qoida bilan qayta tekshirilsa eski band qaytadi."""
        await self._run(make_candidate(sku="1"))
        await self._run(make_candidate(sku="2"))
        assert {r["sku"] for r in await repo.card_items("1")} == {"1"}   # bazada bor
        assert await repo.open_count() == 1                              # menyuda yo'q

        await self._run(make_candidate(sku="1"), make_candidate(sku="2"))
        assert await repo.open_count() == 2                              # ikkalasi qaytdi

    async def test_no_run_yet_means_empty_menu(self) -> None:
        assert await repo.current_run_id() == 0
        assert await repo.categories_with_open_counts() == []
