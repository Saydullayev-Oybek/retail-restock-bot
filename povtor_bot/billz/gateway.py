"""Billz JSON -> domen modellari.

Bu qatlamning butun maqsadi — manba tizimining o'ziga xosligini shu yerda ushlab
qolish. core/rules.py Billz haqida hech narsa bilmaydi; ertaga boshqa savdo
tizimi kelsa faqat shu fayl qayta yoziladi.

Billz javob maydonlari hujjatdan farq qilishi mumkin, shuning uchun hamma joyda
"bir nechta nomni sinab ko'rish" (_pick) yondashuvi ishlatiladi va yetishmagan
maydon istisnо emas, bo'sh qiymat beradi — bitta g'alati qator butun /tekshir
ni yiqitmasligi kerak.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from ..core.models import ProductInfo, SalesRow, Shop, StockRow, TransferRow
from .client import BillzClient

log = logging.getLogger(__name__)

# Bu Billz akkauntida rang `product_attributes` da EMAS, `custom_fields` ichida
# "Цвет" nomi bilan saqlanadi (probe bilan tasdiqlangan: product_attributes hamma
# joyda bo'sh keladi). Nomi til sozlamasiga qarab farq qilishi mumkin.
COLOR_FIELD_NAMES = ("цвет", "color", "rang", "tus")
SUBCATEGORY_FIELD_NAMES = ("подкатегория", "subcategory", "podkategoriya")
KIND_FIELD_NAMES = ("вид", "kind", "tur")

# Billz hisobot dvigateli vaqtni QATOR soniga emas, HAR SO'ROVGA sarflaydi:
# o'lchovda 500 qator 4.2s, 2000 qator 3.3s keldi. Shuning uchun sahifa
# kattaroq bo'lgani yaxshi — so'rovlar soni kamayadi, vaqt esa o'zgarmaydi.
# 1000 — hujjatda ko'rsatilgan maksimum (product-general-table uchun).
_PAGE_LIMIT = 1000
_MAX_PAGES = 200   # cheksiz sikldan himoya


def _pick(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    """Birinchi mavjud va bo'sh bo'lmagan maydonni qaytaradi."""
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return default


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_date(value: Any) -> date | None:
    """Billz sanalari: '2025-11-30 17:40:15', '2024-10-09T14:29:01+05:00', '2025-11-30'."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    # Vaqt va zona qismi bizga kerak emas — sanagacha kesamiz.
    # ISO shakllarining hammasida birinchi 10 belgi aynan YYYY-MM-DD.
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        log.debug("Sana o'qilmadi: %r", value)
        return None


def custom_field(row: dict[str, Any], names: tuple[str, ...]) -> str:
    """`custom_fields` ro'yxatidan nomi mos keladigan qiymatni oladi."""
    fields = row.get("custom_fields")
    if not isinstance(fields, list):
        return ""
    for field_ in fields:
        if not isinstance(field_, dict):
            continue
        name = str(
            field_.get("custom_field_name")
            or field_.get("custom_field_system_name")
            or field_.get("name")
            or ""
        ).strip().casefold()
        if name in names:
            return str(field_.get("custom_field_value") or field_.get("value") or "").strip()
    return ""


def extract_color(row: dict[str, Any]) -> str:
    """Rangni topadi.

    Asosiy manba — `custom_fields["Цвет"]`. Qolgan ikkita yo'l zaxira:
    boshqa Billz akkauntida rang atribut sifatida sozlangan bo'lishi mumkin.
    """
    value = custom_field(row, COLOR_FIELD_NAMES)
    if value:
        return value

    attributes = row.get("product_attributes") or row.get("attributes")
    if isinstance(attributes, list):
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            name = str(attribute.get("attribute_name") or attribute.get("name") or "")
            if name.strip().casefold() in COLOR_FIELD_NAMES:
                return str(
                    attribute.get("attribute_value") or attribute.get("value") or ""
                ).strip()

    flat = row.get("product_attribute")
    if isinstance(flat, str) and flat.strip():
        _, _, tail = flat.partition(":")
        return (tail or flat).strip()
    return ""


def category_levels(row: dict[str, Any]) -> tuple[str, str]:
    """(kategoriya guruhi, podkategoriya) — level_1 / level_2 yoki categories_path."""
    def first(value: Any) -> str:
        if isinstance(value, list) and value:
            return str(value[0]).strip()
        if isinstance(value, str):
            return value.strip()
        return ""

    group = first(row.get("level_1"))
    sub = first(row.get("level_2"))
    if group or sub:
        return group, sub

    path = row.get("categories_path")
    if isinstance(path, list) and path:
        return str(path[0]).strip(), (str(path[1]).strip() if len(path) > 1 else "")

    categories = row.get("categories") or row.get("product_categories")
    if isinstance(categories, list) and categories:
        names = [
            str(c.get("name", "")).strip()
            for c in categories
            if isinstance(c, dict)
        ]
        names = [n for n in names if n]
        if names:
            return names[0], (names[1] if len(names) > 1 else "")
    if isinstance(categories, str):
        return categories.strip(), ""
    return "", ""


def _rows_of(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """Javobdagi qatorlar ro'yxatini topadi (kalit nomi endpoint'ga qarab farq qiladi)."""
    if isinstance(payload.get("data"), dict):
        payload = payload["data"]
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    # Ba'zi javoblarda yagona ro'yxat kaliti bo'ladi — birinchisini olamiz
    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return []


def _count_of(payload: dict[str, Any]) -> int:
    if isinstance(payload.get("data"), dict):
        payload = payload["data"]
    return _as_int(payload.get("count"))


class BillzGateway:
    """Yuqori darajali chaqiruvlar — hammasi domen modellarini qaytaradi."""

    def __init__(self, client: BillzClient, page_limit: int = _PAGE_LIMIT) -> None:
        self._client = client
        self._page_limit = max(1, page_limit)

    # ───────────────────────── sahifalash ─────────────────────────

    async def _paginate(
        self, path: str, params: dict[str, Any], *keys: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Barcha sahifalarni yig'adi. To'xtash sharti — BO'SH sahifa.

        Nega "qisqa sahifa = oxirgi sahifa" emas: Billz o'rtadagi sahifada
        so'ralganidan kam qator qaytarishi mumkin (yuk, throttling, ichki
        filtrlash). Qisqa sahifada to'xtasak, qolgan sahifalar JIM o'qilmay
        qoladi va natija noto'g'ri chiqadi — xato ham berilmaydi.

        Bu real ishga tushirishda kuzatildi: bir tekshiruv 91 nomzod topdi,
        30 daqiqadan keyingisi xuddi shu ma'lumotda 7 ta. Sabab — sotuv
        hisobotining 25 sahifasidan bir nechtasi o'qilmay qolgani.

        Narxi: har hisobot uchun bitta qo'shimcha (bo'sh) so'rov.
        """
        limit = limit or self._page_limit
        collected: list[dict[str, Any]] = []
        seen_pages = 0
        for page in range(1, _MAX_PAGES + 1):
            payload = await self._client.get(
                path, {**params, "page": page, "limit": limit}
            )
            rows = _rows_of(payload, *keys)
            if not rows:
                break
            collected.extend(rows)
            seen_pages = page
        else:
            log.warning(
                "%s: %d sahifadan oshdi, qolgani o'qilmadi (%d qator yig'ildi)",
                path, _MAX_PAGES, len(collected),
            )
        log.debug("%s: %d sahifa, %d qator", path, seen_pages, len(collected))
        return collected

    # ───────────────────────── metodlar ─────────────────────────

    async def shops(self) -> list[Shop]:
        payload = await self._client.get(
            "/v1/shop", {"limit": 100, "only_allowed": "true"}
        )
        return [
            Shop(id=str(_pick(row, "id", "shop_id")), name=str(_pick(row, "name", "shop_name")))
            for row in _rows_of(payload, "shops", "companies", "data")
            if _pick(row, "id", "shop_id")
        ]

    async def usd_rate(self) -> float:
        """Oxirgi amaldagi USD->UZS kursi. Topilmasa 0."""
        payload = await self._client.get(
            "/v2/company-currency-rates",
            {"currency": "USD", "limit": 20, "page": 1},
        )
        rates = _rows_of(payload, "rates")
        best: tuple[str, float] = ("", 0.0)
        for row in rates:
            if str(_pick(row, "source_currency")).upper() != "USD":
                continue
            target = str(_pick(row, "target_currency")).upper()
            # target bo'sh bo'lishi ham mumkin (hujjatdagi misolda shunday)
            if target not in ("UZS", ""):
                continue
            valid_from = str(_pick(row, "valid_from"))
            rate = _as_float(_pick(row, "rate", default=0))
            # valid_to bo'sh => hozir amalda
            if rate > 0 and valid_from >= best[0]:
                best = (valid_from, rate)
        return best[1]

    async def products_by_sku(self, sku: str) -> list[ProductInfo]:
        """GET /v2/products?search=<sku> — bitta artikulning barcha variatsiyalari.

        Nega butun katalog emas: bu akkauntda 76 000+ tovar bor va Billz
        /v2/products ni 5 daqiqada 1 marta chaqirishni tavsiya qiladi. Bir
        tekshiruvda esa atigi ~200 ta artikul kerak bo'ladi, va ular
        `product_variant` jadvalida keshlanadi.
        """
        payload = await self._client.get(
            "/v2/products", {"search": sku, "limit": 100, "page": 1}
        )
        return [
            info
            for info in (_product_info(row) for row in _rows_of(payload, "products"))
            # search noaniq mos kelishi mumkin — faqat aynan shu artikul
            if info is not None and info.sku == sku
        ]

    async def products(self, last_updated: str = "") -> list[ProductInfo]:
        """GET /v2/products — butun katalog (yoki last_updated dan keyingilari).

        DIQQAT: katalog juda katta bo'lishi mumkin. Kunlik tekshiruv buni
        ishlatmaydi — u `products_by_sku()` orqali faqat keraklisini oladi.
        """
        params: dict[str, Any] = {}
        if last_updated:
            params["last_updated_date"] = last_updated
        rows = await self._paginate("/v2/products", params, "products")
        return [info for info in (_product_info(row) for row in rows) if info is not None]

    async def transfers(
        self, *, start: date, end: date, shop_ids: list[str]
    ) -> list[TransferRow]:
        """GET /v1/transfer-report-table — SKLAD -> filial harakatlari."""
        params = {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "shop_ids": ",".join(shop_ids),
            "display_currency": "UZS",
        }
        rows = await self._paginate("/v1/transfer-report-table", params, "rows")
        result: list[TransferRow] = []
        for row in rows:
            arrived = parse_date(_pick(row, "accepted_at", "created_at"))
            sku = str(_pick(row, "product_sku", "sku")).strip()
            if arrived is None or not sku:
                continue
            quantity = _as_int(_pick(row, "arrived_quantity", "sent_quantity", default=0))
            group, _ = category_levels(row)
            # Tannarx: display_currency=UZS so'ralgani uchun javob allaqachon so'mda.
            # /v2/products dagi supply_price bu akkauntda 0 keladi, shuning uchun
            # dona narxi shu yerdan olinadi.
            sent = _as_int(_pick(row, "sent_quantity", default=0)) or quantity
            total_supply = _as_float(_pick(row, "sum_supply_price", default=0))
            unit_price = total_supply / sent if sent > 0 and total_supply > 0 else 0.0
            result.append(
                TransferRow(
                    to_shop_id=str(_pick(row, "to_shop_id")),
                    sku=sku,
                    color=extract_color(row),
                    arrived_date=arrived,
                    quantity=quantity,
                    from_shop_id=str(_pick(row, "from_shop_id")),
                    product_id=str(_pick(row, "product_id")),
                    product_name=str(_pick(row, "product_name")),
                    supplier=str(_pick(row, "supplier", "supplier_name")),
                    category_group=group,
                    unit_supply_price=unit_price,
                )
            )
        return result

    async def sales(
        self, *, start: date, end: date, shop_ids: list[str]
    ) -> list[SalesRow]:
        """GET /v1/product-general-table?detalization=day — kunlik sotuvlar.

        `detalization=day` shart: 50%ga necha kunda yetganini bilish uchun
        yakuniy summa emas, kunlar kesimi kerak.
        """
        params = {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "shop_ids": ",".join(shop_ids),
            "currency": "UZS",
            "detalization": "day",
        }
        # Javobdagi ro'yxat kaliti — `products_stats_by_date` (hujjatda ko'rsatilmagan)
        rows = await self._paginate(
            "/v1/product-general-table", params, "products_stats_by_date", "rows", "products"
        )
        result: list[SalesRow] = []
        for row in rows:
            day = parse_date(_pick(row, "date", "day", "created_at"))
            sku = str(_pick(row, "product_sku", "sku")).strip()
            if day is None or not sku:
                continue
            quantity = _as_int(
                _pick(row, "net_sold_measurement_value", "sold_measurement_value", default=0)
            )
            result.append(
                SalesRow(
                    shop_id=str(_pick(row, "shop_id")),
                    sku=sku,
                    # Sotuv hisobotida rang yo'q — u product_id orqali katalogdan
                    # bog'lanadi (services/check.py)
                    color=extract_color(row),
                    day=day,
                    quantity=quantity,
                    product_id=str(_pick(row, "product_id")),
                )
            )
        return result

    async def stock(self, *, report_date: date, shop_ids: list[str]) -> list[StockRow]:
        """GET /v1/stock-report-table — "BOZORDA YO'Q" da transfer taklifi uchun."""
        params = {
            "report_date": report_date.isoformat(),
            "shop_ids": ",".join(shop_ids),
            "currency": "UZS",
        }
        rows = await self._paginate("/v1/stock-report-table", params, "rows")
        result: list[StockRow] = []
        for row in rows:
            sku = str(_pick(row, "product_sku", "sku")).strip()
            if not sku:
                continue
            group, sub = category_levels(row)
            result.append(
                StockRow(
                    shop_id=str(_pick(row, "shop_id")),
                    sku=sku,
                    color=extract_color(row),
                    product_id=str(_pick(row, "product_id")),
                    quantity=_as_int(_pick(row, "measurement_value", default=0)),
                    supplier=str(_pick(row, "supplier_name", "supplier")),
                    category_group=group,
                    subcategory=sub,
                    supply_price=_as_float(_pick(row, "supply_price", default=0)),
                    supply_currency="UZS",
                )
            )
        return result


# ───────────────────────── /v2/products yordamchilari ─────────────────────────


def _product_info(row: dict[str, Any]) -> ProductInfo | None:
    """Bitta /v2/products qatorini ProductInfo ga o'giradi."""
    sku = str(_pick(row, "sku", "product_sku")).strip()
    if not sku:
        return None
    group, sub_from_levels = category_levels(row)
    supply_price, supply_currency = _supply_price(row)
    return ProductInfo(
        sku=sku,
        color=extract_color(row),
        product_id=str(_pick(row, "id", "product_id")),
        name=str(_pick(row, "name", "product_name")),
        category_group=group,
        # Podkategoriya custom_fields da; level_2 zaxira sifatida qoladi
        subcategory=custom_field(row, SUBCATEGORY_FIELD_NAMES) or sub_from_levels,
        kind=custom_field(row, KIND_FIELD_NAMES),
        supplier=_first_supplier(row),
        image_file=_main_image(row),
        supply_price=supply_price,
        supply_currency=supply_currency,
        stock_by_shop=_stock_by_shop(row),
    )


def _main_image(row: dict[str, Any]) -> str:
    url = str(_pick(row, "main_image_url")).strip()
    if url:
        return url
    photos = row.get("photos")
    if isinstance(photos, list):
        # is_main belgilangani ustunroq, bo'lmasa birinchisi
        for photo in photos:
            if isinstance(photo, dict) and photo.get("is_main"):
                return str(photo.get("photo_url", "")).strip()
        for photo in photos:
            if isinstance(photo, dict) and photo.get("photo_url"):
                return str(photo["photo_url"]).strip()
    return ""


def _first_supplier(row: dict[str, Any]) -> str:
    suppliers = row.get("suppliers") or row.get("product_suppliers")
    if isinstance(suppliers, list):
        for supplier in suppliers:
            if isinstance(supplier, dict) and supplier.get("name"):
                return str(supplier["name"]).strip()
            if isinstance(supplier, str) and supplier.strip():
                return supplier.strip()
    if isinstance(suppliers, str):
        return suppliers.strip()
    return str(_pick(row, "supplier_name", "supplier")).strip()


def _supply_price(row: dict[str, Any]) -> tuple[float, str]:
    """Tannarx: shop_prices ichidan noldan katta birinchi supply_price."""
    prices = row.get("shop_prices")
    if isinstance(prices, list):
        for price in prices:
            if not isinstance(price, dict):
                continue
            value = _as_float(price.get("supply_price"))
            if value > 0:
                return value, str(price.get("supply_currency") or "UZS").upper()
    stocks = row.get("product_supplier_stock")
    if isinstance(stocks, list):
        for stock in stocks:
            if not isinstance(stock, dict):
                continue
            value = _as_float(stock.get("min_supply_price"))
            if value > 0:
                return value, "UZS"
    return 0.0, "UZS"


def _stock_by_shop(row: dict[str, Any]) -> dict[str, int]:
    values = row.get("shop_measurement_values")
    if not isinstance(values, list):
        return {}
    result: dict[str, int] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        shop_id = str(item.get("shop_id") or "")
        if not shop_id:
            continue
        result[shop_id] = _as_int(item.get("active_measurement_value"))
    return result


def index_products(products: list[ProductInfo]) -> dict[tuple[str, str], ProductInfo]:
    """(sku, color) -> ProductInfo. Dublikatda rasmi/tannarxi to'liqrog'i qoladi."""
    index: dict[tuple[str, str], ProductInfo] = {}
    for product in products:
        key = (product.sku, product.color)
        current = index.get(key)
        if current is None or _completeness(product) > _completeness(current):
            index[key] = product
    return index


def _completeness(product: ProductInfo) -> int:
    return sum(
        (
            bool(product.image_file),
            product.supply_price > 0,
            bool(product.supplier),
            bool(product.category_group),
        )
    )
