"""Bitta nusxa qulfi.

Nega kerak: Telegram bitta token bilan faqat BITTA `getUpdates` iste'molchisiga
ruxsat beradi — qolganlari 409 bilan aylanib turadi. Bundan tashqari har bir
nusxada o'z cron'i bo'ladi, ya'ni N ta nusxa ertalab N marta tekshiruv
ishga tushiradi va Billz'ga N barobar yuk tushadi.

Real holat: sinov davrida 10 ta jarayon bir vaqtda ishlab qolgan, eng
eskisi 22 soat.

Qulf fayl deskriptori ustida (`flock`), fayl mazmuni ustida emas: jarayon
qanday tugasa ham (hatto `kill -9`) OS qulfni o'zi bo'shatadi, ya'ni
"o'lik qulf" qolmaydi.
"""

from __future__ import annotations

import atexit
import fcntl
import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    """Boshqa nusxa allaqachon ishlayapti."""

    def __init__(self, pid: str, path: Path) -> None:
        super().__init__(
            f"Bot allaqachon ishlayapti (PID {pid}). "
            f"Qulf: {path}\n"
            f"To'xtatish: kill {pid}   yoki   pkill -f povtor_bot.main"
        )
        self.pid = pid


def acquire(lock_path: str) -> None:
    """Qulfni oladi. Band bo'lsa AlreadyRunning tashlaydi.

    Fayl ataylab yopilmaydi — u jarayon tugaguncha ochiq turishi kerak,
    aks holda qulf bo'shab qoladi.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.seek(0)
        pid = handle.read().strip() or "noma'lum"
        handle.close()
        raise AlreadyRunning(pid, path) from None

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()

    # Jarayon normal tugaganda faylni tozalaymiz. Qulfning o'zi OS tomonidan
    # baribir bo'shatiladi, bu shunchaki chalkash PID qoldirmaslik uchun.
    atexit.register(_release, handle, path)


def _release(handle, path: Path) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        path.unlink(missing_ok=True)
    except OSError:
        pass
