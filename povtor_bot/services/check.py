"""/tekshir — Billz'dan tortish, nomzodlarni hisoblash, bazaga yozish.

Bu servis boshqa hamma narsadan OLDIN kamida bir marta ishlashi kerak, aks
holda menyuda ko'rsatadigan hech narsa bo'lmaydi.

Oqim real Billz ma'lumotiga qarab qurilgan (scripts/billz_probe.py bilan
tekshirilgan):

  1. transfer hisoboti  -> SKLAD'dan filialga kelganlar (from_shop_id bo'yicha)
  2. kelgan artikullar  -> katalogdan RANG olinadi (custom_fields["Цвет"])
  3. sotuv hisoboti     -> kunlik sotuvlar, product_id orqali rangga bog'lanadi
  4. qoldiq hisoboti    -> "BOZORDA YO'Q" dagi transfer taklifi uchun snapshot

Nega katalog to'liq tortilmaydi: bu akkauntda 76 000+ tovar bor va Billz
/v2/products ni 5 daqiqada 1 marta chaqirishni tavsiya qiladi. Bir tekshiruvda
esa atigi ~200 ta artikul kerak, va ular product_variant jadvalida keshlanadi.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, TypeVar

from ..billz.gateway import BillzGateway
from ..config import Settings
from ..core.models import Candidate, ProductInfo, SalesRow, TransferRow
from ..core.rules import RuleConfig, detect_candidates
from ..db import repo

log = logging.getLogger(__name__)

# Rang qo'shiladigan hisobot qatori — transfer yoki sotuv
_Row = TypeVar("_Row", TransferRow, SalesRow)


@dataclass(frozen=True, slots=True)
class CheckResult:
    """/tekshir natijasi — foydalanuvchiga ko'rsatiladigan qisqacha hisobot."""

    new_count: int
    total_found: int
    stock_rows: int
    usd_rate: float = 0.0
    transfer_rows: int = 0
    synced_skus: int = 0
    # Menyuda kutayotgan bandlar soni. "total_found" har tekshiruvda tebranadi
    # (yangi partiya kelishi, qaytarish, oynadan chiqish), menejer uchun esa
    # muhimi — qancha ish qolgani.
    open_count: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def rule_config(settings: Settings) -> RuleConfig:
    return RuleConfig(
        window_days=settings.window_days,
        percent_threshold=settings.percent_threshold,
        min_base_qty=settings.min_base_qty,
        confident_max_days=settings.confident_max_days,
        confident_min_sold=settings.confident_min_sold,
        qty_confident=settings.qty_confident,
        qty_normal=settings.qty_normal,
        high_percent=settings.high_percent,
        allowed_category_groups=tuple(settings.allowed_category_groups),
        high_percent_overrides_min_sold=settings.high_percent_overrides_min_sold,
    )


async def resolve_filial_ids(
    gateway: BillzGateway, settings: Settings
) -> tuple[list[str], dict[str, str]]:
    """Kuzatiladigan filiallar ro'yxati va id -> nom lug'ati.

    FILIAL_SHOP_IDS bo'sh bo'lsa — skladlardan boshqa hamma do'kon olinadi.
    """
    shops = await gateway.shops()
    names = {shop.id: shop.name for shop in shops}
    if settings.filial_shop_ids:
        ids = [sid for sid in settings.filial_shop_ids if sid]
    else:
        warehouses = set(settings.warehouse_shop_ids)
        ids = [shop.id for shop in shops if shop.id not in warehouses]
    return ids, names


async def sync_variants(
    gateway: BillzGateway, skus: list[str], settings: Settings
) -> int:
    """Kerakli artikullarni katalogdan o'qib product_variant ga yozadi.

    Faqat hali o'qilmagan (yoki eskirgan) artikullar so'raladi.
    """
    pending = await repo.stale_skus(skus, settings.sku_sync_days)
    if not pending:
        return 0
    log.info("Katalogdan %d ta yangi artikul o'qiladi", len(pending))
    synced = 0
    for sku in pending:
        try:
            variants = await gateway.products_by_sku(sku)
        except Exception:  # noqa: BLE001 — bitta artikul butun tekshiruvni to'xtatmasin
            log.warning("Artikul o'qilmadi: %s", sku, exc_info=True)
            continue
        await repo.save_variants(
            [
                {
                    "product_id": v.product_id, "sku": v.sku, "color": v.color,
                    "subcategory": v.subcategory, "kind": v.kind,
                    "supplier": v.supplier, "product_name": v.name,
                    "category_group": v.category_group, "image_file": v.image_file,
                }
                for v in variants
            ]
        )
        await repo.mark_sku_synced(sku, len(variants))
        synced += 1
    return synced


def _variant_color(variants: Mapping[str, Any], product_id: str) -> str:
    variant = variants.get(product_id)
    return variant["color"] if variant is not None else ""


def _apply_color(rows: Sequence[_Row], variants: Mapping[str, Any]) -> list[_Row]:
    """Hisobot qatorlariga product_id orqali rangni qo'shadi.

    Transfer va sotuv hisobotlarida rang YO'Q (Billz `product_attributes` ni
    bo'sh qaytaradi), faqat `product_id` bor. Rang esa katalogdagi
    `custom_fields["Цвет"]` da — u product_variant jadvalida saqlanadi.
    """
    result: list[_Row] = []
    for row in rows:
        if row.color:
            result.append(row)
            continue
        variant = variants.get(row.product_id)
        result.append(replace(row, color=_variant_color(variants, row.product_id))
                      if variant is not None else row)
    return result


def _product_index(variants: Mapping[str, Any]) -> dict[tuple[str, str], ProductInfo]:
    """(sku, color) -> ProductInfo. Bir kalitga bir nechta variatsiya tushadi."""
    index: dict[tuple[str, str], ProductInfo] = {}
    for row in variants.values():
        key = (row["sku"], row["color"])
        current = index.get(key)
        info = ProductInfo(
            sku=row["sku"], color=row["color"], product_id=row["product_id"],
            name=row["product_name"], category_group=row["category_group"],
            subcategory=row["subcategory"], kind=row["kind"],
            supplier=row["supplier"], image_file=row["image_file"],
        )
        # Rasmi/nomi to'liqrog'i qoladi
        if current is None or _completeness(info) > _completeness(current):
            index[key] = info
    return index


def _completeness(info: ProductInfo) -> int:
    return sum((bool(info.image_file), bool(info.supplier),
                bool(info.subcategory), bool(info.name)))


async def run_check(
    gateway: BillzGateway, settings: Settings, *, today: date | None = None
) -> CheckResult:
    """To'liq tekshiruv sikli."""
    today = today or date.today()
    cfg = rule_config(settings)

    warehouses = set(settings.warehouse_shop_ids)
    if not warehouses:
        return CheckResult(0, 0, 0, error="WAREHOUSE_SHOP_IDS sozlanmagan")

    try:
        filial_ids, shop_names = await resolve_filial_ids(gateway, settings)
    except Exception as exc:  # noqa: BLE001 — foydalanuvchiga sabab ko'rsatiladi
        log.exception("Filiallar ro'yxati olinmadi")
        return CheckResult(0, 0, 0, error=f"Filiallar olinmadi: {exc}")

    if not filial_ids:
        return CheckResult(0, 0, 0, error="Kuzatiladigan filial topilmadi")

    filials = set(filial_ids)
    start = today - timedelta(days=cfg.window_days)

    try:
        # shop_ids ga SKLAD ham qo'shiladi, aks holda jo'natuvchi tomoni
        # hisobotga tushmasligi mumkin
        transfers = await gateway.transfers(
            start=start, end=today, shop_ids=[*filial_ids, *warehouses],
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Transfer hisoboti olinmadi")
        return CheckResult(0, 0, 0, error=f"Billz xatosi: {exc}")

    # FAQAT sklad -> filial. Filiallar bir-biriga ham yuboradi (real ma'lumotda
    # ~30%), lekin bu "yangi partiya keldi" degani emas — sotilmay qolgan
    # tovarni qayta taqsimlash.
    #
    # Loyiha egasi tasdiqlagan (2026-08-27): "filialdan filialga transfer
    # qilingan tovar tez sotilsa ham bozordan olinmaydi — faqat skladdan
    # kelgani hisoblanadi".
    warehouse_transfers = [
        row for row in transfers
        if row.from_shop_id in warehouses and row.to_shop_id in filials
    ]
    log.info(
        "Transfer: jami %d, skladdan filialga %d",
        len(transfers), len(warehouse_transfers),
    )

    # Kelgan artikullarning rangini bilish uchun katalogdan o'qiymiz
    skus = [row.sku for row in warehouse_transfers]
    synced = await sync_variants(gateway, skus, settings)
    variants = await repo.variant_map()

    # Qoldiq — eng katta hisobot (sahifalarning ~57% i), lekin u faqat
    # "boshqa filialda bormi?" savoliga javob beradi. Bir necha soatlik
    # eskilik zarar qilmaydi, shuning uchun har tekshiruvda qayta o'qilmaydi.
    stock_age = await repo.stock_snapshot_age_hours()
    refresh_stock = stock_age is None or stock_age >= settings.stock_refresh_hours

    try:
        sales = await gateway.sales(start=start, end=today, shop_ids=filial_ids)
        stock = (
            await gateway.stock(report_date=today, shop_ids=filial_ids)
            if refresh_stock else []
        )
        usd_rate = await gateway.usd_rate()
    except Exception as exc:  # noqa: BLE001
        log.exception("Billz'dan ma'lumot olinmadi")
        return CheckResult(0, 0, 0, error=f"Billz xatosi: {exc}")

    if not refresh_stock:
        log.info("Qoldiq snapshoti yangi (%.1f soat) — qayta o'qilmadi", stock_age)

    colored_transfers = _apply_color(warehouse_transfers, variants)
    colored_sales = _apply_color(sales, variants)

    index = _product_index(variants)
    await repo.cache_products(
        {
            "sku": info.sku, "color": info.color, "product_id": info.product_id,
            "product_name": info.name, "category_group": info.category_group,
            "subcategory": info.subcategory, "kind": info.kind,
            "supplier": info.supplier, "image_url": info.image_file,
            "supply_price": info.supply_price, "supply_currency": info.supply_currency,
        }
        for info in index.values()
    )

    # Qoldiq snapshot — "BOZORDA YO'Q" da transfer taklifi shu yerdan chiqadi.
    # Yangilanmagan bo'lsa eskisi saqlanib qoladi (bo'sh ro'yxat bilan
    # almashtirib yuborish transfer taklifini o'chirib qo'yardi).
    if refresh_stock:
        await repo.replace_stock_snapshot(
            [
                (
                    row.shop_id, shop_names.get(row.shop_id, row.shop_id), row.sku,
                    row.color or _variant_color(variants, row.product_id), row.quantity,
                )
                for row in stock
                if row.quantity > 0
            ],
            today,
        )

    candidates: list[Candidate] = detect_candidates(
        today=today,
        transfers=colored_transfers,
        sales=colored_sales,
        products=index,
        shop_names=shop_names,
        cfg=cfg,
        usd_rate=usd_rate,
    )
    new_count = await repo.insert_candidates(candidates)

    if settings.raw_retention_days > 0:
        await repo.purge_raw(settings.raw_retention_days)

    log.info(
        "Tekshiruv: %d nomzod topildi, %d tasi yangi (sotuv=%d, qoldiq=%d)",
        len(candidates), new_count, len(sales), len(stock),
    )
    return CheckResult(
        new_count=new_count,
        total_found=len(candidates),
        stock_rows=len(stock) if refresh_stock else await repo.stock_snapshot_rows(),
        open_count=await repo.open_count(),
        usd_rate=usd_rate,
        transfer_rows=len(warehouse_transfers),
        synced_skus=synced,
    )


async def recent_arrivals(
    gateway: BillzGateway, settings: Settings, *, today: date | None = None
) -> tuple[list[dict[str, object]], str]:
    """/yangi uchun: so'nggi kunlarda skladdan filiallarga kelgan tovarlar.

    Qaytadi: (tovar bo'yicha guruhlangan ro'yxat, xato matni).
    """
    today = today or date.today()
    warehouses = set(settings.warehouse_shop_ids)
    if not warehouses:
        return [], "WAREHOUSE_SHOP_IDS sozlanmagan"

    try:
        filial_ids, shop_names = await resolve_filial_ids(gateway, settings)
        start = today - timedelta(days=max(0, settings.announce_lookback_days - 1))
        transfers = await gateway.transfers(
            start=start, end=today, shop_ids=[*filial_ids, *warehouses],
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Yangi kelgan tovarlar olinmadi")
        return [], f"Billz xatosi: {exc}"

    filials = set(filial_ids)
    fresh = [
        row for row in transfers
        if row.from_shop_id in warehouses
        and row.to_shop_id in filials
        and row.quantity > 0
    ]
    if not fresh:
        return [], ""

    await sync_variants(gateway, [row.sku for row in fresh], settings)
    variants = await repo.variant_map()
    fresh = _apply_color(fresh, variants)

    keys = [
        (row.arrived_date.isoformat(), row.to_shop_id, row.sku, row.color)
        for row in fresh
    ]
    unannounced = set(await repo.filter_unannounced(keys))

    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for row in fresh:
        key4 = (row.arrived_date.isoformat(), row.to_shop_id, row.sku, row.color)
        if key4 not in unannounced:
            continue
        key = (row.sku, row.color)
        entry = grouped.setdefault(key, {
            "sku": row.sku, "color": row.color, "name": row.product_name,
            "supplier": row.supplier, "price_uzs": int(round(row.unit_supply_price)),
            "shops": [], "keys": [],
        })
        entry["shops"].append(  # type: ignore[union-attr]
            (shop_names.get(row.to_shop_id, row.to_shop_id), row.quantity)
        )
        entry["keys"].append(key4)  # type: ignore[union-attr]

    return list(grouped.values()), ""
