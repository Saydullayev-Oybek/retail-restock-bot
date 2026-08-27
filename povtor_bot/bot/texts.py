"""Xabar matnlari (HTML).

Nega HTML, Markdown emas: postavshik nomlarida `_`, `-`, `*` uchraydi
("ABUSAXIY 8-22 M64", "Dilshod Трико M424") va Markdown ularni formatlash
belgisi deb o'qib xato beradi. Barcha dinamik matn esc() dan o'tadi.
"""

from __future__ import annotations

from datetime import date
from html import escape
from typing import Any

from ..core.models import STATUS_NOT_FOUND, STATUS_PENDING, STATUS_TAKEN

STATUS_ICON = {
    STATUS_PENDING: "⚪️",
    STATUS_TAKEN: "✅",
    STATUS_NOT_FOUND: "❌",
}
STATUS_LABEL = {
    STATUS_PENDING: "kutilmoqda",
    STATUS_TAKEN: "OLINDI",
    STATUS_NOT_FOUND: "BOZORDA YO'Q",
}


def esc(value: Any) -> str:
    return escape(str(value or ""), quote=False)


def money(uzs: int) -> str:
    """Tannarx — har doim so'mda, mingliklar ajratilgan holda."""
    if not uzs:
        return "—"
    return f"{uzs:,}".replace(",", " ") + " so'm"


def age_label(detected: str, today: date, stale_after_days: int = 0) -> str:
    """Band necha kun oldin aniqlangani.

    Nega kerak: javob berilmagan band menyuda turaveradi — ertaga ham, bir
    hafta keyin ham. Menejer uning ESKILIGINI ko'rib turishi kerak, chunki
    eski band endi dolzarb bo'lmasligi mumkin (tovar boshqa yo'l bilan
    to'ldirilgan bo'lishi mumkin).

    Bugungi band uchun bo'sh satr — yangi bandga "bugun" deb yozish shovqin.
    """
    try:
        days = (today - date.fromisoformat(str(detected))).days
    except (TypeError, ValueError):
        return ""
    if days <= 0:
        return ""
    text = "kecha" if days == 1 else f"{days} kun oldin"
    # Oynadan chiqqan band endi qayta aniqlanmaydi — buni ajratib ko'rsatamiz
    if stale_after_days and days > stale_after_days:
        return f"⚠️ {text}"
    return text


def card_caption(
    rows: list[Any], *, today: date | None = None, stale_after_days: int = 0
) -> str:
    """Artikul kartasi: tovar sarlavhasi + har bir filial/rang qatori.

    rows — bitta artikulning barcha (filial, rang) bandlari.
    """
    today = today or date.today()
    if not rows:
        return "Band topilmadi."

    head = rows[0]
    lines = [
        f"<b>{esc(head['product_name'] or 'Nomsiz tovar')}</b>",
        f"Artikul: <code>{esc(head['sku'])}</code>",
    ]
    if head["subcategory"]:
        lines.append(f"Bo'lim: {esc(head['subcategory'])}")
    if head["supplier"]:
        lines.append(f"Ta'minotchi: {esc(head['supplier'])}")
    lines.append(f"Tannarx: <b>{esc(money(head['price_uzs']))}</b>")
    lines.append("")

    for row in rows:
        icon = STATUS_ICON.get(row["status"], "⚪️")
        shop = esc(row["shop_name"])
        color = f" · {esc(row['color'])}" if row["color"] else ""
        percent = f"{row['percent']:g}"
        lines.append(f"{icon} <b>{shop}</b>{color} — <b>{row['recommended_qty']} dona</b>")
        stats = (
            f"{row['base_qty']} kelgan, {row['sold_qty']} sotilgan "
            f"({percent}%) · {esc(row['grade'])}"
        )
        age = age_label(row["detected_date"], today, stale_after_days)
        if age:
            stats += f" · {age}"
        lines.append(f"    <i>{stats}</i>")
        if row["status"] != STATUS_PENDING:
            lines.append(f"    → <b>{esc(STATUS_LABEL[row['status']])}</b>")
        if row["transfer_hint"]:
            lines.append(f"    🔁 {esc(row['transfer_hint'])}")
    return "\n".join(lines)


def arrival_message(entry: dict[str, Any], price_uzs: int) -> str:
    """/yangi — guruhga yuboriladigan bitta tovar e'loni."""
    color = f" · {esc(entry['color'])}" if entry.get("color") else ""
    lines = [
        "🆕 <b>Yangi tovar keldi</b>",
        f"<b>{esc(entry.get('name') or 'Nomsiz tovar')}</b>{color}",
        f"Artikul: <code>{esc(entry['sku'])}</code>",
    ]
    if entry.get("supplier"):
        lines.append(f"Ta'minotchi: {esc(entry['supplier'])}")
    lines.append(f"Tannarx: <b>{esc(money(price_uzs))}</b>")
    lines.append("")
    for shop_name, qty in entry.get("shops", []):
        lines.append(f"• {esc(shop_name)} — <b>{qty} dona</b>")
    return "\n".join(lines)


def transfer_hint_text(rows: list[Any]) -> str:
    """"BOZORDA YO'Q" javobidan keyingi taklif."""
    if not rows:
        return ""
    parts = [f"{row['shop_name']}: {row['quantity']} dona" for row in rows]
    return "Transfer qilsa bo'ladi — " + ", ".join(parts)


def check_report(result: Any) -> str:
    if not result.ok:
        return f"⚠️ Tekshiruv bajarilmadi.\n<code>{esc(result.error)}</code>"
    lines = [
        "✅ <b>Tekshiruv tugadi</b>",
        f"Yangi qo'shildi: <b>{result.new_count}</b>",
        f"Hal qilinmagan: <b>{getattr(result, 'open_count', 0)}</b>",
        "",
        f"<i>tekshirildi: {result.total_found} nomzod · "
        f"qoldiq {result.stock_rows} qator</i>",
    ]
    if result.usd_rate:
        lines.append(f"<i>USD kursi: {result.usd_rate:g}</i>")
    if result.new_count == 0 and result.total_found:
        lines.append("\n<i>Yangi nomzod yo'q — hammasi allaqachon ro'yxatda.</i>")
    lines.append("\n/buyurtma — ro'yxatni ochish")
    return "\n".join(lines)
