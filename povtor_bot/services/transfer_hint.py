""""BOZORDA YO'Q" javobidan keyingi transfer taklifi.

Manba — /tekshir da olingan qoldiq snapshot. Nega jonli API emas: Billz
hisobotlarni 30 daqiqada 1 marta chaqirishni tavsiya qiladi, tugma esa
istalgan paytda bosiladi.
"""

from __future__ import annotations

from ..bot.texts import transfer_hint_text
from ..db import repo


async def build_hint(sku: str, color: str, exclude_shop_id: str, limit: int = 3) -> str:
    """Boshqa filiallardagi qoldiqdan matn yasaydi. Hech kimda bo'lmasa — bo'sh satr."""
    rows = await repo.other_shops_with_stock(sku, color, exclude_shop_id, limit=limit)
    return transfer_hint_text(rows)
