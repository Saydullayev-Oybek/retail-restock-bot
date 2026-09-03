"""SQLite ulanishi.

Nega bitta uzoq yashovchi ulanish: SQLite'da yozuvni baribir bitta yozuvchi
bajaradi, va aiosqlite har bir ulanish uchun alohida thread ochadi. Bitta
ulanish + WAL rejimi bu yuk uchun eng sodda va bashorat qilinadigan yechim.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_conn: aiosqlite.Connection | None = None
# Yozuv operatsiyalarini ketma-ket qilish uchun — "database is locked" ni oldini oladi
_write_lock = asyncio.Lock()


async def connect(db_path: str) -> aiosqlite.Connection:
    """Ulanadi va sxemani qo'llaydi (CREATE TABLE IF NOT EXISTS — idempotent)."""
    global _conn
    if _conn is not None:
        return _conn

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    await _migrate(conn)
    await conn.commit()
    _conn = conn
    return conn


# Mavjud bazalarga qo'shilishi kerak bo'lgan ustunlar.
# CREATE TABLE IF NOT EXISTS eski jadvalni o'zgartirmaydi, shuning uchun
# yangi ustun alohida qo'shiladi.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("candidate", "superseded_at", "TEXT"),
    ("candidate", "window_days", "INTEGER NOT NULL DEFAULT 5"),
    ("candidate", "last_run", "INTEGER NOT NULL DEFAULT 0"),
    ("candidate", "brand", "TEXT NOT NULL DEFAULT ''"),
    ("candidate", "material", "TEXT NOT NULL DEFAULT ''"),
    ("product_cache", "brand", "TEXT NOT NULL DEFAULT ''"),
    ("product_cache", "material", "TEXT NOT NULL DEFAULT ''"),
    ("product_variant", "brand", "TEXT NOT NULL DEFAULT ''"),
    ("product_variant", "material", "TEXT NOT NULL DEFAULT ''"),
    ("candidate", "reopened_at", "TEXT"),
)

# Bir marta bajariladigan ma'lumot migratsiyalari: (kalit, SQL).
# Bajarilgani `kv` da belgilanadi, shuning uchun har ishga tushishda takrorlanmaydi.
_DATA_MIGRATIONS: tuple[tuple[str, str], ...] = (
    # Eski keshda rasm sifatida faqat fayl nomi ("<uuid>.jpg") saqlangan, va u
    # BILLZ_IMAGE_BASE_URL sozlanmagani uchun hech qachon rasmga aylanmagan.
    # Endi katalogdan to'liq URL olinadi — buning uchun artikullar qayta
    # o'qilishi kerak. sku_sync ni tozalash keyingi /tekshir da shuni majbur
    # qiladi. Yon foydasi: brend va material ham o'sha o'qishda to'ladi.
    ("resync_2026_09_full_image_url", "DELETE FROM sku_sync"),
)


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Yetishmayotgan ustunlarni qo'shadi. Ma'lumot yo'qolmaydi."""
    for table, column, decl in _MIGRATIONS:
        async with conn.execute(f"PRAGMA table_info({table})") as cursor:
            mavjud = {row["name"] for row in await cursor.fetchall()}
        if column not in mavjud:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    await _migrate_data(conn)


async def _migrate_data(conn: aiosqlite.Connection) -> None:
    """Bir martalik ma'lumot tuzatishlari — `kv` dagi belgi bo'yicha."""
    for key, sql in _DATA_MIGRATIONS:
        async with conn.execute("SELECT 1 FROM kv WHERE key = ?", (key,)) as cursor:
            if await cursor.fetchone() is not None:
                continue
        await conn.execute(sql)
        await conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, datetime('now'))", (key,)
        )


def db() -> aiosqlite.Connection:
    """Ochiq ulanishni qaytaradi. connect() dan oldin chaqirilsa — dasturchi xatosi."""
    if _conn is None:
        raise RuntimeError("Baza ulanmagan: avval db.conn.connect() ni chaqiring")
    return _conn


def write_lock() -> asyncio.Lock:
    return _write_lock


async def close() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None
