"""Kartani yuborish va yangilash.

Telegram'ning ikkita cheklovi butun bu modulning sababi:

1. Matnli xabarni rasmli xabarga (va aksincha) TAHRIRLAB bo'lmaydi. Shu sababli
   oxirgi yuborilgan karta turi (card_msg.has_photo) eslab qolinadi, va tur
   o'zgarganda eski xabar o'chirilib yangisi yuboriladi.
2. Mazmun o'zgarmasa `edit_*` "message is not modified" xatosini beradi. Bu
   xato emas — foydalanuvchi bir tugmani ikki marta bosgan holat. Alohida
   ushlanib e'tiborsiz qoldiriladi.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

from ..bot.keyboards import page_slice
from ..bot.texts import card_caption
from ..db import repo
from . import media

log = logging.getLogger(__name__)

# Telegram caption chegarasi 1024 belgi; matnli xabarda 4096
_CAPTION_LIMIT = 1024


def _caption(rows: list[Any], stale_after_days: int, page: int) -> str:
    visible, page, pages = page_slice(rows, page)
    return card_caption(
        rows, stale_after_days=stale_after_days,
        visible=visible, page=page, pages=pages,
    )


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    return "message is not modified" in str(exc).lower()


async def send_card(
    bot: Bot, chat_id: int, rows: list[Any], markup: InlineKeyboardMarkup,
    *, stale_after_days: int = 0, page: int = 0,
) -> Message | None:
    """Yangi karta yuboradi (rasm bilan yoki matn sifatida).

    `stale_after_days` — bandning yoshi shu kundan oshsa kartada ogohlantirish
    belgisi qo'yiladi. Odatda oynaning kengligi (WINDOW_DAYS) beriladi: undan
    oshgan band /tekshir da endi qayta aniqlanmaydi.
    """
    if not rows:
        return None
    sku, color = rows[0]["sku"], rows[0]["color"]
    caption = _caption(rows, stale_after_days, page)
    photo = await media.resolve_photo(sku, color)

    if photo is not None and len(caption) <= _CAPTION_LIMIT:
        message = await _send_photo(bot, chat_id, photo, caption, markup, sku, color)
        if message is not None:
            await repo.remember_card(chat_id, message.message_id, sku, has_photo=True)
            return message
        # Rasm yuborilmadi — matnga tushamiz

    try:
        message = await bot.send_message(
            chat_id, caption, reply_markup=markup, disable_web_page_preview=True
        )
    except TelegramBadRequest as exc:
        # Jim yiqilish eng yomoni: menejer tugma bosdi, hech nima bo'lmadi va
        # u botni "buzilgan" deb hisoblaydi. Sababni ko'rsatib qo'yamiz.
        log.error("Karta yuborilmadi (%s): %s", sku, exc)
        await _report_failure(bot, chat_id, sku, exc)
        return None
    await repo.remember_card(chat_id, message.message_id, sku, has_photo=False)
    return message


async def _report_failure(
    bot: Bot, chat_id: int, sku: str, exc: TelegramBadRequest
) -> None:
    """Karta ochilmaganini menejerga tushunarli qilib aytadi."""
    try:
        await bot.send_message(
            chat_id,
            f"⚠️ <b>{sku}</b> kartasi ochilmadi.\n"
            f"<code>{str(exc)[:200]}</code>\n\n"
            "<i>Administratorga ayting.</i>",
        )
    except TelegramBadRequest:
        log.error("Xato xabari ham yuborilmadi: chat=%s", chat_id)


async def update_card(
    bot: Bot, chat_id: int, message_id: int, rows: list[Any],
    markup: InlineKeyboardMarkup, *, stale_after_days: int = 0, page: int = 0,
) -> None:
    """Mavjud kartani yangilaydi; tur mos kelmasa qayta yuboradi."""
    if not rows:
        return
    sku, color = rows[0]["sku"], rows[0]["color"]
    caption = _caption(rows, stale_after_days, page)

    known = await repo.get_card(chat_id, message_id)
    had_photo = bool(known["has_photo"]) if known else False
    # Rasmni ATAYLAB yuklab olmaymiz: tur o'zgarmagan bo'lsa u umuman kerak emas
    wants_photo = (
        len(caption) <= _CAPTION_LIMIT and await media.has_photo(sku, color)
    )

    if had_photo != wants_photo:
        # Tur o'zgardi — tahrirlab bo'lmaydi, eskisini o'chirib yangisini yuboramiz
        await delete_quietly(bot, chat_id, message_id)
        await send_card(bot, chat_id, rows, markup,
                        stale_after_days=stale_after_days, page=page)
        return

    try:
        if had_photo:
            await bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id,
                caption=caption, reply_markup=markup,
            )
        else:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=caption,
                reply_markup=markup, disable_web_page_preview=True,
            )
    except TelegramBadRequest as exc:
        if _is_not_modified(exc):
            return   # bir xil mazmun — hech narsa qilinmaydi
        log.warning("Karta tahrirlanmadi, qayta yuboriladi: %s", exc)
        await delete_quietly(bot, chat_id, message_id)
        await send_card(bot, chat_id, rows, markup,
                        stale_after_days=stale_after_days, page=page)


async def _send_photo(
    bot: Bot, chat_id: int, photo: str | bytes, caption: str,
    markup: InlineKeyboardMarkup, sku: str, color: str,
) -> Message | None:
    """file_id yoki baytlar bilan rasm yuboradi; yangi file_id keshlanadi."""
    payload: Any
    if isinstance(photo, bytes):
        payload = BufferedInputFile(photo, filename=f"{sku or 'tovar'}.jpg")
    else:
        payload = photo
    try:
        message = await bot.send_photo(
            chat_id, payload, caption=caption, reply_markup=markup
        )
    except TelegramBadRequest as exc:
        log.warning("Rasm yuborilmadi (%s): %s", sku, exc)
        if isinstance(photo, str):
            # Eskirgan file_id: rasm yo'q degani emas — keyingi safar URL'dan
            # qayta yuklab ko'rish uchun faqat file_id tozalanadi
            await repo.clear_file_id(sku, color)
        return None
    if message.photo:
        await media.remember_file_id(sku, color, message.photo[-1].file_id)
    return message


async def delete_quietly(bot: Bot, chat_id: int, message_id: int) -> None:
    """Xabarni o'chiradi. Allaqachon o'chirilgan bo'lsa — muammo emas."""
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest as exc:
        log.debug("Xabar o'chirilmadi (%s): %s", message_id, exc)
    await repo.forget_card(chat_id, message_id)
