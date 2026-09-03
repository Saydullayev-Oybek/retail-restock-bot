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

OYLAR = ("yan", "fev", "mar", "apr", "may", "iyn",
         "iyl", "avg", "sen", "okt", "noy", "dek")


def short_date(value: str) -> str:
    """'2026-08-22' -> '22-avg'. Karta tor, to'liq sana joy egallaydi."""
    try:
        d = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return ""
    return f"{d.day}-{OYLAR[d.month - 1]}"


def days_between(start: str, end: date) -> int | None:
    try:
        return (end - date.fromisoformat(str(start))).days
    except (TypeError, ValueError):
        return None


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


def age_label(
    detected: str, today: date, stale_after_days: int = 0, arrived: str = ""
) -> str:
    """Bandning yoshi va statistikasi eskirgani.

    Ikki xil "yosh" bor va ular BOSHQA narsani bildiradi:

    * `detected` — band qachon ro'yxatga tushgani. Menejerga "bu qachondan
      beri kutyapti" degan ma'noni beradi.
    * `arrived`  — partiya qachon kelgani. Agar u oynadan chiqqan bo'lsa,
      band /tekshir da endi QAYTA ANIQLANMAYDI va uning raqamlari
      (sotilgan, foiz) o'sha kunda MUZLAB qolgan.

    Ogohlantirish `arrived` bo'yicha qo'yiladi: aynan shunda raqamlar
    eskiradi. Band kecha aniqlangan bo'lishi, lekin partiyasi bir hafta
    oldin kelgan bo'lishi mumkin — bunday holatda "kecha" degan yozuv
    menejerni chalg'itadi.
    """
    try:
        days = (today - date.fromisoformat(str(detected))).days
    except (TypeError, ValueError):
        return ""

    eskirgan = False
    if stale_after_days and arrived:
        try:
            eskirgan = (today - date.fromisoformat(str(arrived))).days > stale_after_days
        except (TypeError, ValueError):
            eskirgan = False

    # Eskirgan bo'lsa kun sonini takrorlamaymiz: kelgan sana yuqoridagi
    # qatorda allaqachon turibdi ("18-avg keldi"), va "eskirgan" so'zining
    # o'zi yetarli signal. Har band uchun 13 belgi tejaydi — bu karta
    # rasm bilan chiqishida hal qiluvchi bo'lishi mumkin.
    if eskirgan:
        return "⚠️ eskirgan"
    if days <= 0:
        return ""
    return "kecha" if days == 1 else f"{days} kun oldin"


def _is_current(row: Any) -> bool:
    """Band oxirgi tekshiruvda topilganmi. Ustun bo'lmasa — topilgan deb olamiz."""
    try:
        value = row["is_current"]
    except (KeyError, IndexError, TypeError):
        return True
    return bool(value)


def _row_get(row: Any, key: str, fallback: str = "") -> str:
    """Ixtiyoriy ustun. Migratsiyagacha yozilgan qatorlarda u bo'lmasligi mumkin."""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return fallback
    return str(value).strip() if value else fallback


def _row_window(row: Any, fallback: int) -> int:
    """Bandni topgan oyna. Eski qatorlarda ustun bo'lmasligi mumkin."""
    try:
        value = row["window_days"]
    except (KeyError, IndexError, TypeError):
        return fallback
    return int(value) if value else fallback


def card_caption(
    rows: list[Any], *, today: date | None = None, stale_after_days: int = 0,
    visible: list[Any] | None = None, page: int = 0, pages: int = 1,
) -> str:
    """Artikul kartasi: tovar sarlavhasi + har bir filial/rang qatori.

    rows — bitta artikulning barcha (filial, rang) bandlari.
    """
    today = today or date.today()
    if not rows:
        return "Band topilmadi."

    head = rows[0]
    shown = rows if visible is None else visible

    # Sarlavhaga brend qo'shiladi. Sababi: Billz'da `product_name` model nomi
    # emas, tur nomi — "Кеды-Casual" ni 33 ta artikul baham ko'radi. Brendsiz
    # ikkita butunlay boshqa model kartada bir xil ko'rinadi.
    nomi = esc(head["product_name"] or "Nomsiz tovar")
    brend = esc(_row_get(head, "brand"))
    lines = [
        f"<b>{nomi}{f' · {brend}' if brend else ''}</b>",
        f"Artikul: <code>{esc(head['sku'])}</code>",
    ]
    # Podkategoriya ko'pincha nom bilan aynan bir xil ("Кеды-Casual") —
    # takrorlash foydasiz, faqat farq qilganda ko'rsatamiz
    if head["subcategory"] and head["subcategory"] != head["product_name"]:
        lines.append(f"Bo'lim: {esc(head['subcategory'])}")
    tafsilot = [esc(x) for x in (_row_get(head, "kind"), _row_get(head, "material")) if x]
    if tafsilot:
        lines.append(f"Tur: {' · '.join(tafsilot)}")
    if head["supplier"]:
        lines.append(f"Ta'minotchi: {esc(head['supplier'])}")
    lines.append(f"Tannarx: <b>{esc(money(head['price_uzs']))}</b>")
    if pages > 1:
        ochiq = sum(1 for r in rows if r["status"] == STATUS_PENDING)
        lines.append(
            f"<i>{len(rows)} band ({ochiq} ta hal qilinmagan) · "
            f"sahifa {page + 1}/{pages}</i>"
        )
    lines.append("")

    for row in shown:
        # Oxirgi tekshiruvda topilmagan band: raqamlari o'sha paytdagi holat.
        # Menyu uni sanamaydi, shuning uchun karta ham farqni ko'rsatishi
        # kerak — aks holda "2 band" deb yozib, 3 tasini ko'rsatadi.
        eski = row["status"] == STATUS_PENDING and not _is_current(row)
        icon = "⏸" if eski else STATUS_ICON.get(row["status"], "⚪️")
        shop = esc(row["shop_name"])
        color = f" · {esc(row['color'])}" if row["color"] else ""
        lines.append(f"{icon} <b>{shop}</b>{color} — <b>{row['recommended_qty']} dona</b>")

        # 1-qator: qachon va nechta kelgani, o'shandan beri qancha sotilgani.
        # Buyer bozorda turib qaror qabul qiladi — unga "qachon kelgan" va
        # "qanchasi ketgan" degan ikkita raqam kerak.
        kelgan = short_date(row["arrived_date"])
        otgan = days_between(row["arrived_date"], today)
        percent = f"{row['percent']:g}"
        kun = f"{otgan} kunda" if otgan and otgan > 0 else "shu kuni"
        lines.append(
            f"    <i>{kelgan} keldi: {row['base_qty']} dona · "
            f"{kun} {row['sold_qty']} sotildi ({percent}%)</i>"
        )

        # 2-qator: daraja, 50% ga yetish tezligi va eskirish belgisi
        detal = [esc(row["grade"]), f"50%ga {row['days_to_50']}-kunda"]
        # Har band O'Z oynasi bilan solishtiriladi: menejer 10 kunlik
        # tekshiruv qilgan bo'lsa, 7 kunlik band eskirgan emas
        oyna = _row_window(row, stale_after_days)
        age = age_label(row["detected_date"], today, oyna, row["arrived_date"])
        if age:
            detal.append(age)
        lines.append(f"    <i>{' · '.join(detal)}</i>")

        # Qayta ochilgan band eskisi bo'lib ko'rinadi — menejer uni nega
        # yana ko'rayotganini bilib tursin
        if row["status"] == STATUS_PENDING and _row_get(row, "reopened_at"):
            lines.append("    <i>🔁 avval bozorda yo'q edi</i>")

        if eski:
            lines.append("    → <i>eski tekshiruvdan</i>")
        elif row["status"] != STATUS_PENDING:
            lines.append(f"    → <b>{esc(STATUS_LABEL[row['status']])}</b>")
        elif row["superseded_at"]:
            lines.append(
                f"    → <b>yangi partiya keldi</b> "
                f"<i>({short_date(row['superseded_at'])})</i>"
            )
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


def check_report(result: Any, *, days: int = 0, percent: int = 0) -> str:
    if not result.ok:
        return f"⚠️ Tekshiruv bajarilmadi.\n<code>{esc(result.error)}</code>"
    lines = ["✅ <b>Tekshiruv tugadi</b>",
             f"Topilgan nomzodlar: <b>{result.total_found}</b>"]

    # Topilgan hammasi menyuga tushmaydi: bir qismiga yangi partiya kelgan,
    # bir qismiga menejer allaqachon javob bergan. Raqamlar o'zaro to'g'ri
    # kelmasa "121 topildi, menyuda 117" degan savol har safar takrorlanadi.
    taqsimot = [
        ("menyuda", getattr(result, "open_now", 0)),
        ("yangi partiya keldi", getattr(result, "superseded", 0)),
        ("javob bergansiz", getattr(result, "already_answered", 0)),
    ]
    taqsimot = [(nom, son) for nom, son in taqsimot if son]
    for index, (nom, son) in enumerate(taqsimot):
        belgi = "└" if index == len(taqsimot) - 1 else "├"
        lines.append(f"   {belgi} {nom}: <b>{son}</b>")

    lines.append(f"Yangi qo'shildi: <b>{result.new_count}</b>")

    dona = getattr(result, "stock_units", 0)
    if dona:
        yosh = getattr(result, "stock_age_hours", None)
        qachon = "yangilandi" if not yosh else f"{yosh:.0f} soat oldingi"
        lines.append(
            f"Filiallardagi qoldiq: <b>{dona:,}</b> dona · "
            f"{getattr(result, 'stock_skus', 0):,} artikul ({qachon})".replace(",", " ")
        )
    elif result.stock_rows:
        lines.append(f"Qoldiq qatorlari: {result.stock_rows}")
    if days and percent:
        # Menejer natijani QAYSI qoida bilan olganini eslay olishi kerak —
        # u har tekshiruvda boshqacha bo'lishi mumkin
        lines.append(f"<i>oyna {days} kun · chegara {percent}%</i>")
    if result.usd_rate:
        lines.append(f"USD kursi: {result.usd_rate:g}")
    ochildi = getattr(result, "reopened", 0)
    if ochildi:
        lines.append(
            f"Bozorda yo'q edi: <b>{ochildi}</b> band qayta so'raladi"
        )
    if result.new_count == 0 and result.total_found:
        lines.append("\n<i>Yangi nomzod yo'q — hammasi allaqachon bazada.</i>")
    lines.append("\n/buyurtma — ro'yxatni ochish")
    return "\n".join(lines)
