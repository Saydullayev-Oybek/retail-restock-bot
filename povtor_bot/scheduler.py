"""Kunlik avtomatik tekshiruv.

Menejer /tekshir ni ishlatishni unutib qo'ysa ham ma'lumot yangilanib tursin.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .billz.gateway import BillzGateway
from .bot import texts
from .config import Settings
from .services import backup as backup_service
from .services import check as check_service

log = logging.getLogger(__name__)


def build_scheduler(bot: Bot, gateway: BillzGateway, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    hour, minute = settings.schedule_hour_minute

    async def daily_check() -> None:
        log.info("Kunlik avtomatik tekshiruv boshlandi")
        # Nusxa tekshiruvdan OLDIN: kechagi holat saqlanib qoladi, ya'ni
        # bugungi tekshiruv nimanidir buzsa ham orqaga qaytish mumkin
        if settings.backup_dir:
            await backup_service.make_backup(
                settings.backup_dir, settings.backup_keep_days
            )
        try:
            result = await check_service.run_check(gateway, settings)
        except check_service.CheckAlreadyRunning:
            # Menejer aynan shu daqiqada qo'lda ishga tushirgan — takrorlash shart emas
            log.info("Tekshiruv allaqachon ketyapti, cron o'tkazib yuborildi")
            return
        if not result.ok:
            log.error("Kunlik tekshiruv xatosi: %s", result.error)
        # Natijani menejerlarga xabar qilamiz — ular ertalab tayyor ro'yxat ko'rsin
        for user_id in settings.allowed_user_ids:
            try:
                await bot.send_message(user_id, texts.check_report(result))
            except Exception:  # noqa: BLE001 — bitta menejer bloklagani qolganini to'xtatmasin
                log.warning("Xabar yuborilmadi: user_id=%s", user_id, exc_info=True)

    scheduler.add_job(
        daily_check,
        CronTrigger(hour=hour, minute=minute, timezone=settings.timezone),
        id="daily_check",
        # Bot o'chiq bo'lgan vaqtda o'tkazib yuborilgan ish qayta ishlamasin,
        # lekin kechikish (masalan 10 daqiqa) kechirilsin
        misfire_grace_time=3600,
        coalesce=True,
        replace_existing=True,
    )
    log.info("Kunlik tekshiruv rejalashtirildi: %02d:%02d (%s)", hour, minute, settings.tz)
    return scheduler
