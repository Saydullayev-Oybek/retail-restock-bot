"""Kaskadli menyu: kategoriya -> ta'minotchi -> artikul -> karta, va javob tugmalari."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from ...core.models import STATUS_NOT_FOUND, STATUS_TAKEN
from ...db import repo
from ...services import cards, transfer_hint
from .. import keyboards
from ..callbacks import AnswerCB, CategoryCB, NavCB, NoopCB, SkuCB, SupplierCB
from ..texts import esc

log = logging.getLogger(__name__)
router = Router(name="menu")

EMPTY_TEXT = (
    "Hal qilinmagan band qolmadi. 🎉\n\n"
    "/tekshir — Billz'dan yangi ma'lumot olish"
)


# ───────────────────────── 1-daraja: kategoriyalar ─────────────────────────


async def categories_rows() -> list[tuple[int, str, int]]:
    rows = await repo.categories_with_open_counts()
    return [
        (await repo.ref_id("cat", row["category_group"]),
         row["category_group"], row["open_count"])
        for row in rows
    ]


async def show_categories(callback: CallbackQuery) -> None:
    rows = await categories_rows()
    if not rows:
        await _replace(callback, EMPTY_TEXT, None)
        return
    await _replace(
        callback, "📂 <b>Kategoriya tanlang</b>", keyboards.categories_kb(rows)
    )


@router.callback_query(NavCB.filter(F.to == "cat"))
async def on_back_to_categories(callback: CallbackQuery) -> None:
    await show_categories(callback)
    await callback.answer()


# ───────────────────────── 2-daraja: ta'minotchilar ─────────────────────────


async def show_suppliers(callback: CallbackQuery, cat_ref: int) -> None:
    category = await repo.ref_value("cat", cat_ref)
    if category is None:
        await callback.answer("Kategoriya topilmadi, menyuni qayta oching", show_alert=True)
        return
    rows = await repo.suppliers_with_open_counts(category)
    if not rows:
        await show_categories(callback)
        return
    items = [
        (await repo.ref_id("sup", row["supplier"]), row["supplier"], row["open_count"])
        for row in rows
    ]
    await _replace(
        callback,
        f"🏭 <b>{esc(category)}</b>\nTa'minotchi tanlang",
        keyboards.suppliers_kb(cat_ref, items),
    )


@router.callback_query(CategoryCB.filter())
async def on_category(callback: CallbackQuery, callback_data: CategoryCB) -> None:
    await show_suppliers(callback, callback_data.ref)
    await callback.answer()


@router.callback_query(NavCB.filter(F.to == "sup"))
async def on_back_to_suppliers(callback: CallbackQuery, callback_data: NavCB) -> None:
    await show_suppliers(callback, callback_data.cat)
    await callback.answer()


# ───────────────────────── 3-daraja: artikullar ─────────────────────────


async def show_skus(callback: CallbackQuery, cat_ref: int, sup_ref: int) -> None:
    category = await repo.ref_value("cat", cat_ref)
    supplier = await repo.ref_value("sup", sup_ref)
    if category is None or supplier is None:
        await callback.answer("Menyu eskirgan, /buyurtma ni qayta yuboring", show_alert=True)
        return
    rows = await repo.skus_with_open_counts(category, supplier)
    if not rows:
        await show_suppliers(callback, cat_ref)
        return
    items = [
        (await repo.ref_id("sku", row["sku"]), row["sku"], row["product_name"] or "",
         row["open_count"], row["total_qty"] or 0)
        for row in rows
    ]
    await _replace(
        callback,
        f"🏷 <b>{esc(supplier)}</b>\nArtikul tanlang",
        keyboards.skus_kb(cat_ref, sup_ref, items),
    )


@router.callback_query(SupplierCB.filter())
async def on_supplier(callback: CallbackQuery, callback_data: SupplierCB) -> None:
    await show_skus(callback, callback_data.cat, callback_data.ref)
    await callback.answer()


@router.callback_query(NavCB.filter(F.to == "art"))
async def on_back_to_skus(callback: CallbackQuery, callback_data: NavCB) -> None:
    await show_skus(callback, callback_data.cat, callback_data.sup)
    await callback.answer()


# ───────────────────────── 4-daraja: karta ─────────────────────────


@router.callback_query(SkuCB.filter())
async def on_sku(callback: CallbackQuery, callback_data: SkuCB, bot: Bot) -> None:
    sku = await repo.ref_value("sku", callback_data.ref)
    if sku is None:
        await callback.answer("Artikul topilmadi", show_alert=True)
        return
    rows = await repo.card_items(sku)
    if not rows:
        await show_skus(callback, callback_data.cat, callback_data.sup)
        await callback.answer()
        return

    # Ro'yxat xabari matnli, karta esa rasmli bo'lishi mumkin — turi mos
    # kelmasligi ehtimoli yuqori, shuning uchun ro'yxatni o'chirib karta yuboramiz.
    markup = keyboards.card_kb(rows, callback_data.cat, callback_data.sup)
    message = callback.message
    if message is not None:
        await cards.delete_quietly(bot, message.chat.id, message.message_id)
        await cards.send_card(bot, message.chat.id, rows, markup)
    await callback.answer()


# ───────────────────────── javob tugmalari ─────────────────────────

_ACTIONS = {"t": STATUS_TAKEN, "n": STATUS_NOT_FOUND}


@router.callback_query(AnswerCB.filter())
async def on_answer(callback: CallbackQuery, callback_data: AnswerCB, bot: Bot) -> None:
    """OLINDI / BOZORDA YO'Q / bekor qilish.

    Faqat bosilgan bandning holati o'zgaradi — boshqa filiallar va ranglar
    tegilmaydi. Karta darhol qayta chiziladi.
    """
    candidate = await repo.get_candidate(callback_data.id)
    if candidate is None:
        await callback.answer("Band topilmadi", show_alert=True)
        return

    user_id = callback.from_user.id if callback.from_user else 0

    if callback_data.act == "r":
        changed = await repo.reset_candidate(callback_data.id, user_id)
        notice = "Bekor qilindi" if changed else "Bu band allaqachon kutilmoqda"
    else:
        status = _ACTIONS.get(callback_data.act)
        if status is None:
            await callback.answer("Noma'lum amal", show_alert=True)
            return
        hint = ""
        if status == STATUS_NOT_FOUND:
            # Bozorda yo'q ekan — boshqa filialdan transfer qilish mumkinmi?
            hint = await transfer_hint.build_hint(
                candidate["sku"], candidate["color"], candidate["shop_id"]
            )
        changed = await repo.answer_candidate(
            callback_data.id, status=status, user_id=user_id, transfer_hint=hint
        )
        if not changed:
            notice = "Bu bandga allaqachon javob berilgan"
        elif status == STATUS_NOT_FOUND:
            notice = hint or "Boshqa filialda ham yo'q"
        else:
            notice = "OLINDI ✅"

    rows = await repo.card_items(candidate["sku"])
    message = callback.message
    if message is not None and rows:
        markup = keyboards.card_kb(rows, callback_data.cat, callback_data.sup)
        await cards.update_card(bot, message.chat.id, message.message_id, rows, markup)
    await callback.answer(notice, show_alert=bool(callback_data.act == "n" and notice))


@router.callback_query(NoopCB.filter())
async def on_noop(callback: CallbackQuery) -> None:
    """Sarlavha tugmasi — bosilsa ham hech narsa qilmaydi.

    Baribir answer() beriladi: aks holda tugma foydalanuvchi telefonida
    30 sekund aylanib turadi.
    """
    await callback.answer()


# ───────────────────────── yordamchilar ─────────────────────────


async def _replace(
    callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None
) -> None:
    """Menyu xabarini yangilaydi; tahrirlab bo'lmasa yangisini yuboradi.

    Karta rasmli bo'lishi mumkin, menyu esa har doim matnli — orqaga qaytishda
    tur mos kelmaydi va Telegram tahrirlashni rad etadi. Shu holat uchun
    o'chirib-qayta-yuborish zaxira yo'li.
    """
    message = callback.message
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=markup)
        return
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        log.debug("Menyu tahrirlanmadi: %s", exc)
    if message.bot is not None:
        await cards.delete_quietly(message.bot, message.chat.id, message.message_id)
    await message.answer(text, reply_markup=markup)
