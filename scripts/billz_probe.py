#!/usr/bin/env python3
"""Billz endpoint'larining HAQIQIY javob shaklini aniqlash.

Nega kerak: hujjatdagi maydon nomlari amaldagi javob bilan farq qilishi mumkin.
Bu skript har bir endpoint'dan bitta kichik sahifa oladi va JSON qilib saqlaydi,
shundan keyin gateway.py dagi maydon nomlarini ishonch bilan qotirish mumkin.

    python scripts/billz_probe.py                 # hammasi
    python scripts/billz_probe.py --only shops    # faqat do'konlar (SKLAD ID'sini bilish uchun)
    python scripts/billz_probe.py --out var/probe

.env dagi BILLZ_SECRET_TOKEN talab qilinadi.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from povtor_bot.billz.client import BillzClient  # noqa: E402
from povtor_bot.config import get_settings  # noqa: E402

TODAY = date.today()
WEEK_AGO = TODAY - timedelta(days=7)


def probes(shop_ids: str, warehouse_ids: str) -> dict[str, tuple[str, dict]]:
    """nom -> (path, params). Hajm ataylab kichik: rate limit 2 rps."""
    return {
        "shops": ("/v1/shop", {"limit": 100, "only_allowed": "true"}),
        "currencies": ("/v2/company-currencies", {}),
        "currency_rates": ("/v2/company-currency-rates",
                           {"currency": "USD", "limit": 5, "page": 1}),
        "categories": ("/v2/category",
                       {"limit": 50, "page": 1, "is_deleted": "false"}),
        "suppliers": ("/v1/supplier", {"limit": 50}),
        "products": ("/v2/products", {"limit": 3, "page": 1}),
        "transfers": ("/v1/transfer-report-table", {
            "start_date": WEEK_AGO.isoformat(), "end_date": TODAY.isoformat(),
            "shop_ids": ",".join(filter(None, [shop_ids, warehouse_ids])),
            "display_currency": "UZS", "page": 1, "limit": 5,
        }),
        "sales_daily": ("/v1/product-general-table", {
            "start_date": WEEK_AGO.isoformat(), "end_date": TODAY.isoformat(),
            "shop_ids": shop_ids, "currency": "UZS", "detalization": "day",
            "page": 1, "limit": 5,
        }),
        "stock": ("/v1/stock-report-table", {
            "report_date": TODAY.isoformat(), "shop_ids": shop_ids,
            "currency": "UZS", "page": 1, "limit": 5,
        }),
    }


def summarise(payload: object, depth: int = 0) -> str:
    """Javob strukturasini qisqa ko'rsatadi — maydon nomlarini tez ko'rish uchun."""
    pad = "  " * depth
    if isinstance(payload, dict):
        lines = []
        for key, value in list(payload.items())[:40]:
            if isinstance(value, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(summarise(value, depth + 1))
            else:
                lines.append(f"{pad}{key}: {type(value).__name__} = {str(value)[:60]}")
        return "\n".join(lines)
    if isinstance(payload, list):
        if not payload:
            return f"{pad}[] (bo'sh)"
        return f"{pad}[{len(payload)} element], birinchisi:\n" + summarise(payload[0], depth + 1)
    return f"{pad}{type(payload).__name__} = {str(payload)[:60]}"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Billz endpoint shakllarini aniqlash")
    parser.add_argument("--out", default="var/probe", help="natija katalogi")
    parser.add_argument("--only", nargs="*", help="faqat shu probe'lar")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.billz_secret_token:
        print("XATO: .env dagi BILLZ_SECRET_TOKEN bo'sh", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = BillzClient(
        secret_token=settings.billz_secret_token,
        base_url=settings.billz_base_url,
        platform_id=settings.billz_platform_id,
        rate_limit_rps=settings.billz_rate_limit_rps,
    )

    try:
        # Avval do'konlarni olamiz — qolgan probe'larga shop_ids kerak
        shops_payload = await client.get("/v1/shop", {"limit": 100, "only_allowed": "true"})
        (out_dir / "shops.json").write_text(
            json.dumps(shops_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        shops = shops_payload.get("shops") or shops_payload.get("data") or []
        if isinstance(shops, dict):
            shops = shops.get("shops", [])
        print("\n=== DO'KONLAR (SKLAD ID'sini shu yerdan oling) ===")
        for shop in shops if isinstance(shops, list) else []:
            if isinstance(shop, dict):
                print(f"  {shop.get('id')}  {shop.get('name')}")

        ids = [str(s.get("id")) for s in shops if isinstance(s, dict) and s.get("id")]
        warehouses = set(settings.warehouse_shop_ids)
        filials = [i for i in ids if i not in warehouses]
        shop_ids = ",".join(filials[:10])

        selected = probes(shop_ids, ",".join(warehouses))
        if args.only:
            selected = {k: v for k, v in selected.items() if k in set(args.only)}

        for name, (path, params) in selected.items():
            if name == "shops":
                continue
            print(f"\n=== {name}  ({path}) ===")
            try:
                payload = await client.get(path, params)
            except Exception as exc:  # noqa: BLE001
                print(f"  XATO: {exc}")
                continue
            (out_dir / f"{name}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(summarise(payload))
    finally:
        await client.aclose()

    print(f"\nJSON fayllar: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
