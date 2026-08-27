"""CallbackData factory'lari.

Nega qo'lda f"ans:{id}:{action}" yig'ish emas: callback_data 64 baytdan oshsa
Telegram xabarni rad etadi, va qo'lda split(":") tip tekshiruvisiz ishlaydi —
prefikslar to'qnashganda xato jim ketadi. aiogram factory'si ikkalasini ham
kompilyatsiya vaqtida hal qiladi.

Uzun matnlar (kategoriya/postavshik nomlari) callback ichiga solinmaydi —
ref jadvalidagi qisqa int ID uzatiladi.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class CategoryCB(CallbackData, prefix="cat"):
    """1-daraja: kategoriya tanlandi."""

    ref: int


class SupplierCB(CallbackData, prefix="sup"):
    """2-daraja: ta'minotchi tanlandi."""

    cat: int
    ref: int


class SkuCB(CallbackData, prefix="art"):
    """3-daraja: artikul tanlandi -> karta ochiladi.

    `page` — karta sahifasi. Bir artikulda 35+ band bo'lsa (7 filial x 5 rang)
    Telegram xabarni butunlay rad etadi (tugma limiti 100), shuning uchun
    karta sahifalarga bo'linadi.
    """

    cat: int
    sup: int
    ref: int
    page: int = 0


class AnswerCB(CallbackData, prefix="ans"):
    """Kartadagi OLINDI / BOZORDA YO'Q tugmasi.

    `act`: 't' = taken, 'n' = not_found, 'r' = reset (bekor qilish).
    Kontekst (cat/sup) saqlanadi — javobdan keyin kartani qayta chizish uchun.
    """

    id: int
    act: str
    cat: int
    sup: int
    page: int = 0


class NavCB(CallbackData, prefix="nav"):
    """Orqaga qaytish. `to`: 'cat' | 'sup' | 'art'."""

    to: str
    cat: int = 0
    sup: int = 0


class NoopCB(CallbackData, prefix="noop"):
    """Bosilmaydigan sarlavha tugmalari uchun."""

    tag: str = ""
