"""Kirish nuqtasi: `python -m povtor_bot.main`."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .billz.client import BillzClient
from .billz.gateway import BillzGateway
from .bot.handlers import commands, menu
from .bot.middlewares import AuthMiddleware
from .config import Settings, get_settings
from .db import conn, repo
from .scheduler import build_scheduler

log = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="tekshir", description="Billz'dan yangi ma'lumot olish"),
    BotCommand(command="buyurtma", description="Buyurtma ro'yxatini ochish"),
    BotCommand(command="yangi", description="Yangi tovarlarni guruhga e'lon qilish"),
    BotCommand(command="export", description="Bugungi javoblarni Excel'ga chiqarish"),
]


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # aiogram va apscheduler juda gapdon — faqat ogohlantirishlarini olamiz
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def build_billz(settings: Settings) -> tuple[BillzClient, BillzGateway]:
    client = BillzClient(
        secret_token=settings.billz_secret_token,
        base_url=settings.billz_base_url,
        platform_id=settings.billz_platform_id,
        rate_limit_rps=settings.billz_rate_limit_rps,
        kv_get=repo.kv_get,
        kv_set=repo.kv_set,
        raw_sink=repo.save_raw,
    )
    return client, BillzGateway(
        client,
        page_limit=settings.billz_page_limit,
        concurrency=settings.billz_concurrency,
    )


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    if not settings.allowed_user_ids:
        log.warning("ALLOWED_USER_IDS bo'sh — botdan hech kim foydalana olmaydi")

    await conn.connect(settings.db_path)
    client, gateway = build_billz(settings)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()

    # Har ikkala oqim uchun ham auth: xabar ham, tugma bosish ham
    auth = AuthMiddleware(settings.allowed_user_ids, settings.announce_chat_id)
    dispatcher.message.middleware(auth)
    dispatcher.callback_query.middleware(auth)

    # Handler'larga tayyor obyektlarni beramiz — global o'zgaruvchi kerak emas
    dispatcher["settings"] = settings
    dispatcher["gateway"] = gateway

    dispatcher.include_router(commands.router)
    dispatcher.include_router(menu.router)

    scheduler = build_scheduler(bot, gateway, settings)
    scheduler.start()

    await bot.set_my_commands(BOT_COMMANDS)
    me = await bot.get_me()
    log.info("Bot ishga tushdi: @%s", me.username)

    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await client.aclose()
        await bot.session.close()
        await conn.close()
        log.info("Bot to'xtatildi")


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        log.info("Foydalanuvchi to'xtatdi")


if __name__ == "__main__":
    sys.exit(main())
