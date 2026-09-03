"""Barcha SQL shu yerda. ORM yo'q.

Nega ORM emas: so'rovlarning ko'pchiligi agregatsiya bilan ("kategoriyada nechta
hal qilinmagan band bor") — bunday mantiq toza SQL'da qisqaroq va o'qishga
oson. Bundan tashqari, javob yozishdagi poyga holati (ikki menejer bir vaqtda
bosishi) shartli UPDATE bilan hal qilinadi, ORM esa buni yashiradi.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

import aiosqlite

from ..core.models import (
    STATUS_NOT_FOUND,
    STATUS_PENDING,
    STATUS_TAKEN,
    Candidate,
)
from .conn import db, write_lock

# ───────────────────────────── kv ─────────────────────────────


async def kv_get(key: str) -> str | None:
    async with db().execute("SELECT value FROM kv WHERE key = ?", (key,)) as cursor:
        row = await cursor.fetchone()
    return row["value"] if row else None


async def kv_set(key: str, value: str) -> None:
    async with write_lock():
        await db().execute(
            """
            INSERT INTO kv (key, value, updated_at) VALUES (?, ?, datetime('now'))
            ON CONFLICT (key) DO UPDATE SET value = excluded.value,
                                            updated_at = excluded.updated_at
            """,
            (key, value),
        )
        await db().commit()


# ───────────────────────────── billz_raw ─────────────────────────────


async def save_raw(endpoint: str, params: dict[str, Any], status: int, body: str) -> None:
    """Xom javobni saqlaydi. Juda uzun javoblar kesiladi — bu faqat debug uchun."""
    async with write_lock():
        await db().execute(
            "INSERT INTO billz_raw (endpoint, params, status, body) VALUES (?, ?, ?, ?)",
            (endpoint, json.dumps(params, ensure_ascii=False), status, body[:200_000]),
        )
        await db().commit()


async def purge_raw(days: int) -> int:
    async with write_lock():
        cursor = await db().execute(
            "DELETE FROM billz_raw WHERE created_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        await db().commit()
    return cursor.rowcount or 0


# ───────────────────────────── product_cache ─────────────────────────────


async def cache_products(rows: Iterable[dict[str, Any]]) -> int:
    """Tovar ma'lumotini keshlaydi.

    tg_file_id VA image_missing ataylab yangilanmaydi: ular Telegram tomonidan
    hosil bo'ladi, Billz sinxronizatsiyasi ularni o'chirib yubormasligi kerak.
    """
    payload = [
        (
            row["sku"], row.get("color", ""), row.get("product_id", ""),
            row.get("product_name", ""), row.get("category_group", ""),
            row.get("subcategory", ""), row.get("kind", ""),
            row.get("brand", ""), row.get("material", ""), row.get("supplier", ""),
            row.get("image_url", ""), float(row.get("supply_price", 0) or 0),
            row.get("supply_currency", "UZS") or "UZS",
        )
        for row in rows
    ]
    if not payload:
        return 0
    async with write_lock():
        await db().executemany(
            """
            INSERT INTO product_cache
                (sku, color, product_id, product_name, category_group, subcategory,
                 kind, brand, material, supplier, image_url, supply_price,
                 supply_currency, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT (sku, color) DO UPDATE SET
                product_id      = excluded.product_id,
                product_name    = excluded.product_name,
                category_group  = excluded.category_group,
                subcategory     = excluded.subcategory,
                kind            = excluded.kind,
                brand           = excluded.brand,
                material        = excluded.material,
                supplier        = excluded.supplier,
                -- yangi manzil bo'sh bo'lsa eskisi saqlanadi
                image_url       = CASE WHEN excluded.image_url <> ''
                                       THEN excluded.image_url ELSE product_cache.image_url END,
                supply_price    = excluded.supply_price,
                supply_currency = excluded.supply_currency,
                updated_at      = datetime('now')
            """,
            payload,
        )
        await db().commit()
    return len(payload)


async def get_cached_product(sku: str, color: str) -> aiosqlite.Row | None:
    async with db().execute(
        "SELECT * FROM product_cache WHERE sku = ? AND color = ?", (sku, color)
    ) as cursor:
        return await cursor.fetchone()


async def set_file_id(sku: str, color: str, file_id: str) -> None:
    """Telegram qaytargan file_id — rasm boshqa hech qachon yuklab olinmaydi."""
    async with write_lock():
        await db().execute(
            """
            INSERT INTO product_cache (sku, color, tg_file_id)
            VALUES (?, ?, ?)
            ON CONFLICT (sku, color) DO UPDATE SET tg_file_id = excluded.tg_file_id,
                                                   image_missing = 0,
                                                   updated_at = datetime('now')
            """,
            (sku, color, file_id),
        )
        await db().commit()


async def clear_file_id(sku: str, color: str) -> None:
    """Eskirgan file_id ni tozalaydi — rasm keyingi safar URL'dan qayta yuklanadi.

    Nega mark_image_missing emas: file_id eskirgani rasm yo'q degani emas,
    Telegram shunchaki o'sha havolani unutgan bo'lishi mumkin.
    """
    async with write_lock():
        await db().execute(
            "UPDATE product_cache SET tg_file_id = '', updated_at = datetime('now') "
            "WHERE sku = ? AND color = ?",
            (sku, color),
        )
        await db().commit()


async def mark_image_missing(sku: str, color: str) -> None:
    """Rasm topilmadi — keyingi safar qayta so'ralmasin.

    Billz'ning /v2/products chaqiruvi 5 daqiqada 1 marta tavsiya etilgan, har
    karta ochilganda qayta urinish rate limit'ni yeb qo'yadi.
    """
    async with write_lock():
        await db().execute(
            """
            INSERT INTO product_cache (sku, color, image_missing)
            VALUES (?, ?, 1)
            ON CONFLICT (sku, color) DO UPDATE SET image_missing = 1,
                                                   updated_at = datetime('now')
            """,
            (sku, color),
        )
        await db().commit()


# ───────────────────────────── product_variant / sku_sync ─────────────────────────────


async def save_variants(rows: Sequence[dict[str, Any]]) -> int:
    """product_id -> (artikul, rang, ...) xaritasini yangilaydi."""
    if not rows:
        return 0
    payload = [
        (
            row["product_id"], row["sku"], row.get("color", ""),
            row.get("subcategory", ""), row.get("kind", ""),
            row.get("brand", ""), row.get("material", ""), row.get("supplier", ""),
            row.get("product_name", ""), row.get("category_group", ""),
            row.get("image_file", ""),
        )
        for row in rows
        if row.get("product_id")
    ]
    if not payload:
        return 0
    async with write_lock():
        await db().executemany(
            """
            INSERT INTO product_variant
                (product_id, sku, color, subcategory, kind, brand, material,
                 supplier, product_name, category_group, image_file, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT (product_id) DO UPDATE SET
                sku = excluded.sku, color = excluded.color,
                subcategory = excluded.subcategory, kind = excluded.kind,
                brand = excluded.brand, material = excluded.material,
                supplier = excluded.supplier, product_name = excluded.product_name,
                category_group = excluded.category_group,
                image_file = CASE WHEN excluded.image_file <> ''
                                  THEN excluded.image_file ELSE product_variant.image_file END,
                updated_at = datetime('now')
            """,
            payload,
        )
        await db().commit()
    return len(payload)


async def variant_map() -> dict[str, aiosqlite.Row]:
    """product_id -> qator. Transfer/sotuv hisobotini rangga bog'lash uchun."""
    async with db().execute("SELECT * FROM product_variant") as cursor:
        return {row["product_id"]: row for row in await cursor.fetchall()}


async def variants_of_sku(sku: str) -> list[aiosqlite.Row]:
    async with db().execute(
        "SELECT * FROM product_variant WHERE sku = ? ORDER BY color", (sku,)
    ) as cursor:
        return list(await cursor.fetchall())


async def stale_skus(skus: Sequence[str], max_age_days: int) -> list[str]:
    """Katalogdan qayta o'qish kerak bo'lgan artikullar.

    Nega: 76 000+ tovarli katalogni har tekshiruvda tortib bo'lmaydi, lekin
    yangi artikullar paydo bo'lib turadi. Shu sababli har artikul bir marta
    o'qiladi va `max_age_days` davomida qayta so'ralmaydi.
    """
    if not skus:
        return []
    async with db().execute(
        "SELECT sku FROM sku_sync WHERE synced_at >= datetime('now', ?)",
        (f"-{int(max_age_days)} days",),
    ) as cursor:
        fresh = {row["sku"] for row in await cursor.fetchall()}
    # Tartib saqlanadi va dublikat tashlanadi
    seen: set[str] = set()
    result: list[str] = []
    for sku in skus:
        if sku and sku not in fresh and sku not in seen:
            seen.add(sku)
            result.append(sku)
    return result


async def mark_sku_synced(sku: str, variants: int) -> None:
    async with write_lock():
        await db().execute(
            """
            INSERT INTO sku_sync (sku, synced_at, variants)
            VALUES (?, datetime('now'), ?)
            ON CONFLICT (sku) DO UPDATE SET synced_at = datetime('now'),
                                            variants = excluded.variants
            """,
            (sku, variants),
        )
        await db().commit()


# ───────────────────────────── candidate ─────────────────────────────

_CANDIDATE_COLUMNS = (
    "detected_date, shop_id, shop_name, category_group, subcategory, kind, product_name, "
    "brand, material, sku, color, supplier, product_id, image_url, supply_price, "
    "supply_currency, price_uzs, base_qty, sold_qty, percent, days_to_50, grade, "
    "recommended_qty, note, arrived_date, window_days, last_run"
)


async def next_run_id() -> int:
    """Keyingi tekshiruv raqami.

    Menyu faqat eng oxirgi tekshiruv natijasini ko'rsatadi, shuning uchun har
    tekshiruvga o'z raqami kerak. Vaqt tamg'asi emas, sanoq: ikki tekshiruv
    bir soniyada tugasa ham ular ajralib turadi.
    """
    raw = await kv_get("check_run_seq")
    return int(raw or 0) + 1


async def finish_run(run_id: int) -> None:
    """Tekshiruv tugadi — menyu shu raqamdagi bandlarni ko'rsatadi.

    Nomzod TOPILMASA ham chaqiriladi: bo'sh natija ham natija, va menyu
    eski tekshiruvni ko'rsatib turmasligi kerak.
    """
    await kv_set("check_run_seq", str(run_id))


async def current_run_id() -> int:
    raw = await kv_get("check_run_seq")
    return int(raw or 0)


async def insert_candidates(
    candidates: Sequence[Candidate], run_id: int | None = None
) -> int:
    """Nomzodlarni yozadi. Qaytadi: YANGI qo'shilganlar soni.

    Uch xil holat:

    * yangi partiya                -> qator qo'shiladi
    * mavjud partiya, javob berilmagan -> statistikasi YANGILANADI
      (sotuv o'sib boradi, menejer eskirgan raqamni ko'rmasligi kerak)
    * mavjud partiya, javob berilgan   -> TEGILMAYDI

    Oxirgisi eng muhimi: /tekshir kuniga necha marta ishlasa ham va ertaga
    ham qayta ishlaganda, menejer bergan javob yo'qolmaydi.

    `detected_date` ataylab yangilanmaydi — u "bu bandni birinchi marta qachon
    ko'rsatgan edik" degan ma'noni saqlaydi, kartadagi yosh shundan hisoblanadi.
    """
    # run_id berilmasa o'z tekshiruvimizni ochib yopamiz. /tekshir uni
    # ataylab beradi: yangi partiyalarni yopish va nomzodlarni yozish BITTA
    # tekshiruvga tegishli bo'lishi kerak.
    auto = run_id is None
    if auto:
        run_id = await next_run_id()

    if not candidates:
        if auto:
            await finish_run(run_id)
        return 0
    payload = [
        (
            c.detected_date.isoformat(), c.shop_id, c.shop_name, c.category_group,
            c.subcategory, c.kind, c.product_name, c.brand, c.material, c.sku, c.color,
            c.supplier, c.product_id, c.image_url, c.supply_price, c.supply_currency,
            c.price_uzs, c.base_qty, c.sold_qty, c.percent, c.days_to_50, c.grade,
            c.recommended_qty, c.note, c.arrived_date.isoformat(), c.window_days, run_id,
        )
        for c in candidates
    ]
    placeholders = ", ".join(["?"] * len(_CANDIDATE_COLUMNS.split(",")))
    async with write_lock():
        cursor = await db().execute("SELECT COUNT(*) AS n FROM candidate")
        before = (await cursor.fetchone())["n"]
        await db().executemany(
            f"""
            INSERT INTO candidate ({_CANDIDATE_COLUMNS}) VALUES ({placeholders})
            ON CONFLICT (shop_id, sku, color, arrived_date) DO UPDATE SET
                base_qty        = excluded.base_qty,
                sold_qty        = excluded.sold_qty,
                percent         = excluded.percent,
                days_to_50      = excluded.days_to_50,
                grade           = excluded.grade,
                recommended_qty = excluded.recommended_qty,
                note            = excluded.note,
                price_uzs       = excluded.price_uzs,
                product_name    = excluded.product_name,
                brand           = excluded.brand,
                material        = excluded.material,
                image_url       = excluded.image_url,
                window_days     = excluded.window_days,
                last_run        = excluded.last_run
            WHERE candidate.status = '{STATUS_PENDING}'
            """,
            payload,
        )
        cursor = await db().execute("SELECT COUNT(*) AS n FROM candidate")
        after = (await cursor.fetchone())["n"]
        await db().commit()
    if auto:
        await finish_run(run_id)
    return after - before


async def supersede_by_new_arrivals(
    arrivals: Sequence[tuple[str, str, str, str]]
) -> int:
    """Yangi partiya kelgan bandlarni yopadi.

    arrivals — (shop_id, sku, color, arrived_date) oxirgi kelishlar.

    Nega kerak: bot bandni faqat menejer javob berganda yopardi. Agar sklad
    o'zi yangi partiya yuborsa, eski band menyuda "yana N dona ol" deb
    turaverardi — ehtiyoj allaqachon qondirilgan bo'lsa ham. Bundan tashqari
    yangi partiya ham nomzod bo'lsa, menejer bitta filial+rangni IKKI MARTA
    ko'rib, ikki barobar buyurtma berib yuborishi mumkin edi.

    Faqat javob berilmagan bandlar yopiladi: menejer allaqachon "OLINDI"
    deb belgilagan band tarixda o'z holicha qolishi kerak.
    """
    if not arrivals:
        return 0
    async with write_lock():
        cursor = await db().executemany(
            f"""
            UPDATE candidate
               SET superseded_at = ?
             WHERE shop_id = ? AND sku = ? AND color = ?
               AND arrived_date < ?
               AND status = '{STATUS_PENDING}'
               AND superseded_at IS NULL
            """,
            [(arrived, shop, sku, color, arrived)
             for shop, sku, color, arrived in arrivals],
        )
        yopildi = cursor.rowcount or 0
        await db().commit()
    return yopildi


async def categories_with_open_counts(detected_date: date | None = None) -> list[aiosqlite.Row]:
    """Menyuning 1-darajasi: kategoriya + hal qilinmagan bandlar soni."""
    where, params = _date_filter(detected_date)
    async with db().execute(
        f"""
        SELECT category_group, COUNT(*) AS open_count
        FROM candidate
        WHERE {_OPEN} {where}
        GROUP BY category_group
        HAVING open_count > 0
        ORDER BY open_count DESC, category_group
        """,
        params,
    ) as cursor:
        return list(await cursor.fetchall())


async def suppliers_with_open_counts(
    category_group: str, detected_date: date | None = None
) -> list[aiosqlite.Row]:
    """2-daraja: tanlangan kategoriyadagi ta'minotchilar."""
    where, params = _date_filter(detected_date)
    async with db().execute(
        f"""
        SELECT supplier, COUNT(*) AS open_count
        FROM candidate
        WHERE {_OPEN} AND category_group = ? {where}
        GROUP BY supplier
        HAVING open_count > 0
        ORDER BY open_count DESC, supplier
        """,
        (category_group, *params),
    ) as cursor:
        return list(await cursor.fetchall())


async def skus_with_open_counts(
    category_group: str, supplier: str, detected_date: date | None = None
) -> list[aiosqlite.Row]:
    """3-daraja: ta'minotchining artikullari."""
    where, params = _date_filter(detected_date)
    async with db().execute(
        f"""
        SELECT sku,
               COUNT(*)                       AS open_count,
               MAX(product_name)              AS product_name,
               SUM(recommended_qty)           AS total_qty
        FROM candidate
        WHERE {_OPEN}
          AND category_group = ? AND supplier = ? {where}
        GROUP BY sku
        HAVING open_count > 0
        ORDER BY total_qty DESC, sku
        """,
        (category_group, supplier, *params),
    ) as cursor:
        return list(await cursor.fetchall())


async def card_items(sku: str, detected_date: date | None = None) -> list[aiosqlite.Row]:
    """4-daraja: artikulning barcha filial+rang bandlari (hal qilinganlari ham).

    Hal qilinganlari ham ko'rsatiladi — menejer nima qilganini ko'rib turishi
    va xato bosgan bo'lsa bilishi kerak.
    """
    where, params = _date_filter(detected_date)
    async with db().execute(
        f"""
        SELECT * FROM candidate
        WHERE sku = ? {where}
        -- Javob berilmaganlar OLDINDA: karta sahifalanganda menejer kerakli
        -- bandlarni birinchi sahifada ko'radi, hal qilinganlari esa oxirida
        ORDER BY (status <> '{STATUS_PENDING}' OR superseded_at IS NOT NULL),
                 shop_name, color
        """,
        (sku, *params),
    ) as cursor:
        return list(await cursor.fetchall())


async def get_candidate(candidate_id: int) -> aiosqlite.Row | None:
    async with db().execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)) as cursor:
        return await cursor.fetchone()


async def answer_candidate(
    candidate_id: int, *, status: str, user_id: int, transfer_hint: str = ""
) -> bool:
    """Bandga javob yozadi. Allaqachon javob berilgan bo'lsa False qaytaradi.

    `AND status = 'pending'` sharti bitta UPDATE ichida — ikki menejer bir vaqtda
    bossa faqat birinchisi yozadi, ikkinchisiga "allaqachon javob berilgan" chiqadi.
    """
    if status not in (STATUS_TAKEN, STATUS_NOT_FOUND):
        raise ValueError(f"noma'lum status: {status}")
    async with write_lock():
        cursor = await db().execute(
            f"""
            UPDATE candidate
               SET status = ?, transfer_hint = ?, answered_by = ?,
                   answered_at = datetime('now')
             WHERE id = ? AND status = '{STATUS_PENDING}'
            """,
            (status, transfer_hint, user_id, candidate_id),
        )
        changed = cursor.rowcount == 1
        if changed:
            await db().execute(
                "INSERT INTO item_event (candidate_id, user_id, action, payload) "
                "VALUES (?, ?, ?, ?)",
                (candidate_id, user_id, status,
                 json.dumps({"transfer_hint": transfer_hint}, ensure_ascii=False)),
            )
        await db().commit()
    return changed


async def reset_candidate(candidate_id: int, user_id: int) -> bool:
    """Javobni bekor qiladi — menejer xato tugma bosgan holat uchun."""
    async with write_lock():
        cursor = await db().execute(
            f"""
            UPDATE candidate
               SET status = '{STATUS_PENDING}', transfer_hint = '',
                   answered_by = NULL, answered_at = NULL
             WHERE id = ? AND status <> '{STATUS_PENDING}'
            """,
            (candidate_id,),
        )
        changed = cursor.rowcount == 1
        if changed:
            await db().execute(
                "INSERT INTO item_event (candidate_id, user_id, action, payload) "
                "VALUES (?, ?, 'reset', '{}')",
                (candidate_id, user_id),
            )
        await db().commit()
    return changed


async def answered_for_export(report_date: date) -> list[aiosqlite.Row]:
    """Export uchun: SHU KUNI javob berilgan bandlar.

    Nega answered_at bo'yicha, detected_date emas: band bir necha kun oldin
    aniqlanib, bugun hal qilingan bo'lishi mumkin. "Kunning hisoboti" — bugun
    qanday QAROR qabul qilinganini ko'rsatishi kerak.
    """
    async with db().execute(
        f"""
        SELECT * FROM candidate
        WHERE date(answered_at) = ?
          AND status IN ('{STATUS_TAKEN}', '{STATUS_NOT_FOUND}')
        ORDER BY shop_name, percent DESC, sku, color
        """,
        (report_date.isoformat(),),
    ) as cursor:
        return list(await cursor.fetchall())


async def open_count(detected_date: date | None = None) -> int:
    where, params = _date_filter(detected_date)
    async with db().execute(
        f"SELECT COUNT(*) AS n FROM candidate WHERE {_OPEN} {where}",
        params,
    ) as cursor:
        return (await cursor.fetchone())["n"]


async def latest_detected_date() -> date | None:
    async with db().execute("SELECT MAX(detected_date) AS d FROM candidate") as cursor:
        row = await cursor.fetchone()
    return date.fromisoformat(row["d"]) if row and row["d"] else None


def _date_filter(detected_date: date | None) -> tuple[str, tuple[Any, ...]]:
    if detected_date is None:
        return "", ()
    return "AND detected_date = ?", (detected_date.isoformat(),)


# Menyu faqat oxirgi tekshiruvda topilgan bandlarni ko'rsatadi. Menejer
# qoidani (oyna, chegara) o'zi tanlagan ekan, ro'yxat aynan shu qoidaga
# javob berishi kerak — aks holda eski tekshiruvlar natijasi to'planib,
# bugungi ish ular orasida ko'rinmay qoladi.
_OPEN = (
    f"status = '{STATUS_PENDING}' AND superseded_at IS NULL "
    "AND last_run = (SELECT CAST(value AS INTEGER) FROM kv WHERE key = 'check_run_seq')"
)


# ───────────────────────────── card_msg ─────────────────────────────


async def remember_card(chat_id: int, message_id: int, sku: str, has_photo: bool) -> None:
    async with write_lock():
        await db().execute(
            """
            INSERT INTO card_msg (chat_id, message_id, sku, has_photo, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT (chat_id, message_id) DO UPDATE SET
                sku = excluded.sku, has_photo = excluded.has_photo,
                updated_at = excluded.updated_at
            """,
            (chat_id, message_id, sku, int(has_photo)),
        )
        await db().commit()


async def get_card(chat_id: int, message_id: int) -> aiosqlite.Row | None:
    async with db().execute(
        "SELECT * FROM card_msg WHERE chat_id = ? AND message_id = ?", (chat_id, message_id)
    ) as cursor:
        return await cursor.fetchone()


async def forget_card(chat_id: int, message_id: int) -> None:
    async with write_lock():
        await db().execute(
            "DELETE FROM card_msg WHERE chat_id = ? AND message_id = ?", (chat_id, message_id)
        )
        await db().commit()


# ───────────────────────────── announced_arrival ─────────────────────────────


async def filter_unannounced(
    rows: Sequence[tuple[str, str, str, str]]
) -> list[tuple[str, str, str, str]]:
    """(arrived_date, shop_id, sku, color) dan hali e'lon qilinmaganlarini qaytaradi."""
    if not rows:
        return []
    fresh: list[tuple[str, str, str, str]] = []
    for row in rows:
        async with db().execute(
            "SELECT 1 FROM announced_arrival "
            "WHERE arrived_date = ? AND shop_id = ? AND sku = ? AND color = ?",
            row,
        ) as cursor:
            if await cursor.fetchone() is None:
                fresh.append(row)
    return fresh


async def mark_announced(rows: Sequence[tuple[str, str, str, str]]) -> None:
    if not rows:
        return
    async with write_lock():
        await db().executemany(
            "INSERT OR IGNORE INTO announced_arrival "
            "(arrived_date, shop_id, sku, color) VALUES (?, ?, ?, ?)",
            rows,
        )
        await db().commit()


# ───────────────────────────── ref (callback_data uchun) ─────────────────────────────


async def ref_id(kind: str, value: str) -> int:
    """Uzun nomga qisqa int ID beradi (callback_data 64 bayt chegarasi uchun)."""
    async with write_lock():
        await db().execute(
            "INSERT OR IGNORE INTO ref (kind, value) VALUES (?, ?)", (kind, value)
        )
        await db().commit()
    async with db().execute(
        "SELECT id FROM ref WHERE kind = ? AND value = ?", (kind, value)
    ) as cursor:
        row = await cursor.fetchone()
    return int(row["id"])


async def ref_value(kind: str, ref: int) -> str | None:
    async with db().execute(
        "SELECT value FROM ref WHERE id = ? AND kind = ?", (ref, kind)
    ) as cursor:
        row = await cursor.fetchone()
    return row["value"] if row else None


# ───────────────────────────── stock_snapshot ─────────────────────────────


async def replace_stock_snapshot(
    rows: Sequence[tuple[str, str, str, str, int]], snapshot_date: date
) -> int:
    """Qoldiq snapshotini to'liq almashtiradi (shop_id, shop_name, sku, color, qty).

    Nega DELETE + INSERT: qoldiq nolga tushgan tovar Billz javobida umuman
    kelmasligi mumkin, UPSERT esa eski qatorni qoldirib "hali bor" deb
    yolg'on ma'lumot beradi.
    """
    async with write_lock():
        await db().execute("DELETE FROM stock_snapshot")
        if rows:
            await db().executemany(
                """
                INSERT OR REPLACE INTO stock_snapshot
                    (shop_id, shop_name, sku, color, quantity, snapshot_date)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(*row, snapshot_date.isoformat()) for row in rows],
            )
        # Yozilgan vaqtni SQLite'ning o'z soati bilan belgilaymiz — yoshi ham
        # shu soatga nisbatan hisoblanadi, ya'ni vaqt mintaqasi chalkashmaydi
        await db().execute(
            """
            INSERT INTO kv (key, value, updated_at)
            VALUES ('stock_synced_at', datetime('now'), datetime('now'))
            ON CONFLICT (key) DO UPDATE SET value = excluded.value,
                                            updated_at = excluded.updated_at
            """
        )
        await db().commit()
    return len(rows)


async def stock_snapshot_rows() -> int:
    async with db().execute("SELECT COUNT(*) AS n FROM stock_snapshot") as cursor:
        return (await cursor.fetchone())["n"]


async def stock_snapshot_age_hours() -> float | None:
    """Qoldiq snapshoti necha soat oldin yozilgan. Bo'sh bo'lsa None."""
    raw = await kv_get("stock_synced_at")
    if not raw:
        return None
    async with db().execute(
        "SELECT (julianday('now') - julianday(?)) * 24 AS h", (raw,)
    ) as cursor:
        row = await cursor.fetchone()
    return float(row["h"]) if row and row["h"] is not None else None


async def other_shops_with_stock(
    sku: str, color: str, exclude_shop_id: str, limit: int = 3
) -> list[aiosqlite.Row]:
    """Shu tovar boshqa qaysi filialda bor — "BOZORDA YO'Q" javobidan keyin.

    Eng ko'p qoldiqli filial birinchi: transfer so'rashda mantiqan shundan
    so'rash qulayroq.
    """
    async with db().execute(
        """
        SELECT shop_id, shop_name, quantity
        FROM stock_snapshot
        WHERE sku = ? AND color = ? AND shop_id <> ? AND quantity > 0
        ORDER BY quantity DESC, shop_name
        LIMIT ?
        """,
        (sku, color, exclude_shop_id, limit),
    ) as cursor:
        return list(await cursor.fetchall())
