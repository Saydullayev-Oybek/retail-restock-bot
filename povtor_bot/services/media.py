"""Tovar rasmi: Billz CDN -> Telegram -> file_id kesh.

Billz hujjati rasmni CDN'dan to'g'ridan-to'g'ri uchinchi tomon resurslarida
ko'rsatishni TAQIQLAYDI. Shu sababli URL Telegram'ga uzatilmaydi: rasm bir
marta yuklab olinadi, Telegram'ga bayt sifatida yuboriladi, qaytgan file_id
saqlanadi va undan keyin faqat shu ishlatiladi.

Manzil `/v2/products` javobidagi `main_image_url_full` dan keladi — qo'shimcha
sozlama talab qilmaydi.

Yon foydasi — taskdagi "birinchi so'ralganda topilmasa keyingi safar qayta
so'ramaslik" talabi ham shu kesh bilan qoplanadi.
"""

from __future__ import annotations

import logging

import httpx

from ..config import get_settings
from ..db import repo

log = logging.getLogger(__name__)

# Telegram photo uchun amaldagi chegara 10 MB
_MAX_BYTES = 10 * 1024 * 1024
_TIMEOUT = 20.0


async def resolve_photo(sku: str, color: str) -> str | bytes | None:
    """Kartaga qo'yish uchun rasm.

    Qaytadi:
      * str   — tayyor Telegram file_id (eng arzon yo'l);
      * bytes — endigina yuklab olingan rasm, uni yuborgach remember_file_id() chaqiring;
      * None  — rasm yo'q, karta matn ko'rinishida chiqadi.
    """
    cached = await repo.get_cached_product(sku, color)
    if cached is None:
        return None
    if cached["tg_file_id"]:
        return cached["tg_file_id"]
    if cached["image_missing"]:
        return None

    url = full_image_url(cached["image_url"])
    if not url:
        # Keshda faqat fayl nomi turgan (migratsiyagacha yozilgan) qatorlar ham
        # shu yerga tushadi — ularni "rasmsiz" deb belgilamaymiz: artikul
        # katalogdan qayta o'qilgach to'liq manzil keladi
        if not cached["image_url"]:
            await repo.mark_image_missing(sku, color)
        return None

    data = await _download(url)
    if data is None:
        # Qayta urinmaymiz: har karta ochilganda CDN'ga borish rate limit'ni yeydi
        await repo.mark_image_missing(sku, color)
        return None
    return data


def full_image_url(image_ref: str) -> str:
    """Billz qaytargan qiymatdan to'liq HTTP manzil yasaydi.

    Odatda gateway `main_image_url_full` ni saqlaydi — u allaqachon to'liq
    manzil va shundayligicha qaytariladi. BILLZ_IMAGE_BASE_URL faqat zaxira:
    javobda to'liq manzil bo'lmagan (yoki keshda eski, fayl nomi turgan)
    holatlar uchun.
    """
    ref = (image_ref or "").strip()
    if not ref:
        return ""
    if ref.startswith(("http://", "https://")):
        return ref
    base = get_settings().billz_image_base_url.strip()
    if not base:
        return ""
    return f"{base.rstrip('/')}/{ref.lstrip('/')}"


async def has_photo(sku: str, color: str) -> bool:
    """Rasm chiqishi MUMKINmi — yuklab olmasdan, faqat keshga qarab.

    update_card kartani har yangilaganda rasmni qayta yuklab olmasligi uchun
    kerak: xabar turi (rasmli/matnli) o'zgarganini bilish uchun shu yetarli.
    """
    cached = await repo.get_cached_product(sku, color)
    if cached is None:
        return False
    if cached["tg_file_id"]:
        return True
    if cached["image_missing"]:
        return False
    return bool(full_image_url(cached["image_url"]))


async def _download(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code != 200:
                log.warning("Rasm yuklanmadi (%s): HTTP %s", url, response.status_code)
                return None
            data = response.content
    except httpx.HTTPError as exc:
        log.warning("Rasm yuklanmadi (%s): %s", url, exc)
        return None

    if not data or len(data) > _MAX_BYTES:
        log.warning("Rasm yaroqsiz (%s): %d bayt", url, len(data))
        return None
    return data


async def remember_file_id(sku: str, color: str, file_id: str) -> None:
    """Telegram qaytargan file_id ni saqlaydi — rasm boshqa yuklab olinmaydi."""
    if file_id:
        await repo.set_file_id(sku, color, file_id)
