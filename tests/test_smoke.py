"""Ishga tushish smoke-testi.

Tarmoqqa chiqmasdan: sozlamalar o'qiladimi, baza sxemasi qo'llanadimi,
router'lar ro'yxatga olinadimi, kaskad callback'lari 64 bayt chegarasiga
sig'adimi.
"""

from __future__ import annotations

from povtor_bot.bot.callbacks import AnswerCB, CategoryCB, NavCB, SkuCB, SupplierCB
from povtor_bot.db import conn


class TestRouters:
    def test_routers_register_without_conflicts(self) -> None:
        from povtor_bot.config import Settings

        from .conftest import build_dispatcher

        dispatcher = build_dispatcher(
            None, Settings(_env_file=None, bot_token="T")  # type: ignore[call-arg]
        )
        assert {r.name for r in dispatcher.sub_routers} == {"commands", "menu"}

    def test_every_command_is_declared_in_menu(self) -> None:
        """/start dan tashqari har bir handler Telegram buyruqlar menyusida bo'lsin."""
        from povtor_bot.main import BOT_COMMANDS

        declared = {c.command for c in BOT_COMMANDS}
        assert declared == {"tekshir", "buyurtma", "yangi", "export"}


class TestCallbackSize:
    """callback_data 64 baytdan oshsa Telegram xabarni RAD ETADI."""

    def test_all_callbacks_fit(self) -> None:
        packed = [
            CategoryCB(ref=999_999).pack(),
            SupplierCB(cat=999_999, ref=999_999).pack(),
            SkuCB(cat=999_999, sup=999_999, ref=999_999).pack(),
            AnswerCB(id=99_999_999, act="n", cat=999_999, sup=999_999).pack(),
            NavCB(to="art", cat=999_999, sup=999_999).pack(),
        ]
        for data in packed:
            assert len(data.encode("utf-8")) <= 64, f"juda uzun: {data}"

    def test_roundtrip_preserves_values(self) -> None:
        original = AnswerCB(id=123, act="t", cat=4, sup=5)
        assert AnswerCB.unpack(original.pack()) == original


class TestSchema:
    async def test_schema_applies_and_is_idempotent(self, tmp_path) -> None:
        path = str(tmp_path / "smoke.db")
        await conn.close()
        await conn.connect(path)
        async with conn.db().execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cursor:
            tables = {r["name"] for r in await cursor.fetchall()}
        await conn.close()

        assert {
            "candidate", "product_cache", "card_msg", "item_event",
            "announced_arrival", "stock_snapshot", "ref", "billz_raw", "kv",
        } <= tables

        # Ikkinchi marta ulanish xato bermasligi kerak (CREATE IF NOT EXISTS)
        await conn.connect(path)
        await conn.close()

    async def test_wal_mode_enabled(self, tmp_path) -> None:
        """WAL: yozuv va o'qish bir-birini bloklamasin."""
        await conn.close()
        await conn.connect(str(tmp_path / "wal.db"))
        async with conn.db().execute("PRAGMA journal_mode") as cursor:
            mode = (await cursor.fetchone())[0]
        await conn.close()
        assert mode.lower() == "wal"
