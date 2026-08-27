"""Sozlamalar — faqat .env dan o'qiladi.

Nega pydantic-settings: token/parolni kod ichida qoldirmaslik uchun yagona
kirish nuqtasi kerak, va qiymatlar tipi ishga tushishda tekshirilsin —
noto'g'ri WINDOW_DAYS bilan bot ishlab ketib, keyin noto'g'ri hisoblagandan
ko'ra darhol yiqilgani afzal.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# NoDecode: pydantic-settings sukut bo'yicha ro'yxat maydonini env dan JSON deb
# o'qishga urinadi va bizning validatorimizgacha yetib bormay yiqiladi
# ("111,222" JSON emas). NoDecode shu bosqichni o'chiradi — xom satr
# _parse_* validatorlariga tushadi.
CsvInts = Annotated[list[int], NoDecode]
CsvStrs = Annotated[list[str], NoDecode]


def _split_csv(value: str | list[str] | None) -> list[str]:
    """'a, b ,c' -> ['a','b','c']. Bo'sh elementlar tashlanadi."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in value.split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ─── Telegram ───
    bot_token: str
    allowed_user_ids: CsvInts = Field(default_factory=list)
    announce_chat_id: int | None = None

    # ─── Billz ───
    billz_secret_token: str = ""
    billz_base_url: str = "https://api-admin.billz.ai"
    billz_platform_id: str = "7d4a4c38-dd84-4902-b744-0488b80a4c01"
    billz_rate_limit_rps: float = 1.5
    # Hisobot sahifasidagi qatorlar soni. Billz vaqtni QATOR soniga emas, har
    # SO'ROVGA sarflaydi (o'lchov: 500 qator 4.2s, 2000 qator 3.3s), shuning
    # uchun katta sahifa tekshiruvni sezilarli tezlashtiradi.
    # 1000 — hujjatdagi maksimum. 2000 ham sinovda toza ishladi.
    billz_page_limit: int = 1000

    # Sklad bitta emas: tarmoqda import skladi va sezoni o'tgan tovar skladi bor.
    # Qaysilari "yangi partiya" manbai hisoblanishi shu ro'yxat bilan belgilanadi.
    warehouse_shop_ids: CsvStrs = Field(default_factory=list)
    filial_shop_ids: CsvStrs = Field(default_factory=list)
    # Billz `main_image_url` da faqat fayl nomini qaytaradi ("<uuid>.jpg").
    # To'liq manzil uchun CDN bazasi kerak; bo'sh bo'lsa kartalar matn holida chiqadi.
    billz_image_base_url: str = ""
    # Artikul katalogdan necha kunda bir qayta o'qilsin
    sku_sync_days: int = 7

    # ─── Nomzod qoidasi ───
    window_days: int = 5
    percent_threshold: float = 50.0
    min_base_qty: int = 5
    confident_max_days: int = 3
    confident_min_sold: int = 4
    qty_confident: int = 10
    qty_normal: int = 5
    high_percent: float = 80.0
    # True => yuqori foiz CONFIDENT_MIN_SOLD ostonasini chetlab o'tadi
    high_percent_overrides_min_sold: bool = False
    allowed_category_groups: CsvStrs = Field(default_factory=list)

    # ─── Boshqa ───
    db_path: str = "var/povtor.db"
    tz: str = "Asia/Tashkent"
    schedule_time: str = "09:00"
    announce_lookback_days: int = 2
    raw_retention_days: int = 7
    log_level: str = "INFO"

    # pydantic ro'yxatni JSON deb o'qishga urinadi; bizda oddiy CSV — o'zimiz bo'lamiz
    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def _parse_user_ids(cls, v: object) -> list[int]:
        return [int(x) for x in _split_csv(v)]  # type: ignore[arg-type]

    @field_validator(
        "warehouse_shop_ids", "filial_shop_ids", "allowed_category_groups",
        mode="before",
    )
    @classmethod
    def _parse_str_list(cls, v: object) -> list[str]:
        return _split_csv(v)  # type: ignore[arg-type]

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def schedule_hour_minute(self) -> tuple[int, int]:
        hour, _, minute = self.schedule_time.partition(":")
        return int(hour), int(minute or 0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Butun jarayon uchun bitta nusxa — .env qayta-qayta o'qilmasin."""
    return Settings()  # type: ignore[call-arg]
