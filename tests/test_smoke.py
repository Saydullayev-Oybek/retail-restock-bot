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


class TestRawLogPolicy:
    """Xom javob saqlash siyosati.

    Har bir muvaffaqiyatli javobni saqlash bazani shishiradi — bir kunlik
    sinovda billz_raw 168 MB bo'lgan (butun bazaning 93% i), va hammasi
    HTTP 200 edi. Muammo chiqqanda kerak bo'ladigani — xato javob.
    """

    async def _sink_for(self, **overrides):
        from povtor_bot.config import Settings
        from povtor_bot.main import build_billz

        base = dict(bot_token="T", billz_secret_token="s")
        base.update(overrides)
        client, _ = build_billz(Settings(_env_file=None, **base))  # type: ignore[arg-type]
        sink = client._raw_sink
        await client.aclose()
        return sink

    async def test_errors_are_saved(self, tmp_path) -> None:
        from povtor_bot.db import conn
        from povtor_bot.db.conn import db

        await conn.close()
        await conn.connect(str(tmp_path / "raw.db"))
        try:
            sink = await self._sink_for()
            await sink("/v1/shop", {}, 500, "internal boom")
            async with db().execute("SELECT status, body FROM billz_raw") as cur:
                rows = list(await cur.fetchall())
            assert [(r["status"], r["body"]) for r in rows] == [(500, "internal boom")]
        finally:
            await conn.close()

    async def test_successful_responses_are_not_saved(self, tmp_path) -> None:
        from povtor_bot.db import conn
        from povtor_bot.db.conn import db

        await conn.close()
        await conn.connect(str(tmp_path / "raw2.db"))
        try:
            sink = await self._sink_for()
            for _ in range(5):
                await sink("/v1/shop", {}, 200, "x" * 100_000)
            async with db().execute("SELECT COUNT(*) AS n FROM billz_raw") as cur:
                assert (await cur.fetchone())["n"] == 0
        finally:
            await conn.close()

    async def test_debug_mode_saves_everything(self, tmp_path) -> None:
        from povtor_bot.db import conn
        from povtor_bot.db.conn import db

        await conn.close()
        await conn.connect(str(tmp_path / "raw3.db"))
        try:
            sink = await self._sink_for(billz_raw_log_all=True)
            await sink("/v1/shop", {}, 200, "ok")
            async with db().execute("SELECT COUNT(*) AS n FROM billz_raw") as cur:
                assert (await cur.fetchone())["n"] == 1
        finally:
            await conn.close()


class TestSingletonLock:
    """Bitta nusxa qulfi.

    Sinov davrida 10 ta jarayon bir vaqtda ishlab qolgan (eng eskisi 22 soat).
    Telegram bitta token bilan faqat bitta getUpdates iste'molchisiga ruxsat
    beradi, va har nusxada o'z cron'i bo'ladi — N barobar Billz yuki.
    """

    def test_second_acquire_is_rejected(self, tmp_path) -> None:
        import subprocess
        import sys

        from povtor_bot.singleton import acquire

        lock = str(tmp_path / "bot.lock")
        acquire(lock)

        # Ikkinchi nusxa ALOHIDA jarayonda bo'lishi kerak: flock bitta jarayon
        # ichida qayta olinaveradi
        code = (
            "import sys; sys.path.insert(0, %r);\n"
            "from povtor_bot.singleton import acquire, AlreadyRunning\n"
            "try:\n"
            "    acquire(%r); print('OLDI')\n"
            "except AlreadyRunning:\n"
            "    print('RAD')\n" % (str(__import__('pathlib').Path.cwd()), lock)
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
        )
        assert out.stdout.strip() == "RAD", out.stderr

    def test_lock_file_holds_the_pid(self, tmp_path) -> None:
        import os

        from povtor_bot.singleton import acquire

        lock = tmp_path / "pid.lock"
        acquire(str(lock))
        assert lock.read_text().strip() == str(os.getpid())


class TestBackup:
    async def test_backup_is_a_usable_copy(self, tmp_path) -> None:
        from povtor_bot.db import conn, repo
        from povtor_bot.services import backup

        await conn.close()
        await conn.connect(str(tmp_path / "src.db"))
        try:
            from .conftest import make_candidate

            await repo.insert_candidates([make_candidate()])
            path = await backup.make_backup(str(tmp_path / "bk"), keep_days=14)
            assert path is not None and path.exists()
        finally:
            await conn.close()

        # Nusxa mustaqil ishlashi kerak
        import sqlite3

        c = sqlite3.connect(path)
        assert c.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 1
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        c.close()

    async def test_old_backups_are_purged(self, tmp_path) -> None:
        from datetime import date, timedelta

        from povtor_bot.db import conn
        from povtor_bot.services import backup

        bk = tmp_path / "bk"
        bk.mkdir()
        eski = bk / f"povtor-{(date.today() - timedelta(days=30)).isoformat()}.db"
        yangi = bk / f"povtor-{(date.today() - timedelta(days=2)).isoformat()}.db"
        eski.write_text("x")
        yangi.write_text("x")

        await conn.close()
        await conn.connect(str(tmp_path / "s.db"))
        try:
            await backup.make_backup(str(bk), keep_days=14)
        finally:
            await conn.close()
        assert not eski.exists()
        assert yangi.exists()


class TestSuspendedProcessHint:
    """Ctrl+Z bosilgan bot qulfni ushlab turadi, lekin ishlamaydi.

    Tashqaridan bu "bot ishlayapti, lekin javob bermayapti" bo'lib ko'rinadi
    va sababini topish qiyin — real holatda aynan shunday bo'lgan.
    """

    def test_stopped_process_is_explained(self, monkeypatch, tmp_path) -> None:
        from povtor_bot import singleton

        monkeypatch.setattr(singleton, "_process_state", lambda pid: "T")
        exc = singleton.AlreadyRunning("123", tmp_path / "x.lock")
        matn = str(exc)
        assert "TO'XTATILGAN" in matn and "Ctrl+Z" in matn
        assert "kill -CONT 123" in matn      # davom ettirish yo'li

    def test_running_process_gets_the_plain_hint(self, monkeypatch, tmp_path) -> None:
        from povtor_bot import singleton

        monkeypatch.setattr(singleton, "_process_state", lambda pid: "S+")
        matn = str(singleton.AlreadyRunning("123", tmp_path / "x.lock"))
        assert "TO'XTATILGAN" not in matn
        assert "kill 123" in matn

    def test_unknown_state_does_not_break(self, monkeypatch, tmp_path) -> None:
        from povtor_bot import singleton

        monkeypatch.setattr(singleton, "_process_state", lambda pid: "")
        assert "kill 123" in str(singleton.AlreadyRunning("123", tmp_path / "x.lock"))
