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
import subprocess
from pathlib import Path


def _process_state(pid: str) -> str:
    """Jarayonning holati (ps STAT ustuni). Aniqlab bo'lmasa bo'sh satr."""
    if not pid.isdigit():
        return ""
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", pid],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


class AlreadyRunning(RuntimeError):
    """Boshqa nusxa allaqachon ishlayapti."""

    def __init__(self, pid: str, path: Path) -> None:
        state = _process_state(pid)
        lines = [f"Bot allaqachon ishlayapti (PID {pid}). Qulf: {path}"]

        # Ctrl+Z bosilgan jarayon xotirada qoladi va qulfni USHLAB TURADI,
        # lekin hech nima qilmaydi — Telegram'dan xabar olmaydi. Tashqaridan
        # bu "bot ishlayapti, lekin javob bermayapti" bo'lib ko'rinadi va
        # sababini topish qiyin.
        if state.startswith("T"):
            lines += [
                "",
                "⚠️  Lekin u TO'XTATILGAN holatda (Ctrl+Z bosilgan).",
                "    Qulfni ushlab turibdi, ammo ishlamayapti.",
                "",
                f"    Davom ettirish : fg   yoki   kill -CONT {pid}",
                f"    Butunlay yopish: kill {pid}",
            ]
        else:
            lines += ["", f"To'xtatish: kill {pid}   yoki   pkill -f povtor_bot.main"]

        super().__init__("\n".join(lines))
        self.pid = pid
        self.state = state


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
