"""Billz REST API bilan past darajali ishlash: auth, rate limit, retry.

Billz cheklovlari (hujjatdan) shu klass dizaynini belgilaydi:
  * bitta IP dan sekundiga 2 so'rov  -> token-bucket
  * evristik DDoS analizatori bor    -> zaxira bilan 1.5 rps default
  * access_token 15 kun yashaydi     -> kv jadvalida saqlanadi, restart'da qayta olinmaydi
  * 401 kelsa refresh, u ham ishlamasa qayta login
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

_KV_ACCESS = "billz_access_token"
_KV_REFRESH = "billz_refresh_token"
_KV_EXPIRES = "billz_token_expires_at"   # unix timestamp

# Muddati tugashiga shuncha qolganda oldindan yangilaymiz — so'rov o'rtasida
# tokenning "o'lib qolishi" holatini kamaytiradi
_RENEW_MARGIN_SEC = 300


class BillzError(RuntimeError):
    """Billz javobi kutilmagan bo'lganda."""


class TokenBucket:
    """Sekundiga N so'rovdan oshmaslik uchun oddiy token-bucket.

    Nega Semaphore emas: Semaphore parallellikni cheklaydi, tezlikni emas.
    Bizga aynan "sekundiga nechta" kerak.
    """

    def __init__(self, rate_per_sec: float) -> None:
        self._interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_at = now + self._interval


class BillzClient:
    """Billz API klienti.

    `raw_sink` — xom javoblarni saqlash uchun callback (billz_raw jadvali).
    Hujjat bilan haqiqiy javob farq qilganda shu yozuvlar yagona dalil bo'ladi.
    """

    def __init__(
        self,
        *,
        secret_token: str,
        base_url: str = "https://api-admin.billz.ai",
        platform_id: str = "",
        rate_limit_rps: float = 1.5,
        max_retries: int = 3,
        timeout: float = 60.0,
        kv_get=None,
        kv_set=None,
        raw_sink=None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._secret = secret_token
        self._base = base_url.rstrip("/")
        self._platform_id = platform_id
        self._bucket = TokenBucket(rate_limit_rps)
        self._max_retries = max_retries
        self._kv_get = kv_get
        self._kv_set = kv_set
        self._raw_sink = raw_sink
        self._http = client or httpx.AsyncClient(timeout=timeout)
        self._owns_http = client is None
        self._auth_lock = asyncio.Lock()
        self._access: str = ""
        self._refresh: str = ""
        self._expires_at: float = 0.0

    # ───────────────────────── auth ─────────────────────────

    async def _load_tokens(self) -> None:
        """Tokenni kv dan tiklaydi — bot restart bo'lganda qayta login qilmaslik uchun."""
        if self._access or self._kv_get is None:
            return
        self._access = await self._kv_get(_KV_ACCESS) or ""
        self._refresh = await self._kv_get(_KV_REFRESH) or ""
        raw_exp = await self._kv_get(_KV_EXPIRES)
        self._expires_at = float(raw_exp) if raw_exp else 0.0

    async def _store_tokens(self, data: dict[str, Any]) -> None:
        self._access = data.get("access_token", "") or ""
        self._refresh = data.get("refresh_token", "") or ""
        expires_in = int(data.get("expires_in") or 0)
        self._expires_at = time.time() + expires_in if expires_in else 0.0
        if self._kv_set is not None:
            await self._kv_set(_KV_ACCESS, self._access)
            await self._kv_set(_KV_REFRESH, self._refresh)
            await self._kv_set(_KV_EXPIRES, str(self._expires_at))

    async def login(self) -> None:
        """POST /v1/auth/login — secret_token bilan yangi juftlik oladi."""
        if not self._secret:
            raise BillzError("BILLZ_SECRET_TOKEN sozlanmagan")
        payload = await self._raw_request(
            "POST", "/v1/auth/login", json_body={"secret_token": self._secret},
            authorized=False,
        )
        await self._store_tokens(_unwrap(payload))
        log.info("Billz: yangi access_token olindi")

    async def refresh_token(self) -> bool:
        """POST /v2/auth/refresh. Muvaffaqiyatsiz bo'lsa False — chaqiruvchi login qiladi."""
        if not self._refresh:
            return False
        try:
            payload = await self._raw_request(
                "POST", "/v2/auth/refresh",
                json_body={"refresh_token": self._refresh},
                headers={"platform-id": self._platform_id},
                authorized=False,
            )
        except BillzError as exc:
            log.warning("Billz: refresh ishlamadi (%s), qayta login qilinadi", exc)
            return False
        await self._store_tokens(_unwrap(payload))
        log.info("Billz: token refresh qilindi")
        return True

    async def _ensure_token(self) -> None:
        await self._load_tokens()
        async with self._auth_lock:
            expired = self._expires_at and time.time() >= self._expires_at - _RENEW_MARGIN_SEC
            if self._access and not expired:
                return
            if self._access and expired and await self.refresh_token():
                return
            await self.login()

    # ───────────────────────── so'rov ─────────────────────────

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Avtorizatsiyalangan GET. 401/429 avtomatik hal qilinadi."""
        await self._ensure_token()
        return await self._request_with_retry("GET", path, params=params)

    async def _request_with_retry(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reauthorized = False
        for attempt in range(self._max_retries + 1):
            try:
                return await self._raw_request(
                    method, path, params=params, json_body=json_body, authorized=True
                )
            except _RetryAfter as exc:
                if attempt >= self._max_retries:
                    raise BillzError(f"{path}: 429 — urinishlar tugadi") from exc
                delay = exc.seconds or (2 ** attempt)
                log.warning("Billz 429 (%s): %.1f s kutiladi", path, delay)
                await asyncio.sleep(delay)
            except _Unauthorized:
                # Bir marta token yangilab ko'ramiz; ikkinchi 401 — haqiqiy muammo
                if reauthorized:
                    raise BillzError(f"{path}: qayta avtorizatsiyadan keyin ham 401")
                reauthorized = True
                self._expires_at = 0.0
                if not await self.refresh_token():
                    await self.login()
            except httpx.TransportError as exc:
                # DNS uzilishi, ulanish rad etilishi, timeout — o'tkinchi nosozliklar.
                # Bularsiz uzoq davom etadigan sinxronizatsiyada qatorlar JIM
                # tushib qoladi (real ishga tushirishda 204 artikuldan 14 tasi).
                if attempt >= self._max_retries:
                    raise BillzError(f"{path}: tarmoq xatosi — {exc}") from exc
                delay = 2 ** attempt
                log.warning("Billz tarmoq xatosi (%s): %.1f s kutiladi — %s",
                            path, delay, exc)
                await asyncio.sleep(delay)
        raise BillzError(f"{path}: so'rov bajarilmadi")

    async def _raw_request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None, authorized: bool = True,
    ) -> dict[str, Any]:
        await self._bucket.acquire()
        request_headers = {"accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if authorized and self._access:
            request_headers["Authorization"] = f"Bearer {self._access}"

        url = f"{self._base}{path}"
        response = await self._http.request(
            method, url, params=params, json=json_body, headers=request_headers
        )
        body = response.text
        if self._raw_sink is not None:
            await self._raw_sink(path, params or {}, response.status_code, body)

        if response.status_code == 429:
            raise _RetryAfter(_retry_after_seconds(response))
        if response.status_code == 401:
            raise _Unauthorized()
        if response.status_code >= 400:
            raise BillzError(f"{path}: HTTP {response.status_code} — {body[:300]}")

        try:
            return json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise BillzError(f"{path}: JSON emas — {body[:200]}") from exc

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()


class _RetryAfter(Exception):
    def __init__(self, seconds: float = 0.0) -> None:
        super().__init__(seconds)
        self.seconds = seconds


class _Unauthorized(Exception):
    pass


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    """Billz javobni {code, message, error, data} ichiga o'raydi.

    Ba'zi endpoint'lar o'ramasdan qaytaradi — ikkala holatni ham qo'llab-quvvatlaymiz.
    """
    if isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload
