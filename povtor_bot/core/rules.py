"""Nomzod aniqlash mantiqi — SOF funksiyalar, I/O yo'q.

Nega alohida modul va I/O'siz: bu botning yuragi, va uni real Billz'siz,
real Telegram'siz test qilish kerak. tests/test_rules.py shu yerdagi
funksiyalarni haqiqiy POVTOR faylidan olingan 128 qatorli dataset bilan
tekshiradi.

Qoida (POVTOR_2026-08-20.xlsx dagi 128 qatorning hammasiga mos keladi):

    arrived_date — tovar skladdan filialga OXIRGI marta kelgan sana
    base_qty     — o'sha transferda kelgan miqdor ("Asos")
    sold_qty     — arrived_date dan bugungacha sotilgani
    percent      — sold_qty / base_qty * 100
    days_to_50   — kunlik cumsum base ning 50% iga yetgan birinchi kun (0-based)

    Nomzod  <=>  base_qty >= MIN_BASE_QTY
             VA  o'tgan kun <= WINDOW_DAYS
             VA  percent >= PERCENT_THRESHOLD
    ishonchli <=>  sold_qty >= CONFIDENT_MIN_SOLD
               VA  (percent >= HIGH_PERCENT  YOKI  days_to_50 <= CONFIDENT_MAX_DAYS)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .models import (
    GRADE_CONFIDENT,
    GRADE_NORMAL,
    Candidate,
    ProductInfo,
    SalesRow,
    TransferRow,
)


@dataclass(frozen=True, slots=True)
class RuleConfig:
    """Qoidaning barcha raqamlari — .env dan keladi, kodda qattiq yozilmagan."""

    window_days: int = 5
    percent_threshold: float = 50.0
    # Kichik "to'ldirish" transferlarini chetlab o'tish uchun eng kam partiya.
    # Namuna POVTOR faylida Asos hech qachon 5 dan kichik emas (74/128 aynan 5):
    # 1-2 dona kelgan tovar "tez sotilyapti" degan xulosaga asos bo'lmaydi.
    min_base_qty: int = 5
    confident_max_days: int = 3
    confident_min_sold: int = 4
    qty_confident: int = 10
    qty_normal: int = 5
    high_percent: float = 80.0
    allowed_category_groups: tuple[str, ...] = ()
    # True bo'lsa: yuqori foiz sold_qty shartini chetlab o'tadi (A varianti).
    # Default False (B varianti) — kichik partiyada 100% sotilishi ishonch bermaydi.
    high_percent_overrides_min_sold: bool = False


def round_percent(sold: int, base: int) -> float:
    """Foizni namuna fayldagidek bir kasr xonagacha yaxlitlaydi, 100% bilan cheklab.

    Nega cheklov: filialda oldindan qoldiq bo'lsa, sotuv oxirgi partiyadan
    ko'p bo'lib chiqadi (5 keldi, 6 sotildi = 120%). Bunday holatda partiya
    to'g'ri maxraj emas. Namuna faylda foiz hech qachon 100 dan oshmagan.
    """
    if base <= 0:
        return 0.0
    return round(min(sold, base) / base * 100, 1)


def days_to_reach(
    daily: Sequence[int], base: int, threshold_ratio: float = 0.5
) -> int | None:
    """Kunlik sotuvlar bo'yicha `threshold_ratio` ga yetgan kun indeksi (0-based).

    daily[0] — kelgan kuni. Yetmagan bo'lsa None.
    """
    if base <= 0:
        return None
    need = base * threshold_ratio
    running = 0
    for index, qty in enumerate(daily):
        running += qty
        if running >= need:
            return index
    return None


def grade_of(days_to_50: int, sold_qty: int, percent: float, cfg: RuleConfig) -> str:
    """Daraja: yetarli hajmda sotilgan VA (tez yoki ko'p) bo'lsa 'ishonchli'.

    Ikki signal ishonch beradi:
      * tezlik  — 50% ga confident_max_days ichida yetgan;
      * hajm    — umumiy foiz high_percent dan oshgan.
    Ikkalasi ham confident_min_sold donalik minimal ostonadan o'tishi shart:
    5 dan 3 tasi 3 kunda sotilishi 11 dan 7 tasi 3 kunda sotilishi bilan bir xil
    ishonch bermaydi — kichik partiyada tasodif ulushi katta.

    Bu qoida namuna POVTOR faylidagi 128 qatorning hammasiga mos keladi
    (tests/fixtures/golden_rules.json).
    """
    fast_or_big = percent >= cfg.high_percent or days_to_50 <= cfg.confident_max_days
    if not fast_or_big:
        return GRADE_NORMAL
    if sold_qty >= cfg.confident_min_sold:
        return GRADE_CONFIDENT
    # A varianti: yuqori foiz minimal ostonani bekor qiladi
    if cfg.high_percent_overrides_min_sold and percent >= cfg.high_percent:
        return GRADE_CONFIDENT
    return GRADE_NORMAL


def recommended_qty(grade: str, cfg: RuleConfig) -> int:
    return cfg.qty_confident if grade == GRADE_CONFIDENT else cfg.qty_normal


def build_note(percent: float, grade: str, days_to_50: int, sold_qty: int,
               cfg: RuleConfig) -> str:
    """Izoh matni — namuna Excel'dagi uch shakl bilan bir xil."""
    percent_text = f"{percent:g}"
    if percent >= cfg.high_percent:
        return f"{percent_text}% sotildi"
    if grade == GRADE_CONFIDENT:
        return f"{days_to_50}-kunda 50%, {sold_qty} dona"
    return f"{percent_text}% sotildi — 50% oshdi"


def evaluate(
    *,
    base_qty: int,
    daily_sales: Sequence[int],
    days_elapsed: int,
    cfg: RuleConfig,
) -> dict[str, object] | None:
    """Bitta (filial, artikul, rang) uchun qaror.

    Nomzod bo'lmasa None qaytaradi.
    """
    if base_qty < max(1, cfg.min_base_qty):
        return None
    if days_elapsed > cfg.window_days:
        return None

    # Oynadan tashqaridagi kunlarni hisobga olmaymiz
    window = list(daily_sales[: cfg.window_days + 1])
    sold_qty = sum(window)
    if sold_qty <= 0:
        return None

    percent = round_percent(sold_qty, base_qty)
    if percent < cfg.percent_threshold:
        return None

    reached = days_to_reach(window, base_qty)
    # percent >= 50 ekan, 50% ga albatta yetgan; None bo'lishi mantiqan mumkin emas,
    # lekin yaxlitlash chegarasida (masalan 49.95 -> 50.0) himoya sifatida qoldiramiz
    days_50 = reached if reached is not None else len(window) - 1

    grade = grade_of(days_50, sold_qty, percent, cfg)
    return {
        "sold_qty": sold_qty,
        "percent": percent,
        "days_to_50": days_50,
        "grade": grade,
        "recommended_qty": recommended_qty(grade, cfg),
        "note": build_note(percent, grade, days_50, sold_qty, cfg),
    }


def category_allowed(category_group: str, cfg: RuleConfig) -> bool:
    """Ro'yxat bo'sh bo'lsa — hamma kategoriya, aks holda faqat ko'rsatilganlar.

    Solishtirish registrga bog'liq emas: Billz'da "Obuv" ham, "OBUV" ham uchraydi.
    """
    if not cfg.allowed_category_groups:
        return True
    allowed = {g.strip().casefold() for g in cfg.allowed_category_groups}
    return category_group.strip().casefold() in allowed


def _key(shop_id: str, sku: str, color: str) -> tuple[str, str, str]:
    return (shop_id, sku, color)


@dataclass(slots=True)
class _Arrival:
    """Bir (filial, artikul, rang) ning oxirgi kelishi va o'sha kundagi qatorlar."""

    date: date
    rows: list[TransferRow]


def detect_candidates(
    *,
    today: date,
    transfers: Iterable[TransferRow],
    sales: Iterable[SalesRow],
    products: Mapping[tuple[str, str], ProductInfo],
    shop_names: Mapping[str, str],
    cfg: RuleConfig,
    usd_rate: float = 0.0,
) -> list[Candidate]:
    """Transfer + sotuv tarixidan nomzodlar ro'yxatini yasaydi.

    products — (sku, color) -> ProductInfo (rasm, tannarx, kategoriya).
    usd_rate — USD->UZS kursi; tannarx boshqa valyutada bo'lsa shu bilan o'giriladi.
    """
    # 1. Har bir (filial, artikul, rang) uchun OXIRGI KELGAN SANA va o'sha
    #    sanada kelgan UMUMIY miqdor.
    #
    #    Nega yig'indi, bitta qator emas: Billz'da bir (artikul + rang) bir
    #    nechta product_id ga bo'linadi (o'lcham setkasi, sezon bo'yicha), va
    #    ular alohida transfer qatorlari bo'lib keladi. Menejer uchun esa bu
    #    bitta qaror — "shu ko'ylakni oq rangda olamizmi".
    arrivals: dict[tuple[str, str, str], _Arrival] = {}
    for row in transfers:
        if row.quantity <= 0:
            continue
        key = _key(row.to_shop_id, row.sku, row.color)
        current = arrivals.get(key)
        if current is None or row.arrived_date > current.date:
            arrivals[key] = _Arrival(date=row.arrived_date, rows=[row])
        elif row.arrived_date == current.date:
            current.rows.append(row)

    # 2. Sotuvlarni kun bo'yicha guruhlaymiz.
    sales_by_key: dict[tuple[str, str, str], dict[date, int]] = {}
    for sale in sales:
        if sale.quantity <= 0:
            continue
        key = _key(sale.shop_id, sale.sku, sale.color)
        per_day = sales_by_key.setdefault(key, {})
        per_day[sale.day] = per_day.get(sale.day, 0) + sale.quantity

    candidates: list[Candidate] = []
    for key, arrival in arrivals.items():
        shop_id, sku, color = key
        days_elapsed = (today - arrival.date).days
        if days_elapsed < 0 or days_elapsed > cfg.window_days:
            continue

        info = products.get((sku, color))
        head = arrival.rows[0]
        # Kategoriya hisobotdan ham keladi — katalogdan oldin shu ishlatiladi
        category_group = head.category_group or (info.category_group if info else "")
        if not category_allowed(category_group, cfg):
            continue

        base_qty = sum(row.quantity for row in arrival.rows)

        # Kelgan kunidan bugungacha kunlik sotuv qatori (bo'sh kunlar = 0)
        day_map = sales_by_key.get(key, {})
        daily = [
            day_map.get(arrival.date + timedelta(days=offset), 0)
            for offset in range(days_elapsed + 1)
        ]

        verdict = evaluate(
            base_qty=base_qty,
            daily_sales=daily,
            days_elapsed=days_elapsed,
            cfg=cfg,
        )
        if verdict is None:
            continue

        # Tannarx: transfer hisoboti UZS'da so'ralgani uchun dona narxi
        # allaqachon so'mda. Katalogdagi supply_price zaxira sifatida qoladi.
        unit_price = max((row.unit_supply_price for row in arrival.rows), default=0.0)
        if unit_price > 0:
            supply_price, supply_currency = unit_price, "UZS"
        else:
            supply_price = info.supply_price if info else 0.0
            supply_currency = (info.supply_currency if info else "UZS") or "UZS"

        candidates.append(
            Candidate(
                detected_date=today,
                shop_id=shop_id,
                shop_name=shop_names.get(shop_id, shop_id),
                sku=sku,
                color=color,
                arrived_date=arrival.date,
                base_qty=base_qty,
                sold_qty=int(verdict["sold_qty"]),          # type: ignore[arg-type]
                percent=float(verdict["percent"]),          # type: ignore[arg-type]
                days_to_50=int(verdict["days_to_50"]),      # type: ignore[arg-type]
                grade=str(verdict["grade"]),
                recommended_qty=int(verdict["recommended_qty"]),  # type: ignore[arg-type]
                note=str(verdict["note"]),
                category_group=category_group,
                subcategory=info.subcategory if info else "",
                kind=info.kind if info else "",
                product_name=(info.name if info else "") or head.product_name,
                supplier=(info.supplier if info else "") or head.supplier,
                product_id=(info.product_id if info else "") or head.product_id,
                image_url=info.image_file if info else "",
                supply_price=supply_price,
                supply_currency=supply_currency,
                price_uzs=to_uzs(supply_price, supply_currency, usd_rate),
            )
        )

    # Menejer eng "issiq" pozitsiyani birinchi ko'rsin
    candidates.sort(key=lambda c: (c.shop_name, -c.percent, c.sku, c.color))
    return candidates


def to_uzs(amount: float, currency: str, usd_rate: float) -> int:
    """Tannarxni HAR DOIM so'mga o'giradi.

    Nega: kartada bir tovar dollarda, boshqasi so'mda chiqsa menejer chalkashadi.
    Kurs noma'lum bo'lsa 0 qaytariladi — noto'g'ri raqam ko'rsatgandan ko'ra
    umuman ko'rsatmagan afzal.
    """
    code = (currency or "UZS").strip().upper()
    if amount <= 0:
        return 0
    if code == "UZS":
        return int(round(amount))
    if code == "USD" and usd_rate > 0:
        return int(round(amount * usd_rate))
    return 0
