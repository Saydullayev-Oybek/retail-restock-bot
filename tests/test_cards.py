"""services/cards.py — rasm ↔ matn almashinuvi va rasm keshi.

Bu taskdagi eng nozik UX talabi: Telegram matnli xabarni rasmli xabarga
tahrirlay olmaydi. Test aynan shu chegaraviy holatlarni qotiradi.
"""

from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from povtor_bot.db import repo
from povtor_bot.services import cards, media

from .conftest import TODAY, make_candidate

pytestmark = pytest.mark.usefixtures("database")

MARKUP = InlineKeyboardMarkup(inline_keyboard=[])


@pytest.fixture(autouse=True)
def image_base(monkeypatch):
    """Rasm yo'li ochiq bo'lsin.

    Billz `main_image_url` da faqat fayl nomini beradi, shuning uchun to'liq
    manzil BILLZ_IMAGE_BASE_URL bilan yig'iladi. U sozlanmagan bo'lsa rasm
    umuman ko'rsatilmaydi — kartalar testi uchun sozlangan holatni taqlid qilamiz.
    """
    from povtor_bot.config import get_settings

    monkeypatch.setenv("BOT_TOKEN", "T")
    monkeypatch.setenv("BILLZ_IMAGE_BASE_URL", "https://cdn.example/img")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakePhotoSize:
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


class FakeMessage:
    def __init__(self, message_id: int, photo: list | None = None) -> None:
        self.message_id = message_id
        self.photo = photo


class FakeBot:
    """Telegram chaqiruvlarini yozib boradigan bot."""

    def __init__(self, *, photo_fails: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.photo_fails = photo_fails
        self._next_id = 100

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kw):
        self.calls.append(("send_photo", {"caption": caption, "photo": photo}))
        if self.photo_fails:
            raise TelegramBadRequest(method=None, message="wrong file identifier")
        return FakeMessage(self._new_id(), photo=[FakePhotoSize("NEW-FILE-ID")])

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.calls.append(("send_message", {"text": text}))
        return FakeMessage(self._new_id())

    async def edit_message_caption(self, chat_id, message_id, caption=None, **kw):
        self.calls.append(("edit_caption", {"caption": caption}))

    async def edit_message_text(self, chat_id, message_id, text=None, **kw):
        self.calls.append(("edit_text", {"text": text}))

    async def delete_message(self, chat_id, message_id):
        self.calls.append(("delete", {"message_id": message_id}))

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


async def seed_rows(*, with_image: bool):
    await repo.insert_candidates([make_candidate()])
    await repo.cache_products([{
        "sku": "39666", "color": "Белый", "product_name": "Рубашка",
        "image_url": "https://cdn/39666.jpg" if with_image else "",
        "supply_price": 100000, "supply_currency": "UZS",
    }])
    if not with_image:
        await repo.mark_image_missing("39666", "Белый")
    return await repo.card_items("39666", TODAY)


@pytest.fixture
def no_download(monkeypatch):
    """Rasm yuklab olishni soxtalashtiradi — testda tarmoqqa chiqilmasin."""
    async def fake_download(url: str) -> bytes | None:
        return b"\xff\xd8\xff-fake-jpeg"

    monkeypatch.setattr(media, "_download", fake_download)


class TestSendCard:
    async def test_sends_photo_when_image_exists(self, no_download) -> None:
        bot = FakeBot()
        rows = await seed_rows(with_image=True)
        await cards.send_card(bot, 1, rows, MARKUP)
        assert bot.names == ["send_photo"]

    async def test_sends_text_when_no_image(self) -> None:
        bot = FakeBot()
        rows = await seed_rows(with_image=False)
        await cards.send_card(bot, 1, rows, MARKUP)
        assert bot.names == ["send_message"]

    async def test_caches_returned_file_id(self, no_download) -> None:
        """Rasm bir marta yuklanadi — keyingi safar file_id ishlatiladi."""
        bot = FakeBot()
        rows = await seed_rows(with_image=True)
        await cards.send_card(bot, 1, rows, MARKUP)
        cached = await repo.get_cached_product("39666", "Белый")
        assert cached["tg_file_id"] == "NEW-FILE-ID"

        bot2 = FakeBot()
        await cards.send_card(bot2, 1, rows, MARKUP)
        assert bot2.calls[0][1]["photo"] == "NEW-FILE-ID"   # bayt emas, file_id

    async def test_photo_failure_falls_back_to_text(self, no_download) -> None:
        bot = FakeBot(photo_fails=True)
        rows = await seed_rows(with_image=True)
        await cards.send_card(bot, 1, rows, MARKUP)
        assert bot.names == ["send_photo", "send_message"]

    async def test_stale_file_id_is_cleared_not_blocked(self, no_download) -> None:
        """Eskirgan file_id rasmni butunlay o'chirib yubormasligi kerak."""
        await seed_rows(with_image=True)
        await repo.set_file_id("39666", "Белый", "STALE")
        rows = await repo.card_items("39666", TODAY)
        await cards.send_card(FakeBot(photo_fails=True), 1, rows, MARKUP)
        cached = await repo.get_cached_product("39666", "Белый")
        assert cached["tg_file_id"] == ""
        assert cached["image_missing"] == 0        # rasm hali ham mumkin
        assert cached["image_url"] == "https://cdn/39666.jpg"

    async def test_empty_rows_send_nothing(self) -> None:
        bot = FakeBot()
        assert await cards.send_card(bot, 1, [], MARKUP) is None
        assert bot.calls == []


class TestUpdateCard:
    async def test_same_type_edits_in_place(self, no_download) -> None:
        bot = FakeBot()
        rows = await seed_rows(with_image=True)
        message = await cards.send_card(bot, 1, rows, MARKUP)
        bot.calls.clear()
        await cards.update_card(bot, 1, message.message_id, rows, MARKUP)
        assert bot.names == ["edit_caption"]

    async def test_text_card_edits_text(self) -> None:
        bot = FakeBot()
        rows = await seed_rows(with_image=False)
        message = await cards.send_card(bot, 1, rows, MARKUP)
        bot.calls.clear()
        await cards.update_card(bot, 1, message.message_id, rows, MARKUP)
        assert bot.names == ["edit_text"]

    async def test_text_to_photo_resends_instead_of_editing(self, no_download) -> None:
        """Asosiy holat: matnli karta rasmli bo'lishi kerak -> o'chirib qayta yuboriladi."""
        bot = FakeBot()
        rows = await seed_rows(with_image=False)
        message = await cards.send_card(bot, 1, rows, MARKUP)
        assert bot.names == ["send_message"]

        # Endi rasm paydo bo'ldi
        await repo.cache_products([{
            "sku": "39666", "color": "Белый", "image_url": "https://cdn/39666.jpg",
        }])
        await repo.set_file_id("39666", "Белый", "FID")
        bot.calls.clear()

        await cards.update_card(bot, 1, message.message_id, rows, MARKUP)
        assert bot.names == ["delete", "send_photo"]

    async def test_photo_to_text_resends_when_image_gone(self, no_download) -> None:
        bot = FakeBot()
        rows = await seed_rows(with_image=True)
        message = await cards.send_card(bot, 1, rows, MARKUP)
        bot.calls.clear()

        # Rasm butunlay yo'qoldi: file_id ham, URL ham ishlamaydi
        await repo.clear_file_id("39666", "Белый")
        await repo.mark_image_missing("39666", "Белый")
        await cards.update_card(bot, 1, message.message_id, rows, MARKUP)
        assert bot.names == ["delete", "send_message"]

    async def test_cached_file_id_survives_image_missing_flag(self, no_download) -> None:
        """image_missing faqat URL yo'liga taalluqli — mavjud file_id ishlayveradi."""
        bot = FakeBot()
        rows = await seed_rows(with_image=True)
        message = await cards.send_card(bot, 1, rows, MARKUP)
        bot.calls.clear()
        await repo.mark_image_missing("39666", "Белый")
        await cards.update_card(bot, 1, message.message_id, rows, MARKUP)
        assert bot.names == ["edit_caption"]

    async def test_long_caption_falls_back_to_text(self, no_download) -> None:
        """Caption 1024 belgidan oshsa rasm bilan yubora olmaymiz — matnga o'tamiz.

        Sahifalash odatda buni oldini oladi (6 ta band ~900 belgi), lekin juda
        uzun tovar nomi bilan BITTA band ham chegaradan oshishi mumkin —
        zaxira yo'l ishlashi kerak.
        """
        await repo.insert_candidates([make_candidate(product_name="Ж" * 900)])
        await repo.cache_products([{
            "sku": "39666", "color": "Белый", "image_url": "https://cdn/x.jpg",
        }])
        await repo.set_file_id("39666", "Белый", "FID")

        rows = await repo.card_items("39666", TODAY)
        assert len(rows) == 1
        assert len(cards._caption(rows, 0, 0)) > cards._CAPTION_LIMIT

        bot = FakeBot()
        await cards.send_card(bot, 1, rows, MARKUP)
        assert bot.names == ["send_message"]

    async def test_many_rows_keep_the_photo(self, no_download) -> None:
        """Sahifalashdan keyin ko'p bandli karta ham RASM bilan chiqadi.

        Ilgari 20 ta band caption chegarasidan oshib, rasm yo'qolardi.
        """
        await seed_rows(with_image=True)
        await repo.insert_candidates([
            make_candidate(shop_id=f"s{i}", shop_name=f"FILIAL-{i}", color=f"Rang-{i}")
            for i in range(20)
        ])
        rows = await repo.card_items("39666", TODAY)
        assert len(cards._caption(rows, 0, 0)) <= cards._CAPTION_LIMIT

        bot = FakeBot()
        await cards.send_card(bot, 1, rows, MARKUP)
        assert bot.names == ["send_photo"]

    async def test_caption_fits_when_every_row_is_stale(self, no_download) -> None:
        """Eng yomon holat: sahifadagi hamma band eski, har biri qo'shimcha qator.

        "eski tekshiruvdan" belgisi caption'ni 1024 chegarasidan chiqarib
        yuborsa, karta rasmini butunlay yo'qotadi.
        """
        await seed_rows(with_image=True)
        await repo.insert_candidates([
            make_candidate(shop_id=f"s{i}", shop_name=f"SHAXRISTON-FILIAL-{i}",
                           color="Тёмно-синий/Коричневый")
            for i in range(10)
        ])
        # Yangi tekshiruv ochib yopamiz — yuqoridagilar "eski" bo'lib qoladi
        run = await repo.next_run_id()
        await repo.finish_run(run)

        rows = await repo.card_items("39666", TODAY)
        assert all(not r["is_current"] for r in rows)
        for page in range(3):
            assert len(cards._caption(rows, page, 3)) <= cards._CAPTION_LIMIT

    async def test_not_modified_is_swallowed(self) -> None:
        """Bir tugmani ikki marta bosish xato bermasligi kerak."""
        class Stubborn(FakeBot):
            async def edit_message_text(self, *a, **kw):
                self.calls.append(("edit_text", {}))
                raise TelegramBadRequest(
                    method=None, message="Bad Request: message is not modified"
                )

        bot = Stubborn()
        rows = await seed_rows(with_image=False)
        message = await cards.send_card(bot, 1, rows, MARKUP)
        bot.calls.clear()
        await cards.update_card(bot, 1, message.message_id, rows, MARKUP)
        assert bot.names == ["edit_text"]      # o'chirish/qayta yuborish bo'lmadi

    async def test_other_edit_error_triggers_resend(self) -> None:
        class Broken(FakeBot):
            async def edit_message_text(self, *a, **kw):
                self.calls.append(("edit_text", {}))
                raise TelegramBadRequest(method=None, message="message to edit not found")

        bot = Broken()
        rows = await seed_rows(with_image=False)
        message = await cards.send_card(bot, 1, rows, MARKUP)
        bot.calls.clear()
        await cards.update_card(bot, 1, message.message_id, rows, MARKUP)
        assert bot.names == ["edit_text", "delete", "send_message"]


class TestMediaCache:
    async def test_missing_image_is_not_retried(self, monkeypatch) -> None:
        """Rasm topilmasa qayta so'ralmaydi — Billz rate limit'ini asraydi."""
        downloads: list[str] = []

        async def counting_download(url: str) -> bytes | None:
            downloads.append(url)
            return None

        monkeypatch.setattr(media, "_download", counting_download)
        await repo.cache_products([{
            "sku": "1", "color": "", "image_url": "https://cdn/1.jpg",
        }])
        assert await media.resolve_photo("1", "") is None
        assert await media.resolve_photo("1", "") is None
        assert len(downloads) == 1          # ikkinchi marta urinilmadi

    async def test_has_photo_does_not_download(self, monkeypatch) -> None:
        async def boom(url: str):
            raise AssertionError("has_photo rasmni yuklab olmasligi kerak")

        monkeypatch.setattr(media, "_download", boom)
        await repo.cache_products([{"sku": "1", "color": "", "image_url": "u"}])
        assert await media.has_photo("1", "") is True

    async def test_unknown_product_has_no_photo(self) -> None:
        assert await media.has_photo("yo'q", "") is False
        assert await media.resolve_photo("yo'q", "") is None


class TestImageUrl:
    """Billz fayl nomidan to'liq manzil yasash."""

    def test_filename_gets_base_prefix(self) -> None:
        assert media.full_image_url("abc.jpg") == "https://cdn.example/img/abc.jpg"

    def test_full_url_passes_through(self) -> None:
        assert media.full_image_url("https://x/y.jpg") == "https://x/y.jpg"

    def test_empty_reference(self) -> None:
        assert media.full_image_url("") == ""
        assert media.full_image_url("   ") == ""

    def test_no_base_configured_means_no_image(self, monkeypatch) -> None:
        """BILLZ_IMAGE_BASE_URL sozlanmagan bo'lsa rasm ko'rsatilmaydi."""
        from povtor_bot.config import get_settings

        monkeypatch.delenv("BILLZ_IMAGE_BASE_URL", raising=False)
        get_settings.cache_clear()
        try:
            assert media.full_image_url("abc.jpg") == ""
        finally:
            get_settings.cache_clear()

    async def test_missing_base_does_not_mark_image_missing(self, monkeypatch) -> None:
        """Sozlama qo'shilishi bilan rasm paydo bo'lishi kerak — "yo'q" deb belgilamaymiz."""
        from povtor_bot.config import get_settings

        await repo.cache_products([{"sku": "9", "color": "", "image_url": "z.jpg"}])
        monkeypatch.delenv("BILLZ_IMAGE_BASE_URL", raising=False)
        get_settings.cache_clear()
        try:
            assert await media.resolve_photo("9", "") is None
            cached = await repo.get_cached_product("9", "")
            assert cached["image_missing"] == 0
        finally:
            get_settings.cache_clear()


class TestCardPagination:
    """Karta sahifalanadi.

    35 ta band (7 filial x 5 rang) Telegram'ning 100 tugma chegarasidan
    oshib ketardi va xabar BUTUNLAY rad etilardi — menejer nima bo'lganini
    bilmasdi. Sahifa hajmi 8 ta band: 24 tugma va ~950 belgi, ya'ni karta
    RASM bilan chiqishda davom etadi.
    """

    async def _many(self, n: int):
        from datetime import timedelta
        await repo.insert_candidates([
            make_candidate(shop_id=f"s{i}", shop_name=f"FILIAL-{i}",
                           color=f"Rang-{i}", arrived_date=TODAY - timedelta(days=2))
            for i in range(n)
        ])
        return await repo.card_items("39666")

    async def test_fifty_items_never_exceed_hard_limits(self, no_download) -> None:
        """QATTIQ chegara: oshsa Telegram xabarni butunlay rad etadi."""
        from povtor_bot.bot import keyboards
        from povtor_bot.services.cards import _caption

        rows = await self._many(50)
        _, _, pages = keyboards.page_slice(rows, 0)
        assert pages > 1
        for page in range(pages):
            caption = _caption(rows, 5, page)
            kb = keyboards.card_kb(rows, 1, 1, sku_ref=1, page=page)
            buttons = sum(len(r) for r in kb.inline_keyboard)
            assert buttons <= 100, f"{page}-sahifa: {buttons} tugma"
            assert len(caption) <= 4096, f"{page}-sahifa: {len(caption)} belgi"

    async def test_pages_usually_fit_the_caption_limit(self, no_download) -> None:
        """YUMSHOQ chegara: oshsa rasm ko'rsatilmaydi (xato emas, lekin achinarli)."""
        from povtor_bot.bot import keyboards
        from povtor_bot.services.cards import _caption

        rows = await self._many(50)
        _, _, pages = keyboards.page_slice(rows, 0)
        sigdi = sum(1 for p in range(pages) if len(_caption(rows, 5, p)) <= 1024)
        assert sigdi == pages, f"{pages - sigdi} sahifa rasmsiz qoladi"

    async def test_pending_items_come_first(self) -> None:
        from povtor_bot.bot import keyboards
        from povtor_bot.core.models import STATUS_TAKEN

        rows = await self._many(20)
        for r in rows[:5]:
            await repo.answer_candidate(r["id"], status=STATUS_TAKEN, user_id=1)
        rows = await repo.card_items("39666")
        first_page, _, _ = keyboards.page_slice(rows, 0)
        assert all(r["status"] == "pending" for r in first_page)

    async def test_page_out_of_range_is_clamped(self) -> None:
        from povtor_bot.bot import keyboards

        rows = await self._many(10)
        visible, page, pages = keyboards.page_slice(rows, 999)
        assert page == pages - 1 and visible

    async def test_single_page_has_no_nav_buttons(self, no_download) -> None:
        from povtor_bot.bot import keyboards

        rows = await self._many(3)
        kb = keyboards.card_kb(rows, 1, 1, sku_ref=1, page=0)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert "◀️" not in labels and "▶️" not in labels


class TestStaleRowHasNoAnswerButtons:
    """Eski tekshiruvdan qolgan bandga javob tugmasi berilmasligi kerak.

    Uning raqamlari o'sha paytdagi holat: menejer eskirgan ma'lumot asosida
    buyurtma berib qo'yishi mumkin edi.
    """

    async def test_stale_row_gets_only_a_label(self, no_download) -> None:
        from povtor_bot.bot import keyboards

        await seed_rows(with_image=False)
        run = await repo.next_run_id()
        await repo.finish_run(run)      # mavjud band endi "eski"
        rows = await repo.card_items("39666", TODAY)
        assert not rows[0]["is_current"]

        labels = [
            b.text
            for r in keyboards.card_kb(rows, 1, 1, sku_ref=1).inline_keyboard
            for b in r
        ]
        assert any("eski tekshiruvdan" in x for x in labels)
        assert not any("OLINDI" in x or "BOZORDA" in x for x in labels)

    async def test_current_row_keeps_its_buttons(self, no_download) -> None:
        from povtor_bot.bot import keyboards

        await seed_rows(with_image=False)
        rows = await repo.card_items("39666", TODAY)
        assert rows[0]["is_current"]

        labels = [
            b.text
            for r in keyboards.card_kb(rows, 1, 1, sku_ref=1).inline_keyboard
            for b in r
        ]
        assert any("OLINDI" in x for x in labels)
        assert any("BOZORDA" in x for x in labels)


class TestSendFailureIsReported:
    """Karta yuborilmasa menejer sababini ko'rishi kerak.

    Jim yiqilish eng yomoni: tugma bosildi, hech nima bo'lmadi, va menejer
    botni "buzilgan" deb hisoblaydi.
    """

    async def test_failure_produces_a_message(self, no_download) -> None:
        class Broken(FakeBot):
            async def send_message(self, chat_id, text, reply_markup=None, **kw):
                self.calls.append(("send_message", {"text": text}))
                if "ochilmadi" in text:
                    return FakeMessage(1)      # xato xabari o'tadi
                raise TelegramBadRequest(method=None, message="message is too long")

        bot = Broken()
        rows = await seed_rows(with_image=False)
        result = await cards.send_card(bot, 1, rows, MARKUP)
        assert result is None
        texts = [p.get("text", "") for _, p in bot.calls]
        assert any("ochilmadi" in t for t in texts)

    async def test_failure_does_not_raise(self, no_download) -> None:
        class Dead(FakeBot):
            async def send_message(self, *a, **kw):
                raise TelegramBadRequest(method=None, message="chat not found")

        rows = await seed_rows(with_image=False)
        assert await cards.send_card(Dead(), 1, rows, MARKUP) is None
