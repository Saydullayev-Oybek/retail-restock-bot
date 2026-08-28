"""Inline klaviaturalar — kaskadli menyu va karta tugmalari."""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..core.models import STATUS_PENDING
from .callbacks import AnswerCB, CategoryCB, NavCB, NoopCB, SkuCB, SupplierCB


def categories_kb(rows: list[tuple[int, str, int]]) -> InlineKeyboardMarkup:
    """1-daraja. rows: (ref_id, kategoriya nomi, hal qilinmagan soni)."""
    builder = InlineKeyboardBuilder()
    for ref, name, count in rows:
        builder.button(
            text=f"{name or 'Kategoriyasiz'} · {count}",
            callback_data=CategoryCB(ref=ref),
        )
    builder.adjust(1)
    return builder.as_markup()


def suppliers_kb(cat_ref: int, rows: list[tuple[int, str, int]]) -> InlineKeyboardMarkup:
    """2-daraja. rows: (ref_id, ta'minotchi nomi, soni)."""
    builder = InlineKeyboardBuilder()
    for ref, name, count in rows:
        builder.button(
            text=f"{name or 'Nomsiz'} · {count}",
            callback_data=SupplierCB(cat=cat_ref, ref=ref),
        )
    builder.adjust(1)
    builder.row(InlineKeyboardButton(
        text="⬅️ Kategoriyalar", callback_data=NavCB(to="cat").pack()
    ))
    return builder.as_markup()


def skus_kb(cat_ref: int, sup_ref: int, rows: list[tuple[int, str, str, int, int]]) -> InlineKeyboardMarkup:
    """3-daraja. rows: (ref_id, artikul, tovar nomi, bandlar soni, umumiy dona)."""
    builder = InlineKeyboardBuilder()
    for ref, sku, name, count, total_qty in rows:
        label = f"{sku} · {total_qty} dona"
        if count > 1:
            label += f" ({count} band)"
        builder.button(
            text=label, callback_data=SkuCB(cat=cat_ref, sup=sup_ref, ref=ref)
        )
    builder.adjust(1)
    builder.row(InlineKeyboardButton(
        text="⬅️ Ta'minotchilar", callback_data=NavCB(to="sup", cat=cat_ref).pack()
    ))
    return builder.as_markup()


# Bir sahifada nechta band.
#
# Ikki xil chegara bor:
#   * QATTIQ  — 100 tugma va 4096 belgi. Oshsa Telegram xabarni RAD ETADI.
#   * YUMSHOQ — 1024 belgi caption. Oshsa rasm ko'rsatilmaydi, matnga o'tadi.
#
# 5 ta band = 16 tugma va ~950 belgi eng yomon holatda (uzun tovar nomi,
# uzun ta'minotchi nomi, eskirgan belgisi bilan) — ya'ni karta RASM bilan
# chiqishda davom etadi. 6 ta bandda caption 1110 belgi bo'lib chegaradan
# oshadi va rasm yo'qoladi.
CARD_PAGE_SIZE = 5


def page_slice(rows: list[Any], page: int) -> tuple[list[Any], int, int]:
    """Sahifadagi bandlar, joriy sahifa raqami va jami sahifalar soni."""
    total = max(1, -(-len(rows) // CARD_PAGE_SIZE))
    page = max(0, min(page, total - 1))
    start = page * CARD_PAGE_SIZE
    return rows[start : start + CARD_PAGE_SIZE], page, total


def card_kb(
    rows: list[Any], cat_ref: int, sup_ref: int, sku_ref: int = 0, page: int = 0
) -> InlineKeyboardMarkup:
    """4-daraja: har bir (filial + rang) uchun alohida tugma juftligi.

    Nega har band uchun alohida: tugma bosilganda FAQAT o'sha aniq band holati
    o'zgarishi kerak — boshqa filiallar/ranglar tegilmaydi.

    Bandlar ko'p bo'lsa sahifalanadi: 35 ta band Telegram'ning 100 tugma
    chegarasidan oshib ketadi va xabar butunlay rad etiladi.
    """
    visible, page, total = page_slice(rows, page)
    builder = InlineKeyboardBuilder()
    for row in visible:
        color = f" {row['color']}" if row["color"] else ""
        title = f"{row['shop_name']}{color}"
        if row["status"] == STATUS_PENDING:
            builder.row(
                InlineKeyboardButton(text=title, callback_data=NoopCB(tag="h").pack())
            )
            builder.row(
                InlineKeyboardButton(
                    text="✅ OLINDI",
                    callback_data=AnswerCB(
                        id=row["id"], act="t", cat=cat_ref, sup=sup_ref, page=page
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ BOZORDA YO'Q",
                    callback_data=AnswerCB(
                        id=row["id"], act="n", cat=cat_ref, sup=sup_ref, page=page
                    ).pack(),
                ),
            )
        else:
            # Javob berilgan band — faqat bekor qilish tugmasi qoladi
            mark = "✅" if row["status"] == "taken" else "❌"
            builder.row(
                InlineKeyboardButton(
                    text=f"{mark} {title} — bekor qilish",
                    callback_data=AnswerCB(
                        id=row["id"], act="r", cat=cat_ref, sup=sup_ref, page=page
                    ).pack(),
                )
            )
    if total > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="◀️", callback_data=SkuCB(
                    cat=cat_ref, sup=sup_ref, ref=sku_ref, page=page - 1).pack()))
        nav.append(InlineKeyboardButton(
            text=f"{page + 1}/{total}", callback_data=NoopCB(tag="p").pack()))
        if page < total - 1:
            nav.append(InlineKeyboardButton(
                text="▶️", callback_data=SkuCB(
                    cat=cat_ref, sup=sup_ref, ref=sku_ref, page=page + 1).pack()))
        builder.row(*nav)

    builder.row(InlineKeyboardButton(
        text="⬅️ Artikullar",
        callback_data=NavCB(to="art", cat=cat_ref, sup=sup_ref).pack(),
    ))
    return builder.as_markup()
