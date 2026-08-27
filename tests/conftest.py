"""Umumiy fixture'lar."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta

import pytest
import pytest_asyncio

from aiogram import Dispatcher

from povtor_bot.config import Settings
from povtor_bot.core.models import Candidate
from povtor_bot.db import conn

TODAY = date(2026, 8, 20)


@pytest.fixture(autouse=True, scope="session")
def isolate_env_file():
    """Testlar dasturchining haqiqiy .env fayliga BOG'LIQ BO'LMASIN.

    Bu himoya bo'lmasa, lokal .env dagi qiymat (masalan FILIAL_SHOP_IDS) test
    ichidagi Settings ga sizib kiradi va test bir mashinada o'tib, boshqasida
    yiqiladi — yoki bundan ham yomoni, notog'ri sababdan o'tib ketadi.
    """
    original = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    try:
        yield
    finally:
        Settings.model_config["env_file"] = original


@pytest_asyncio.fixture
async def database(tmp_path) -> AsyncIterator[None]:
    """Har bir test uchun toza SQLite fayli."""
    await conn.close()
    await conn.connect(str(tmp_path / "test.db"))
    try:
        yield
    finally:
        await conn.close()


def make_candidate(**overrides) -> Candidate:
    """Testlar uchun to'ldirilgan nomzod; kerakli maydonlar overrides bilan almashtiriladi."""
    defaults = dict(
        detected_date=TODAY,
        shop_id="shop1",
        shop_name="ANDALUS",
        sku="39666",
        color="Белый",
        arrived_date=TODAY - timedelta(days=2),
        base_qty=5,
        sold_qty=5,
        percent=100.0,
        days_to_50=2,
        grade="ishonchli",
        recommended_qty=10,
        note="100% sotildi",
        category_group="Poyasnaya",
        subcategory="Рубашка с дл/р",
        kind="Однотонный",
        product_name="Рубашка 39666",
        supplier="Sharof M255",
        product_id="uuid-1",
        image_url="https://cdn.example/39666.jpg",
        supply_price=100000.0,
        supply_currency="UZS",
        price_uzs=100000,
    )
    defaults.update(overrides)
    return Candidate(**defaults)  # type: ignore[arg-type]


# ─────────────────────── Dispatcher ───────────────────────
# aiogram router'lari modul darajasidagi singleton va bitta Dispatcher'ga
# faqat bir marta bog'lanadi. Ishlab chiqarishda Dispatcher ham bitta, ya'ni
# bu cheklov emas. Testlarda esa u BITTA marta yig'ilib, kontekst obyektlari
# (gateway/settings) har test uchun almashtiriladi.
_DISPATCHER: Dispatcher | None = None


def build_dispatcher(gateway, settings) -> Dispatcher:
    """main.py dagi yig'ilishning aynan o'zi."""
    from povtor_bot.bot.handlers import commands, menu
    from povtor_bot.bot.middlewares import AuthMiddleware

    global _DISPATCHER
    if _DISPATCHER is None:
        dispatcher = Dispatcher()
        auth = AuthMiddleware(settings.allowed_user_ids, settings.announce_chat_id)
        dispatcher.message.middleware(auth)
        dispatcher.callback_query.middleware(auth)
        dispatcher.include_router(commands.router)
        dispatcher.include_router(menu.router)
        _DISPATCHER = dispatcher

    _DISPATCHER["settings"] = settings
    _DISPATCHER["gateway"] = gateway
    return _DISPATCHER
