"""billz/client.py — auth, rate limit va xato holatlari.

Nega mock transport: haqiqiy Billz'ga urish testlarni sekin, nobarqaror va
rate limit'ga bog'liq qiladi. httpx.MockTransport bizga aynan kerakli
javoblarni (429, 401, buzilgan JSON) bermoqchi bo'lganda qaytaradi.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from povtor_bot.billz.client import BillzClient, BillzError, TokenBucket

LOGIN_OK = {
    "code": 200, "message": "ok", "error": None,
    "data": {
        "access_token": "ACCESS-1", "refresh_token": "REFRESH-1",
        "token_type": "Bearer", "expires_in": 86400,
    },
}
REFRESH_OK = {
    "code": 200,
    "data": {
        "access_token": "ACCESS-2", "refresh_token": "REFRESH-2",
        "token_type": "Bearer", "expires_in": 1209600,
    },
}


class Recorder:
    """So'rovlarni yozib boradigan mock transport."""

    def __init__(self, script: list[httpx.Response]) -> None:
        self.script = script
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.script.pop(0) if self.script else httpx.Response(200, json={})
        return response

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def make_client(script: list[httpx.Response], **kwargs) -> tuple[BillzClient, Recorder]:
    recorder = Recorder(script)
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    client = BillzClient(
        secret_token="SECRET", rate_limit_rps=0, client=http, **kwargs
    )
    return client, recorder


class TestTokenBucket:
    async def test_zero_rate_never_waits(self) -> None:
        bucket = TokenBucket(0)
        await asyncio.wait_for(bucket.acquire(), timeout=0.5)

    async def test_spaces_out_requests(self) -> None:
        """20 rps => ikkinchi so'rov kamida ~50ms kutadi."""
        bucket = TokenBucket(20)
        loop = asyncio.get_running_loop()
        await bucket.acquire()
        start = loop.time()
        await bucket.acquire()
        assert loop.time() - start >= 0.04


class TestAuth:
    async def test_login_then_authorized_get(self) -> None:
        client, rec = make_client([
            httpx.Response(200, json=LOGIN_OK),
            httpx.Response(200, json={"shops": []}),
        ])
        await client.get("/v1/shop")
        assert rec.paths == ["/v1/auth/login", "/v1/shop"]
        assert rec.requests[1].headers["Authorization"] == "Bearer ACCESS-1"
        await client.aclose()

    async def test_login_body_carries_secret(self) -> None:
        client, rec = make_client([
            httpx.Response(200, json=LOGIN_OK), httpx.Response(200, json={}),
        ])
        await client.get("/v1/shop")
        assert json.loads(rec.requests[0].content) == {"secret_token": "SECRET"}
        await client.aclose()

    async def test_missing_secret_raises(self) -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        client = BillzClient(secret_token="", rate_limit_rps=0, client=http)
        with pytest.raises(BillzError, match="BILLZ_SECRET_TOKEN"):
            await client.get("/v1/shop")
        await client.aclose()

    async def test_401_triggers_refresh_then_retries(self) -> None:
        """Token eskirsa — refresh qilinadi va so'rov qaytariladi."""
        client, rec = make_client([
            httpx.Response(200, json=LOGIN_OK),          # dastlabki login
            httpx.Response(401, json={"error": "Token is expired"}),
            httpx.Response(200, json=REFRESH_OK),        # refresh
            httpx.Response(200, json={"shops": [{"id": "1"}]}),
        ])
        payload = await client.get("/v1/shop")
        assert payload == {"shops": [{"id": "1"}]}
        assert rec.paths == ["/v1/auth/login", "/v1/shop", "/v2/auth/refresh", "/v1/shop"]
        assert rec.requests[-1].headers["Authorization"] == "Bearer ACCESS-2"
        await client.aclose()

    async def test_refresh_sends_platform_id_header(self) -> None:
        client, rec = make_client([
            httpx.Response(200, json=LOGIN_OK),
            httpx.Response(401, json={}),
            httpx.Response(200, json=REFRESH_OK),
            httpx.Response(200, json={}),
        ], platform_id="PLATFORM-XYZ")
        await client.get("/v1/shop")
        refresh = rec.requests[2]
        assert refresh.headers["platform-id"] == "PLATFORM-XYZ"
        await client.aclose()

    async def test_failed_refresh_falls_back_to_login(self) -> None:
        client, rec = make_client([
            httpx.Response(200, json=LOGIN_OK),
            httpx.Response(401, json={}),
            httpx.Response(400, json={"error": "bad refresh"}),   # refresh yiqildi
            httpx.Response(200, json=LOGIN_OK),                   # qayta login
            httpx.Response(200, json={"ok": True}),
        ])
        assert await client.get("/v1/shop") == {"ok": True}
        assert rec.paths[-2:] == ["/v1/auth/login", "/v1/shop"]
        await client.aclose()

    async def test_persistent_401_raises(self) -> None:
        client, _ = make_client([
            httpx.Response(200, json=LOGIN_OK),
            httpx.Response(401, json={}),
            httpx.Response(200, json=REFRESH_OK),
            httpx.Response(401, json={}),
        ])
        with pytest.raises(BillzError, match="401"):
            await client.get("/v1/shop")
        await client.aclose()

    async def test_tokens_restored_from_kv_without_login(self) -> None:
        """Bot restart bo'lganda saqlangan token qayta ishlatiladi."""
        import time
        store = {
            "billz_access_token": "SAVED",
            "billz_refresh_token": "SAVED-R",
            "billz_token_expires_at": str(time.time() + 100_000),
        }

        async def kv_get(key: str) -> str | None:
            return store.get(key)

        async def kv_set(key: str, value: str) -> None:
            store[key] = value

        client, rec = make_client(
            [httpx.Response(200, json={"ok": 1})], kv_get=kv_get, kv_set=kv_set
        )
        await client.get("/v1/shop")
        assert rec.paths == ["/v1/shop"]      # login qilinmadi
        assert rec.requests[0].headers["Authorization"] == "Bearer SAVED"
        await client.aclose()

    async def test_expired_kv_token_is_refreshed(self) -> None:
        store = {
            "billz_access_token": "OLD", "billz_refresh_token": "OLD-R",
            "billz_token_expires_at": "1",      # ancha oldin tugagan
        }

        async def kv_get(key: str) -> str | None:
            return store.get(key)

        async def kv_set(key: str, value: str) -> None:
            store[key] = value

        client, rec = make_client([
            httpx.Response(200, json=REFRESH_OK), httpx.Response(200, json={}),
        ], kv_get=kv_get, kv_set=kv_set)
        await client.get("/v1/shop")
        assert rec.paths == ["/v2/auth/refresh", "/v1/shop"]
        assert store["billz_access_token"] == "ACCESS-2"
        await client.aclose()


class TestRateLimitAndErrors:
    async def test_429_is_retried(self, monkeypatch) -> None:
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr("povtor_bot.billz.client.asyncio.sleep", fake_sleep)
        client, rec = make_client([
            httpx.Response(200, json=LOGIN_OK),
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"ok": True}),
        ])
        assert await client.get("/v1/shop") == {"ok": True}
        assert slept == [7.0]
        await client.aclose()

    async def test_429_without_header_backs_off_exponentially(self, monkeypatch) -> None:
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr("povtor_bot.billz.client.asyncio.sleep", fake_sleep)
        client, _ = make_client([
            httpx.Response(200, json=LOGIN_OK),
            httpx.Response(429), httpx.Response(429), httpx.Response(200, json={"ok": 1}),
        ])
        await client.get("/v1/shop")
        assert slept == [1, 2]
        await client.aclose()

    async def test_429_exhausted_raises(self, monkeypatch) -> None:
        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr("povtor_bot.billz.client.asyncio.sleep", fake_sleep)
        client, _ = make_client(
            [httpx.Response(200, json=LOGIN_OK)] + [httpx.Response(429)] * 5
        )
        with pytest.raises(BillzError, match="429"):
            await client.get("/v1/shop")
        await client.aclose()

    async def test_server_error_raises_with_body(self) -> None:
        client, _ = make_client([
            httpx.Response(200, json=LOGIN_OK),
            httpx.Response(500, text="internal boom"),
        ])
        with pytest.raises(BillzError, match="internal boom"):
            await client.get("/v1/shop")
        await client.aclose()

    async def test_invalid_json_raises(self) -> None:
        client, _ = make_client([
            httpx.Response(200, json=LOGIN_OK),
            httpx.Response(200, text="<html>not json</html>"),
        ])
        with pytest.raises(BillzError, match="JSON emas"):
            await client.get("/v1/shop")
        await client.aclose()

    async def test_raw_sink_records_every_call(self) -> None:
        """Xom javoblar debug uchun saqlanadi."""
        captured: list[tuple[str, int]] = []

        async def sink(path: str, params: dict, status: int, body: str) -> None:
            captured.append((path, status))

        client, _ = make_client([
            httpx.Response(200, json=LOGIN_OK), httpx.Response(200, json={}),
        ], raw_sink=sink)
        await client.get("/v1/shop")
        assert captured == [("/v1/auth/login", 200), ("/v1/shop", 200)]
        await client.aclose()


class TestNetworkErrors:
    """O'tkinchi tarmoq nosozliklari qayta urinilishi kerak.

    Sabab: 200 ta artikulni ketma-ket o'qiyotganda bitta DNS uzilishi
    qatorni JIM tushirib qoldiradi — real ishga tushirishda 204 dan 14 tasi
    aynan shu sababdan yo'qolgan edi.
    """

    def _flaky(self, failures: int, script: list[httpx.Response]):
        state = {"left": failures}
        recorder = Recorder(script)

        def transport(request: httpx.Request) -> httpx.Response:
            if state["left"] > 0 and request.url.path != "/v1/auth/login":
                state["left"] -= 1
                raise httpx.ConnectError("nodename nor servname provided")
            return recorder(request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        return BillzClient(secret_token="S", rate_limit_rps=0, client=http), recorder

    async def test_connect_error_is_retried(self, monkeypatch) -> None:
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr("povtor_bot.billz.client.asyncio.sleep", fake_sleep)
        client, _ = self._flaky(2, [
            httpx.Response(200, json=LOGIN_OK), httpx.Response(200, json={"ok": True}),
        ])
        assert await client.get("/v1/shop") == {"ok": True}
        assert slept == [1, 2]
        await client.aclose()

    async def test_persistent_network_failure_raises_clear_error(self, monkeypatch) -> None:
        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr("povtor_bot.billz.client.asyncio.sleep", fake_sleep)
        client, _ = self._flaky(99, [httpx.Response(200, json=LOGIN_OK)])
        with pytest.raises(BillzError, match="tarmoq xatosi"):
            await client.get("/v1/shop")
        await client.aclose()

    async def test_timeout_is_retried_too(self, monkeypatch) -> None:
        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr("povtor_bot.billz.client.asyncio.sleep", fake_sleep)
        state = {"left": 1}
        recorder = Recorder([
            httpx.Response(200, json=LOGIN_OK), httpx.Response(200, json={"ok": 1}),
        ])

        def transport(request: httpx.Request) -> httpx.Response:
            if state["left"] > 0 and request.url.path != "/v1/auth/login":
                state["left"] -= 1
                raise httpx.ReadTimeout("timed out")
            return recorder(request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        client = BillzClient(secret_token="S", rate_limit_rps=0, client=http)
        assert await client.get("/v1/shop") == {"ok": 1}
        await client.aclose()
