"""Kunlik zaxira nusxa.

Baza — bitta fayl. Diskda nosozlik yoki tasodifiy o'chirish bo'lsa menejerlar
bergan barcha javoblar va audit tarixi yo'qoladi.

`VACUUM INTO` ishlatiladi: u SQLite'ning o'z mexanizmi, botni to'xtatish
shart emas va natija butun (yarim yozilgan fayl chiqmaydi).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from ..db.conn import db, write_lock

log = logging.getLogger(__name__)


async def make_backup(directory: str, keep_days: int = 14) -> Path | None:
    """Bugungi nusxani yaratadi va eskilarini tozalaydi.

    Kun ichida qayta chaqirilsa nusxa yangilanadi (bir kunga bitta fayl).
    """
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"povtor-{date.today().isoformat()}.db"

    try:
        # VACUUM INTO mavjud faylga yozmaydi — avval o'chiramiz
        target.unlink(missing_ok=True)
        async with write_lock():
            await db().execute("VACUUM INTO ?", (str(target),))
    except Exception:  # noqa: BLE001 — zaxira nusxa botni to'xtatmasin
        log.exception("Zaxira nusxa olinmadi")
        return None

    _purge_old(target_dir, keep_days)
    log.info("Zaxira nusxa: %s (%.1f MB)", target, target.stat().st_size / 1024 / 1024)
    return target


def _purge_old(directory: Path, keep_days: int) -> None:
    if keep_days <= 0:
        return
    chegara = date.today() - timedelta(days=keep_days)
    for path in directory.glob("povtor-*.db"):
        try:
            kun = date.fromisoformat(path.stem.removeprefix("povtor-"))
        except ValueError:
            continue
        if kun < chegara:
            path.unlink(missing_ok=True)
            log.debug("Eski nusxa o'chirildi: %s", path.name)
