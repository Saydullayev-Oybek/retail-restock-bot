"""bot/middlewares.py — ruxsat nazorati.

Nega alohida test: bu yagona to'siq. Agar u ochilib qolsa, botni istalgan
odam ishlatib, do'kon ma'lumotlarini ko'ra oladi.

Testlar HAQIQIY aiogram tiplarini ishlatadi: middleware isinstance bilan
tekshiradi, va xavfsizlik chegarasida soxta obyekt bilan test qilish
"o'tdi, lekin ishlamaydi" holatini yashirib qo'yishi mumkin.
"""

from __future__ import annotations

from datetime import datetime

from aiogram.types import CallbackQuery, Chat, Message, User

from povtor_bot.bot.middlewares import DENIED_TEXT, AuthMiddleware

ALLOWED = 111
STRANGER = 999
ANNOUNCE_CHAT = -1001234567890


def make_user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Test")


def make_message(chat_id: int, user_id: int) -> Message:
    return Message(
        message_id=1,
        date=datetime(2026, 8, 20, 9, 0),
        chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
        from_user=make_user(user_id),
    )


def make_callback(chat_id: int, user_id: int) -> CallbackQuery:
    return CallbackQuery(
        id="cb-1",
        from_user=make_user(user_id),
        chat_instance="ci-1",
        data="cat:1",
        message=make_message(chat_id, user_id),
    )


class Spy:
    """Middleware chaqirgan javob metodini ushlab qoladi."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def __call__(self, text: str = "", *args, **kwargs) -> None:
        self.texts.append(text)


async def run(middleware: AuthMiddleware, event, user_id: int | None):
    reached: list[object] = []

    async def handler(event_, data_):
        reached.append(event_)
        return "handled"

    data = {"event_from_user": make_user(user_id) if user_id is not None else None}
    result = await middleware(handler, event, data)
    return result, reached


class TestMessages:
    async def test_allowed_user_reaches_handler(self) -> None:
        result, reached = await run(
            AuthMiddleware([ALLOWED]), make_message(ALLOWED, ALLOWED), ALLOWED
        )
        assert result == "handled" and len(reached) == 1

    async def test_stranger_is_blocked_with_notice(self, monkeypatch) -> None:
        spy = Spy()
        monkeypatch.setattr(Message, "answer", spy)
        result, reached = await run(
            AuthMiddleware([ALLOWED]), make_message(STRANGER, STRANGER), STRANGER
        )
        assert result is None and reached == []
        assert spy.texts == [DENIED_TEXT]

    async def test_empty_allowlist_blocks_everyone(self, monkeypatch) -> None:
        monkeypatch.setattr(Message, "answer", Spy())
        result, reached = await run(AuthMiddleware([]), make_message(1, 1), 1)
        assert result is None and reached == []

    async def test_missing_user_is_blocked(self, monkeypatch) -> None:
        monkeypatch.setattr(Message, "answer", Spy())
        result, reached = await run(
            AuthMiddleware([ALLOWED]), make_message(1, 1), None
        )
        assert result is None and reached == []


class TestCallbacks:
    async def test_allowed_user_reaches_handler(self) -> None:
        result, reached = await run(
            AuthMiddleware([ALLOWED]), make_callback(ALLOWED, ALLOWED), ALLOWED
        )
        assert result == "handled" and len(reached) == 1

    async def test_stranger_gets_alert(self, monkeypatch) -> None:
        spy = Spy()
        monkeypatch.setattr(CallbackQuery, "answer", spy)
        result, reached = await run(
            AuthMiddleware([ALLOWED]), make_callback(5, STRANGER), STRANGER
        )
        assert result is None and reached == []
        assert spy.texts == [DENIED_TEXT]


class TestAnnounceGroup:
    async def test_group_events_are_ignored_silently(self, monkeypatch) -> None:
        """E'lon guruhida bot faqat yozadi — u yerdagi xabarlarga javob bermaydi."""
        spy = Spy()
        monkeypatch.setattr(Message, "answer", spy)
        result, reached = await run(
            AuthMiddleware([ALLOWED], announce_chat_id=ANNOUNCE_CHAT),
            make_message(ANNOUNCE_CHAT, STRANGER),
            STRANGER,
        )
        assert result is None and reached == []
        assert spy.texts == []      # "bot yopiq" xabari guruhga tushmadi

    async def test_allowed_user_still_works_in_announce_group(self) -> None:
        result, _ = await run(
            AuthMiddleware([ALLOWED], announce_chat_id=ANNOUNCE_CHAT),
            make_message(ANNOUNCE_CHAT, ALLOWED),
            ALLOWED,
        )
        assert result == "handled"
