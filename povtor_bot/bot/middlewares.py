"""Auth middleware.

Nega middleware, har handler'da tekshirish emas: ruxsat tekshiruvi bitta joyda
bo'lsa, yangi handler qo'shilganda uni himoyalashni unutib bo'lmaydi.
Ro'yxatdan o'tmagan foydalanuvchi handler'gacha umuman yetib bormaydi.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

log = logging.getLogger(__name__)

DENIED_TEXT = "Bu bot yopiq. Ruxsat uchun administratorga murojaat qiling."


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_user_ids: list[int], announce_chat_id: int | None = None) -> None:
        self._allowed = set(allowed_user_ids)
        # E'lon guruhida bot faqat yozadi — u yerdagi hodisalar jim o'tkazib yuboriladi
        self._announce_chat_id = announce_chat_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is not None and user.id in self._allowed:
            return await handler(event, data)

        chat = getattr(event, "chat", None) or getattr(
            getattr(event, "message", None), "chat", None
        )
        chat_id = getattr(chat, "id", None)
        if chat_id is not None and chat_id == self._announce_chat_id:
            return None   # e'lon guruhi — javob bermaymiz

        if user is not None:
            log.warning("Ruxsatsiz urinish: user_id=%s chat_id=%s", user.id, chat_id)

        if isinstance(event, CallbackQuery):
            await event.answer(DENIED_TEXT, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(DENIED_TEXT)
        return None
