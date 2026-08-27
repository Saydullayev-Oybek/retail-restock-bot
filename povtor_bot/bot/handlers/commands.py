"""Buyruqlar: /start, /tekshir, /buyurtma, /yangi, /export."""

from __future__ import annotations

import logging
from datetime import date

from aiogram import Bot, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message

from ...billz.gateway import BillzGateway
from ...config import Settings
from ...db import repo
from ...services import announce as announce_service
from ...services import check as check_service
from ...services import export as export_service
from .. import keyboards, texts
from .menu import categories_rows

log = logging.getLogger(__name__)
router = Router(name="commands")

START_TEXT = (
    "👋 <b>POVTOR ZAKAZ boti</b>\n\n"
    "/tekshir — Billz'dan yangi ma'lumot olib nomzodlarni hisoblash\n"
    "/buyurtma — ro'yxatni ochish (kategoriya → ta'minotchi → artikul)\n"
    "/yangi — yangi kelgan tovarlarni guruhga e'lon qilish\n"
    "/export — bugungi javoblarni Excel'ga chiqarish\n\n"
    "<i>Birinchi marta /tekshir ni ishlating — aks holda ko'rsatadigan "
    "narsa bo'lmaydi.</i>"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


@router.message(Command("tekshir"))
async def cmd_check(message: Message, gateway: BillzGateway, settings: Settings) -> None:
    """Billz'dan tortadi, hisoblaydi, yangi nomzodlarni yozadi."""
    if check_service.is_running():
        await message.answer(
            "⏳ Tekshiruv allaqachon ketyapti — tugashini kuting.\n"
            "<i>Odatda 1-2 daqiqa oladi.</i>"
        )
        return

    notice = await message.answer("⏳ Billz'dan ma'lumot olinmoqda…")
    try:
        result = await check_service.run_check(gateway, settings)
    except check_service.CheckAlreadyRunning:
        await notice.edit_text("⏳ Tekshiruv allaqachon ketyapti — tugashini kuting.")
        return
    await notice.edit_text(texts.check_report(result))


@router.message(Command("buyurtma"))
async def cmd_order(message: Message) -> None:
    """Kaskadli menyuning 1-darajasi."""
    rows = await categories_rows()
    if not rows:
        total = await repo.open_count()
        if total == 0 and await repo.latest_detected_date() is None:
            await message.answer(
                "Baza bo'sh. Avval /tekshir ni ishlating."
            )
        else:
            await message.answer("Hal qilinmagan band qolmadi. 🎉")
        return
    await message.answer(
        "📂 <b>Kategoriya tanlang</b>", reply_markup=keyboards.categories_kb(rows)
    )


@router.message(Command("yangi"))
async def cmd_announce(
    message: Message, bot: Bot, gateway: BillzGateway, settings: Settings
) -> None:
    """So'nggi kunlarda kelgan tovarlarni umumiy guruhga e'lon qiladi."""
    if not settings.announce_chat_id:
        await message.answer("⚠️ ANNOUNCE_CHAT_ID sozlanmagan.")
        return

    notice = await message.answer("⏳ Yangi kelgan tovarlar yig'ilmoqda…")
    entries, error = await check_service.recent_arrivals(gateway, settings)
    if error:
        await notice.edit_text(f"⚠️ {texts.esc(error)}")
        return
    if not entries:
        await notice.edit_text("Yangi tovar yo'q (yoki hammasi allaqachon e'lon qilingan).")
        return

    await notice.edit_text(f"📤 {len(entries)} ta tovar yuborilmoqda…")
    usd_rate = await _usd_rate_quietly(gateway)
    sent, failed = await announce_service.announce(
        bot, settings.announce_chat_id, entries, usd_rate
    )
    summary = f"✅ Guruhga yuborildi: <b>{sent}</b> ta tovar"
    if failed:
        summary += f"\n⚠️ Yuborilmadi: {failed} ta"
    await notice.edit_text(summary)


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """BUGUN javob berilgan bandlarni xlsx qilib qaytaradi.

    Sana javob berilgan kun bo'yicha olinadi (aniqlangan kun emas): band bir
    necha kun oldin aniqlanib, bugun hal qilingan bo'lishi mumkin, va kunlik
    hisobot aynan bugungi QARORLARNI ko'rsatishi kerak.
    """
    report_date = date.today()
    rows = await repo.answered_for_export(report_date)
    if not rows:
        await message.answer(
            f"{report_date.isoformat()} uchun javob berilgan band yo'q."
        )
        return

    content = export_service.build_workbook(rows, report_date)
    await message.answer_document(
        BufferedInputFile(content, filename=export_service.filename(report_date)),
        caption=(
            f"📊 <b>{report_date.isoformat()}</b>\n"
            f"Jami javob berilgan: <b>{len(rows)}</b> ta band"
        ),
    )


async def _usd_rate_quietly(gateway: BillzGateway) -> float:
    """Kurs olinmasa e'lon to'xtamasin — tannarx shunchaki ko'rsatilmaydi."""
    try:
        return await gateway.usd_rate()
    except Exception:  # noqa: BLE001
        log.warning("USD kursi olinmadi", exc_info=True)
        return 0.0
