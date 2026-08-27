"""/yangi — so'nggi kunlarda kelgan tovarlarni umumiy guruhga e'lon qilish.

Bu odamlar hozir qo'lda qilayotgan ishni almashtiradi: har bir tovar uchun
alohida xabar (rasm + nomi + tannarx + qaysi filialga nechtadan).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile

from ..bot.texts import arrival_message
from ..core.rules import to_uzs
from ..db import repo
from . import media

log = logging.getLogger(__name__)

# Telegram guruhga sekundiga ~20 xabar ruxsat beradi; 3 xabar/sek xavfsiz zaxira
_SEND_INTERVAL = 0.35
_MAX_RETRIES = 3
_CAPTION_LIMIT = 1024


async def announce(
    bot: Bot, chat_id: int, entries: list[dict[str, Any]], usd_rate: float = 0.0
) -> tuple[int, int]:
    """Har bir tovar uchun alohida xabar yuboradi.

    Qaytadi: (yuborilgan, xato). Muvaffaqiyatli yuborilgan tovar DARHOL
    "e'lon qilingan" deb belgilanadi — jarayon o'rtasida uzilsa dublikat ketmasin.
    """
    sent = failed = 0
    for entry in entries:
        try:
            await _send_one(bot, chat_id, entry, usd_rate)
        except Exception:  # noqa: BLE001 — bitta tovar butun e'lonni to'xtatmasin
            log.exception("Tovar e'lon qilinmadi: %s", entry.get("sku"))
            failed += 1
        else:
            await repo.mark_announced(entry.get("keys", []))
            sent += 1
        await asyncio.sleep(_SEND_INTERVAL)
    return sent, failed


async def _send_one(
    bot: Bot, chat_id: int, entry: dict[str, Any], usd_rate: float
) -> None:
    sku = str(entry["sku"])
    color = str(entry.get("color") or "")

    # Narx: transfer hisobotidagi dona narxi ustunroq — u display_currency=UZS
    # bilan so'ralgani uchun ishonchli. Katalogdagi supply_price ko'p hollarda 0.
    price_uzs = int(entry.get("price_uzs") or 0)

    # Transfer hisobotida nom/postavshik bo'sh bo'lishi mumkin — keshdan to'ldiramiz
    cached = await repo.get_cached_product(sku, color)
    if cached is not None:
        entry["name"] = entry.get("name") or cached["product_name"]
        entry["supplier"] = entry.get("supplier") or cached["supplier"]
        if price_uzs <= 0:
            price_uzs = to_uzs(
                cached["supply_price"], cached["supply_currency"], usd_rate
            )

    text = arrival_message(entry, price_uzs)
    photo = await media.resolve_photo(sku, color)

    if photo is not None and len(text) <= _CAPTION_LIMIT:
        payload = (
            BufferedInputFile(photo, filename=f"{sku}.jpg")
            if isinstance(photo, bytes) else photo
        )
        try:
            message = await _with_retry(
                bot.send_photo, chat_id, payload, caption=text
            )
        except TelegramBadRequest as exc:
            log.warning("Rasm bilan yuborilmadi (%s): %s", sku, exc)
        else:
            if message.photo:
                await media.remember_file_id(sku, color, message.photo[-1].file_id)
            return

    await _with_retry(bot.send_message, chat_id, text, disable_web_page_preview=True)


async def _with_retry(func, *args, **kwargs):
    """TelegramRetryAfter (429) da ko'rsatilgan vaqt kutiladi.

    Nega kerak: 100+ xabarni ketma-ket yuborganda Telegram flood limitga uradi
    va bot yarim yo'lda to'xtab qolishi mumkin.
    """
    for attempt in range(_MAX_RETRIES):
        try:
            return await func(*args, **kwargs)
        except TelegramRetryAfter as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            log.warning("Telegram 429: %s s kutiladi", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 1)
    raise RuntimeError("yuborilmadi")
