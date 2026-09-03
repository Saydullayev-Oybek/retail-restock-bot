"""Buyruqlar: /start, /tekshir, /buyurtma, /yangi, /export."""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ...billz.gateway import BillzGateway
from ...config import Settings
from ...db import repo
from ...services import announce as announce_service
from ...services import check as check_service
from ...services import export as export_service
from .. import keyboards, texts
from ..callbacks import CheckCB
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


BAND_TEXT = (
    "⏳ Tekshiruv allaqachon ketyapti — tugashini kuting.\n"
    "<i>Odatda 1-2 daqiqa oladi.</i>"
)

# Aql bovar qiladigan chegaralar. Oyna juda katta bo'lsa hisobotlar sekinlashadi
# va natija ma'nosini yo'qotadi ("tez sotilgan" 3 oylik oynada bema'ni).
MAX_WINDOW_DAYS = 60


def parse_check_args(raw: str) -> tuple[int | None, int | None, str]:
    """`/tekshir 7 60` argumentlarini o'qiydi.

    Qaytadi: (kun, foiz, xato). Argument berilmagan bo'lsa (None, None, "").
    """
    parts = (raw or "").split()
    if not parts:
        return None, None, ""
    if len(parts) > 2:
        return None, None, "Ko'p argument. Namuna: <code>/tekshir 7 60</code>"

    try:
        days = int(parts[0])
    except ValueError:
        return None, None, f"<code>{texts.esc(parts[0])}</code> — kun raqam bo'lishi kerak."
    if not 1 <= days <= MAX_WINDOW_DAYS:
        return None, None, f"Kun 1 dan {MAX_WINDOW_DAYS} gacha bo'lishi kerak."

    if len(parts) == 1:
        return days, None, ""

    try:
        percent = int(parts[1])
    except ValueError:
        return None, None, f"<code>{texts.esc(parts[1])}</code> — foiz raqam bo'lishi kerak."
    if not 1 <= percent <= 100:
        return None, None, "Foiz 1 dan 100 gacha bo'lishi kerak."
    return days, percent, ""


@router.message(Command("tekshir"))
async def cmd_check(
    message: Message, command: CommandObject, gateway: BillzGateway, settings: Settings
) -> None:
    """Tekshiruvni boshlaydi.

    Argumentsiz — oyna va chegara so'raladi (menejer har safar o'zi tanlaydi).
    `/tekshir 7` yoki `/tekshir 7 60` — darhol ishga tushadi.
    """
    # Band ekanini TUGMALARDAN OLDIN aytamiz: menejerdan tanlov so'rab,
    # keyin "band" deyish bezovta qiladi
    if check_service.is_running():
        await message.answer(BAND_TEXT)
        return

    days, percent, error = parse_check_args(command.args or "")
    if error:
        await message.answer(
            f"⚠️ {error}\n\nYoki quyidagidan tanlang:",
            reply_markup=keyboards.check_days_kb(settings.window_days),
        )
        return

    if days is None:
        await message.answer(
            "📅 <b>Necha kunlik oynada tekshiray?</b>\n"
            "<i>Tovar skladdan kelganidan beri shuncha kun kuzatiladi.</i>",
            reply_markup=keyboards.check_days_kb(settings.window_days),
        )
        return

    if percent is None:
        percent = int(settings.percent_threshold)
    await _run_and_report(message, gateway, settings, days, percent)


@router.callback_query(CheckCB.filter(F.percent == 0))
async def on_check_days(callback: CallbackQuery, callback_data: CheckCB,
                        settings: Settings) -> None:
    """1-qadam tugadi: kun tanlandi, endi chegarani so'raymiz."""
    if callback.message is not None:
        await callback.message.edit_text(
            f"📊 <b>Sotuv chegarasi?</b>\n"
            f"<i>Oyna: {callback_data.days} kun. "
            f"Shu muddatda shuncha foizi sotilgan tovarlar ajratiladi.</i>",
            reply_markup=keyboards.check_percent_kb(
                callback_data.days, int(settings.percent_threshold)
            ),
        )
    await callback.answer()


@router.callback_query(CheckCB.filter())
async def on_check_run(callback: CallbackQuery, callback_data: CheckCB,
                       gateway: BillzGateway, settings: Settings) -> None:
    """2-qadam tugadi: tekshiruvni ishga tushiramiz."""
    await callback.answer()
    if callback.message is None:
        return
    await _run_and_report(
        callback.message, gateway, settings,
        callback_data.days, callback_data.percent, edit=True,
    )


async def _run_and_report(
    message: Message, gateway: BillzGateway, settings: Settings,
    days: int, percent: int, *, edit: bool = False,
) -> None:
    """Berilgan parametrlar bilan tekshiradi va natijani ko'rsatadi."""
    matn = (
        f"⏳ Billz'dan ma'lumot olinmoqda…\n"
        f"<i>oyna {days} kun · chegara {percent}%</i>"
    )
    # edit_text qaytargan qiymatga tayanmaymiz: Telegram ba'zi holatlarda
    # Message emas, True qaytaradi. Xabar obyektining o'zi bilan ishlaymiz.
    if edit:
        await message.edit_text(matn)
        notice = message
    else:
        notice = await message.answer(matn)

    # run_check sozlamalarni Settings dan oladi, shuning uchun imzoni
    # o'zgartirmasdan o'zgartirilgan NUSXA uzatiladi
    override = settings.model_copy(update={
        "window_days": days,
        "percent_threshold": float(percent),
    })
    try:
        result = await check_service.run_check(gateway, override)
    except check_service.CheckAlreadyRunning:
        await notice.edit_text(BAND_TEXT)
        return
    await notice.edit_text(texts.check_report(result, days=days, percent=percent))


@router.message(Command("buyurtma"))
async def cmd_order(message: Message) -> None:
    """Kaskadli menyuning 1-darajasi."""
    rows = await categories_rows()
    if not rows:
        if await repo.current_run_id() == 0:
            await message.answer("Baza bo'sh. Avval /tekshir ni ishlating.")
        else:
            # Menyu OXIRGI tekshiruv natijasini ko'rsatadi, shuning uchun
            # bo'shlik ikki xil bo'lishi mumkin: hammasi hal qilingan yoki
            # tanlangan qoida hech nima topmagan
            await message.answer(
                "Oxirgi tekshiruvda ko'rsatadigan band yo'q.\n\n"
                "<i>Ro'yxat oxirgi <b>/tekshir</b> natijasini ko'rsatadi. "
                "Kengroq oyna yoki past chegara bilan qayta urinib ko'ring — "
                "masalan <code>/tekshir 7 50</code>.</i>"
            )
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
async def cmd_export(message: Message, settings: Settings) -> None:
    """BUGUN javob berilgan bandlarni xlsx qilib qaytaradi.

    Sana javob berilgan kun bo'yicha olinadi (aniqlangan kun emas): band bir
    necha kun oldin aniqlanib, bugun hal qilingan bo'lishi mumkin, va kunlik
    hisobot aynan bugungi QARORLARNI ko'rsatishi kerak.

    Kun MAHALLIY vaqt (TZ sozlamasi) bo'yicha kesiladi — baza UTC saqlaydi.
    """
    report_date = datetime.now(settings.timezone).date()
    rows = await repo.answered_for_export(report_date, tz=settings.timezone)
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
