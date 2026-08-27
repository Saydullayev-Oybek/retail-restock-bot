""".env dan sozlamalarni o'qish.

Nega kerak: ALLOWED_USER_IDS kabi ro'yxatlarni pydantic sukut bo'yicha JSON deb
o'qishga urinadi, biz esa oddiy CSV yozamiz. Bu joy jim buziladi — noto'g'ri
o'qilsa bot hech kimni kiritmaydi yoki hammani kiritadi.
"""

from __future__ import annotations

import pytest

from povtor_bot.config import Settings


def build(**overrides) -> Settings:
    """Konstruktor orqali — sukut qiymatlarni tekshirish uchun."""
    base = {"bot_token": "T"}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def from_env(monkeypatch, **env) -> Settings:
    """AYNAN .env dagidek — env o'zgaruvchilari orqali.

    Nega shu muhim: pydantic-settings ro'yxat maydonini env dan o'qiyotganda
    avval JSON deb ko'radi. Konstruktorga qiymat berish bu bosqichni chetlab
    o'tadi, ya'ni konstruktor testi haqiqiy ishga tushishni ISBOTLAMAYDI.
    """
    monkeypatch.setenv("BOT_TOKEN", "T")
    for key, value in env.items():
        monkeypatch.setenv(key.upper(), value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestListParsingFromEnv:
    def test_user_ids_from_csv(self, monkeypatch) -> None:
        settings = from_env(monkeypatch, allowed_user_ids="111, 222 ,333")
        assert settings.allowed_user_ids == [111, 222, 333]

    def test_single_user_id(self, monkeypatch) -> None:
        assert from_env(monkeypatch, allowed_user_ids="111").allowed_user_ids == [111]

    def test_user_ids_empty(self, monkeypatch) -> None:
        assert from_env(monkeypatch, allowed_user_ids="").allowed_user_ids == []

    def test_category_groups_keep_spaces_inside_names(self, monkeypatch) -> None:
        """Nom ichidagi probel saqlanadi, atrofidagisi kesiladi (kirill nomlar)."""
        settings = from_env(
            monkeypatch,
            allowed_category_groups="Поясные одежды, Обувь ,Верхняя одежда",
        )
        assert settings.allowed_category_groups == [
            "Поясные одежды", "Обувь", "Верхняя одежда",
        ]

    def test_filial_ids_from_csv(self, monkeypatch) -> None:
        settings = from_env(monkeypatch, filial_shop_ids="a,b,,c")
        assert settings.filial_shop_ids == ["a", "b", "c"]

    def test_uuid_list_survives(self, monkeypatch) -> None:
        """Haqiqiy Billz shop ID'lari — UUID, vergul bilan."""
        ids = "fbfb9d6c-cbb2-43ab-8c90-e97d2eeb34d6,92302dfb-fa33-4e6a-8d19-87214852b25d"
        assert from_env(monkeypatch, filial_shop_ids=ids).filial_shop_ids == ids.split(",")

    def test_bool_flag_from_env(self, monkeypatch) -> None:
        assert from_env(
            monkeypatch, high_percent_overrides_min_sold="true"
        ).high_percent_overrides_min_sold is True

    def test_already_a_list_passes_through(self) -> None:
        assert build(allowed_user_ids=[1, 2]).allowed_user_ids == [1, 2]


class TestEnvExampleIsLoadable:
    def test_shipped_example_parses(self, monkeypatch, tmp_path) -> None:
        """.env.example nusxa ko'chirilib ishlatiladi — u o'qilishi SHART."""
        import shutil
        from pathlib import Path

        source = Path(__file__).resolve().parents[1] / ".env.example"
        target = tmp_path / ".env"
        shutil.copy(source, target)
        settings = Settings(_env_file=str(target))  # type: ignore[call-arg]
        assert settings.allowed_user_ids == [111111111, 222222222]
        # Kategoriya nomlari KIRILL va ichida probel bor — CSV bo'luvchi ularni
        # buzmasligi kerak
        assert settings.allowed_category_groups == [
            "Поясные одежды", "Плечевые одежды", "Верхняя одежда", "Обувь",
        ]
        assert settings.announce_chat_id == -1001234567890
        assert settings.schedule_hour_minute == (9, 0)
        assert settings.billz_rate_limit_rps == 1.5


class TestSchedule:
    @pytest.mark.parametrize("value, expected", [
        ("09:00", (9, 0)), ("7:30", (7, 30)), ("23:59", (23, 59)), ("6", (6, 0)),
    ])
    def test_hour_minute(self, value: str, expected: tuple[int, int]) -> None:
        assert build(schedule_time=value).schedule_hour_minute == expected

    def test_timezone_resolves(self) -> None:
        assert build(tz="Asia/Tashkent").timezone.key == "Asia/Tashkent"


class TestDefaults:
    def test_rule_defaults_match_sample_file(self) -> None:
        settings = build()
        assert (settings.window_days, settings.percent_threshold) == (5, 50.0)
        assert (settings.confident_max_days, settings.confident_min_sold) == (3, 4)
        assert (settings.qty_confident, settings.qty_normal) == (10, 5)
        assert settings.high_percent == 80.0
        assert settings.high_percent_overrides_min_sold is False

    def test_rate_limit_stays_below_billz_cap(self) -> None:
        """Billz 2 rps beradi; default undan past bo'lishi kerak."""
        assert build().billz_rate_limit_rps < 2.0

    def test_missing_bot_token_raises(self) -> None:
        with pytest.raises(Exception):
            Settings(bot_token=None)  # type: ignore[arg-type]
