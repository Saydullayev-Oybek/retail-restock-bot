"""Domen modellari.

Nega dataclass va Billz JSON'ining o'zi emas: Billz javob maydonlari hujjatdan
farq qilishi mumkin va nomlar rus/ingliz aralash. Normalizatsiya bitta joyda
(billz/gateway.py) bo'lsa, mantiq (core/rules.py) manba tizimidan mustaqil
qoladi — ertaga Billz o'rniga boshqa tizim kelsa faqat gateway o'zgaradi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class Shop:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ProductInfo:
    """Tovar variatsiyasi (bitta Billz product_id).

    Bir artikul (sku) ko'p variatsiyaga ega bo'ladi: rang, o'lcham setkasi,
    sezon bo'yicha. Rang va podkategoriya Billz'da `custom_fields` ichida
    ("Цвет", "Подкатегория") saqlanadi — `product_attributes` bo'sh keladi.
    """

    sku: str
    color: str
    product_id: str = ""
    name: str = ""
    category_group: str = ""   # Billz level_1 — menyuning 1-darajasi
    subcategory: str = ""      # custom_fields["Подкатегория"]
    kind: str = ""             # custom_fields["Вид"] — Excel'dagi "Tur" ustuni
    supplier: str = ""
    # Billz faqat fayl nomini qaytaradi ("<uuid>.jpg"), to'liq manzil emas
    image_file: str = ""
    supply_price: float = 0.0
    supply_currency: str = "UZS"
    # filial_id -> qoldiq. "BOZORDA YO'Q" da transfer taklifi shu yerdan chiqadi.
    stock_by_shop: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TransferRow:
    """Transferning bitta qatori.

    `from_shop_id` MAJBURIY: filiallar bir-biriga ham tovar yuboradi, va ular
    "skladdan kelgan yangi partiya" hisoblanmasligi kerak.
    """

    to_shop_id: str
    sku: str
    color: str
    arrived_date: date
    quantity: int
    from_shop_id: str = ""
    product_id: str = ""
    product_name: str = ""
    supplier: str = ""
    category_group: str = ""
    # Tannarx: Billz display_currency=UZS bilan so'raladi, ya'ni allaqachon so'mda
    unit_supply_price: float = 0.0


@dataclass(frozen=True, slots=True)
class SalesRow:
    """Bir kunlik sotuv (detalization=day)."""

    shop_id: str
    sku: str
    color: str
    day: date
    quantity: int
    product_id: str = ""


@dataclass(frozen=True, slots=True)
class StockRow:
    """Filialdagi joriy qoldiq."""

    shop_id: str
    sku: str
    color: str
    quantity: int
    product_id: str = ""
    supplier: str = ""
    category_group: str = ""
    subcategory: str = ""
    supply_price: float = 0.0
    supply_currency: str = "UZS"


@dataclass(frozen=True, slots=True)
class Candidate:
    """Hisoblangan qayta buyurtma nomzodi — bazaga shu ko'rinishda yoziladi."""

    detected_date: date
    shop_id: str
    shop_name: str
    sku: str
    color: str
    arrived_date: date
    base_qty: int
    sold_qty: int
    percent: float
    days_to_50: int
    grade: str            # 'ishonchli' | 'oddiy'
    recommended_qty: int
    note: str
    category_group: str = ""
    subcategory: str = ""
    kind: str = ""
    product_name: str = ""
    supplier: str = ""
    product_id: str = ""
    image_url: str = ""
    supply_price: float = 0.0
    supply_currency: str = "UZS"
    price_uzs: int = 0
    # Qaysi oyna bilan topilgani — kartadagi "eskirgan" belgisi shunga qaraydi
    window_days: int = 5


# Statuslar — bitta joyda, magic string tarqalmasin
STATUS_PENDING = "pending"
STATUS_TAKEN = "taken"
STATUS_NOT_FOUND = "not_found"

GRADE_CONFIDENT = "ishonchli"
GRADE_NORMAL = "oddiy"
