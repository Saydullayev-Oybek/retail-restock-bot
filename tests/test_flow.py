"""Uchdan-uchgacha oqim: haqiqiy Update -> Dispatcher -> handler -> baza.

Nega bu test kerak: qolgan testlar funksiyalarni TO'G'RIDAN-TO'G'RI chaqiradi.
Bu esa aiogram'ning o'zi ishlaydigan yo'lni tekshiradi — middleware o'tkazadimi,
dispatcher["gateway"] handler argumentiga bog'lanadimi, callback filtri
to'g'ri handler'ni tanlaydimi. Bularning har biri qolgan hamma test o'tsa ham
botni ishlamas holga keltira oladi.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, Update, User

from povtor_bot.bot.callbacks import AnswerCB, CategoryCB
from povtor_bot.config import Settings
from povtor_bot.core.models import STATUS_NOT_FOUND, STATUS_TAKEN
from povtor_bot.db import repo
from povtor_bot.services import check as check_service

from .conftest import TODAY, build_dispatcher, make_candidate
from .test_check import FakeGateway, a_product, a_sale, a_transfer

pytestmark = pytest.mark.usefixtures("database")

MANAGER = 111
STRANGER = 999
CHAT = 111


class RecordingBot(Bot):
    """Tarmoqqa chiqmaydigan bot: har bir Telegram metodini yozib boradi."""

    def __init__(self) -> None:
        super().__init__(
            token="123456:AAFAKE-TOKEN-FOR-TESTS",
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.sent: list[tuple[str, dict]] = []
        self._message_id = 1000

    async def __call__(self, method: TelegramMethod, request_timeout=None):
        name = type(method).__name__
        payload = method.model_dump(exclude_none=True)
        self.sent.append((name, payload))
        self._message_id += 1
        if name in ("SendMessage", "SendPhoto", "SendDocument"):
            # .as_(self) — haqiqiy aiogram ham shunday qiladi; busiz qaytgan
            # Message ustida .edit_text() chaqirib bo'lmaydi
            return Message(
                message_id=self._message_id,
                date=datetime(2026, 8, 20, 9, 0),
                chat=Chat(id=payload.get("chat_id", CHAT), type="private"),
            ).as_(self)
        return True

    def texts_of(self, method: str) -> list[str]:
        return [
            p.get("text") or p.get("caption") or ""
            for name, p in self.sent if name == method
        ]

    @property
    def method_names(self) -> list[str]:
        return [name for name, _ in self.sent]


def settings_for(**overrides) -> Settings:
    base = dict(
        bot_token="T", allowed_user_ids=[MANAGER], warehouse_shop_ids=["sklad-uuid"],
        billz_secret_token="s",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def command_update(text: str, user_id: int = MANAGER, update_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime(2026, 8, 20, 9, 0),
            chat=Chat(id=CHAT if user_id == MANAGER else user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="M"),
            text=text,
            entities=[{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}],
        ),
    )


def callback_update(data: str, user_id: int = MANAGER, update_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        callback_query={
            "id": f"cb-{update_id}",
            "from": {"id": user_id, "is_bot": False, "first_name": "M"},
            "chat_instance": "ci",
            "data": data,
            "message": {
                "message_id": 500,
                "date": datetime(2026, 8, 20, 9, 0),
                "chat": {"id": CHAT, "type": "private"},
            },
        },
    )


@pytest.fixture
def no_download(monkeypatch):
    from povtor_bot.services import media

    async def fake_download(url: str) -> bytes | None:
        return None          # rasm yo'q -> matnli karta (testda oddiyroq)

    monkeypatch.setattr(media, "_download", fake_download)


class TestAccess:
    async def test_stranger_gets_refusal_not_data(self) -> None:
        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(bot, command_update("/buyurtma", user_id=STRANGER))
        assert bot.method_names == ["SendMessage"]
        assert "yopiq" in bot.texts_of("SendMessage")[0]
        await bot.session.close()

    async def test_manager_gets_start_help(self) -> None:
        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(bot, command_update("/start"))
        text = bot.texts_of("SendMessage")[0]
        assert "/tekshir" in text and "/buyurtma" in text
        await bot.session.close()


class TestCheckCommand:
    async def test_check_pulls_and_reports(self, monkeypatch) -> None:
        """/tekshir Billz'ga boradi, hisoblaydi va natijani xabar qilib qaytaradi."""
        gateway = FakeGateway(
            transfers=[
                a_transfer("shop1", "39666", "Белый", TODAY - timedelta(days=2), 5),
            ],
            sales=[
                a_sale("shop1", "39666", "Белый", TODAY - timedelta(days=1), 5),
            ],
            products=[a_product()],
        )
        bot = RecordingBot()
        dispatcher = build_dispatcher(gateway, settings_for())

        # Handler `today` ni bermaydi (date.today() ishlatiladi) — testda sanani
        # qotirish uchun run_check vaqtincha almashtiriladi
        original = check_service.run_check
        monkeypatch.setattr(
            check_service, "run_check",
            lambda gw, st, *, today=None: original(gw, st, today=TODAY),
        )
        await dispatcher.feed_update(bot, command_update("/tekshir"))

        assert "shops" in gateway.calls and "transfers" in gateway.calls
        assert bot.method_names == ["SendMessage", "EditMessageText"]
        assert "Tekshiruv tugadi" in bot.texts_of("EditMessageText")[0]
        assert await repo.open_count(TODAY) == 1
        await bot.session.close()


class TestOrderMenu:
    async def test_empty_database_tells_user_to_check_first(self) -> None:
        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(bot, command_update("/buyurtma"))
        assert "/tekshir" in bot.texts_of("SendMessage")[0]
        await bot.session.close()

    async def test_shows_categories_with_counts(self) -> None:
        await repo.insert_candidates([
            make_candidate(category_group="Obuv", sku="1"),
            make_candidate(category_group="Obuv", sku="2"),
            make_candidate(category_group="Poyasnaya", sku="3"),
        ])
        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(bot, command_update("/buyurtma"))

        name, payload = bot.sent[0]
        assert name == "SendMessage" and "Kategoriya tanlang" in payload["text"]
        labels = [
            b["text"] for row in payload["reply_markup"]["inline_keyboard"] for b in row
        ]
        assert "Obuv · 2" in labels and "Poyasnaya · 1" in labels
        await bot.session.close()

    async def test_category_click_lists_suppliers(self) -> None:
        await repo.insert_candidates([
            make_candidate(category_group="Obuv", supplier="Bektosh M291", sku="1"),
        ])
        cat_ref = await repo.ref_id("cat", "Obuv")
        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())

        await dispatcher.feed_update(bot, callback_update(CategoryCB(ref=cat_ref).pack()))
        assert "EditMessageText" in bot.method_names
        text = bot.texts_of("EditMessageText")[0]
        assert "Obuv" in text and "Ta'minotchi" in text
        await bot.session.close()


class TestAnswerFlow:
    async def _one_candidate(self) -> int:
        await repo.insert_candidates([make_candidate()])
        rows = await repo.card_items("39666", TODAY)
        return rows[0]["id"]

    async def test_taken_button_writes_status(self, no_download) -> None:
        cid = await self._one_candidate()
        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(
            bot, callback_update(AnswerCB(id=cid, act="t", cat=1, sup=1).pack())
        )
        row = await repo.get_candidate(cid)
        assert row["status"] == STATUS_TAKEN and row["answered_by"] == MANAGER
        assert "AnswerCallbackQuery" in bot.method_names
        await bot.session.close()

    async def test_not_found_adds_transfer_hint(self, no_download) -> None:
        """"BOZORDA YO'Q" bosilganda boshqa filialdagi qoldiq taklif qilinadi."""
        cid = await self._one_candidate()
        await repo.replace_stock_snapshot(
            [("shop2", "BERUNIY", "39666", "Белый", 7)], TODAY
        )
        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(
            bot, callback_update(AnswerCB(id=cid, act="n", cat=1, sup=1).pack())
        )
        row = await repo.get_candidate(cid)
        assert row["status"] == STATUS_NOT_FOUND
        assert "BERUNIY: 7 dona" in row["transfer_hint"]

        alerts = [p.get("text", "") for n, p in bot.sent if n == "AnswerCallbackQuery"]
        assert any("BERUNIY" in a for a in alerts)
        await bot.session.close()

    async def test_only_the_clicked_item_changes(self, no_download) -> None:
        """Asosiy talab: bitta tugma FAQAT o'z bandini o'zgartiradi."""
        await repo.insert_candidates([
            make_candidate(shop_id="shop1", shop_name="ANDALUS", color="Белый"),
            make_candidate(shop_id="shop1", shop_name="ANDALUS", color="Синий"),
            make_candidate(shop_id="shop2", shop_name="BERUNIY", color="Белый"),
        ])
        rows = await repo.card_items("39666", TODAY)
        target = next(r for r in rows if r["shop_name"] == "ANDALUS" and r["color"] == "Синий")

        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(
            bot, callback_update(AnswerCB(id=target["id"], act="t", cat=1, sup=1).pack())
        )

        after = {(r["shop_name"], r["color"]): r["status"] for r in
                 await repo.card_items("39666", TODAY)}
        assert after[("ANDALUS", "Синий")] == STATUS_TAKEN
        assert after[("ANDALUS", "Белый")] == "pending"
        assert after[("BERUNIY", "Белый")] == "pending"
        await bot.session.close()

    async def test_double_click_keeps_first_answer(self, no_download) -> None:
        cid = await self._one_candidate()
        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(
            bot, callback_update(AnswerCB(id=cid, act="t", cat=1, sup=1).pack(), update_id=1)
        )
        await dispatcher.feed_update(
            bot, callback_update(AnswerCB(id=cid, act="n", cat=1, sup=1).pack(), update_id=2)
        )
        assert (await repo.get_candidate(cid))["status"] == STATUS_TAKEN
        alerts = [p.get("text", "") for n, p in bot.sent if n == "AnswerCallbackQuery"]
        assert any("allaqachon" in a for a in alerts)
        await bot.session.close()

    async def test_reset_returns_item_to_pending(self, no_download) -> None:
        cid = await self._one_candidate()
        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(
            bot, callback_update(AnswerCB(id=cid, act="t", cat=1, sup=1).pack(), update_id=1)
        )
        await dispatcher.feed_update(
            bot, callback_update(AnswerCB(id=cid, act="r", cat=1, sup=1).pack(), update_id=2)
        )
        assert (await repo.get_candidate(cid))["status"] == "pending"
        await bot.session.close()


class TestExportCommand:
    async def test_sends_xlsx_document(self) -> None:
        await repo.insert_candidates([make_candidate()])
        rows = await repo.card_items("39666", TODAY)
        await repo.answer_candidate(rows[0]["id"], status=STATUS_TAKEN, user_id=MANAGER)

        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(bot, command_update("/export"))
        assert "SendDocument" in bot.method_names
        document = next(p for n, p in bot.sent if n == "SendDocument")
        # fayl nomi hisobot sanasi bo'yicha — javob bugun berildi
        assert document["document"].filename == f"POVTOR_{date.today().isoformat()}.xlsx"
        await bot.session.close()

    async def test_nothing_answered_says_so(self) -> None:
        await repo.insert_candidates([make_candidate()])
        bot, dispatcher = RecordingBot(), build_dispatcher(FakeGateway(), settings_for())
        await dispatcher.feed_update(bot, command_update("/export"))
        assert "SendDocument" not in bot.method_names
        assert "javob berilgan band yo'q" in bot.texts_of("SendMessage")[0]
        await bot.session.close()


class TestAnnounceCommand:
    async def test_missing_chat_id_is_reported(self) -> None:
        bot, dispatcher = RecordingBot(), build_dispatcher(
            FakeGateway(), settings_for(announce_chat_id=None)
        )
        await dispatcher.feed_update(bot, command_update("/yangi"))
        assert "ANNOUNCE_CHAT_ID" in bot.texts_of("SendMessage")[0]
        await bot.session.close()
