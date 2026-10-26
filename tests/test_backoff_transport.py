"""BackoffTransport — retry on 429/5xx, honour Retry-After, give up after max_retries."""

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from fill import BackoffTransport


def _response(status: int, retry_after: str | None = None) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after else {}
    return httpx.Response(status_code=status, headers=headers)


def _request() -> httpx.Request:
    return httpx.Request("GET", "https://api.example.com/x")


class TestComputeWait:
    def test_uses_retry_after_when_present(self):
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        t = BackoffTransport(wrapped, base_wait=2.0, max_wait=60.0)
        assert t._compute_wait("7", attempt=0) == 7.0

    def test_exponential_backoff_when_no_header(self):
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        t = BackoffTransport(wrapped, base_wait=2.0, max_wait=60.0)
        assert t._compute_wait(None, attempt=0) == 2.0
        assert t._compute_wait(None, attempt=1) == 4.0
        assert t._compute_wait(None, attempt=3) == 16.0

    def test_caps_at_max_wait(self):
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        t = BackoffTransport(wrapped, base_wait=10.0, max_wait=20.0)
        assert t._compute_wait(None, attempt=10) == 20.0

    def test_invalid_retry_after_falls_back_to_backoff(self):
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        t = BackoffTransport(wrapped, base_wait=2.0)
        assert t._compute_wait("not-a-number", attempt=0) == 2.0


class TestRetryBudget:
    def test_retry_after_is_capped_at_max_wait(self):
        t = BackoffTransport(MagicMock(spec=httpx.AsyncBaseTransport), max_wait=60.0)
        assert t._compute_wait("3600", attempt=0) == 60.0

    @pytest.mark.asyncio
    async def test_gives_up_instead_of_sleeping_past_the_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        wrapped.handle_async_request = AsyncMock(return_value=_response(429))
        sleep_mock = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", sleep_mock)

        t = BackoffTransport(
            wrapped,
            max_retries=5,
            base_wait=2.0,
            max_total_wait=45.0,
            log=logging.getLogger("t"),
        )
        result = await t.handle_async_request(_request())

        assert result.status_code == 429
        assert [c.args[0] for c in sleep_mock.call_args_list] == [2.0, 4.0, 8.0, 16.0]

    @pytest.mark.asyncio
    async def test_closes_each_discarded_response(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        first, second = _response(503), _response(200)
        first.aclose = AsyncMock()
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        wrapped.handle_async_request = AsyncMock(side_effect=[first, second])
        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        result = await BackoffTransport(wrapped, base_wait=0.01).handle_async_request(
            _request()
        )

        assert result is second
        first.aclose.assert_awaited_once()


class TestHandleAsyncRequest:
    @pytest.mark.asyncio
    async def test_passes_through_200(self, monkeypatch: pytest.MonkeyPatch):
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        wrapped.handle_async_request = AsyncMock(return_value=_response(200))
        sleep_mock = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", sleep_mock)

        t = BackoffTransport(wrapped, max_retries=3, log=logging.getLogger("t"))
        result = await t.handle_async_request(_request())
        assert result.status_code == 200
        assert wrapped.handle_async_request.call_count == 1
        sleep_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_on_429(self, monkeypatch: pytest.MonkeyPatch):
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        wrapped.handle_async_request = AsyncMock(
            side_effect=[_response(429), _response(429), _response(200)]
        )
        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        t = BackoffTransport(
            wrapped, max_retries=5, base_wait=0.01, log=logging.getLogger("t")
        )
        result = await t.handle_async_request(_request())
        assert result.status_code == 200
        assert wrapped.handle_async_request.call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_503(self, monkeypatch: pytest.MonkeyPatch):
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        wrapped.handle_async_request = AsyncMock(
            side_effect=[_response(503), _response(200)]
        )
        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        t = BackoffTransport(
            wrapped, max_retries=3, base_wait=0.01, log=logging.getLogger("t")
        )
        result = await t.handle_async_request(_request())
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self, monkeypatch: pytest.MonkeyPatch):
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        wrapped.handle_async_request = AsyncMock(return_value=_response(429))
        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        t = BackoffTransport(
            wrapped, max_retries=2, base_wait=0.01, log=logging.getLogger("t")
        )
        result = await t.handle_async_request(_request())
        assert result.status_code == 429
        assert wrapped.handle_async_request.call_count == 3  # initial + 2 retries

    @pytest.mark.asyncio
    async def test_does_not_retry_on_400(self, monkeypatch: pytest.MonkeyPatch):
        wrapped = MagicMock(spec=httpx.AsyncBaseTransport)
        wrapped.handle_async_request = AsyncMock(return_value=_response(400))
        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        t = BackoffTransport(wrapped, max_retries=5, log=logging.getLogger("t"))
        result = await t.handle_async_request(_request())
        assert result.status_code == 400
        assert wrapped.handle_async_request.call_count == 1
