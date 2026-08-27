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


def card_kb(rows: list[Any], cat_ref: int, sup_ref: int) -> InlineKeyboardMarkup:
    """4-daraja: har bir (filial + rang) uchun alohida tugma juftligi.

    Nega har band uchun alohida: tugma bosilganda FAQAT o'sha aniq band holati
    o'zgarishi kerak — boshqa filiallar/ranglar tegilmaydi.
    """
    builder = InlineKeyboardBuilder()
    for row in rows:
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
                        id=row["id"], act="t", cat=cat_ref, sup=sup_ref
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ BOZORDA YO'Q",
                    callback_data=AnswerCB(
                        id=row["id"], act="n", cat=cat_ref, sup=sup_ref
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
                        id=row["id"], act="r", cat=cat_ref, sup=sup_ref
                    ).pack(),
                )
            )
    builder.row(InlineKeyboardButton(
        text="⬅️ Artikullar",
        callback_data=NavCB(to="art", cat=cat_ref, sup=sup_ref).pack(),
    ))
    return builder.as_markup()
