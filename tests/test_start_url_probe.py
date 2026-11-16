"""StartUrlProbe: skip a portal whose start page is gone before a browser opens."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from fill import Orchestrator, PortalUnreachable, StartUrlProbe
from tests.test_orchestrator import TestProxyProviders

START = "https://jobs.example.com/remote"
OK = 200
NOT_FOUND = 404


class RecordingTransport(httpx.AsyncBaseTransport):
    """Answers every request with one status (or raises) and keeps the URLs."""

    def __init__(self, status: int = OK, error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.urls: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status, request=request)


class TestStartUrlProbe:
    @pytest.mark.parametrize("status", [404, 410])
    async def test_gone_page_is_unreachable(self, status: int) -> None:
        probe = StartUrlProbe(RecordingTransport(status))
        with pytest.raises(PortalUnreachable, match=f"{START}: HTTP {status}"):
            await probe.check({"name": "x", "url": START})

    async def test_no_answer_is_unreachable(self) -> None:
        probe = StartUrlProbe(RecordingTransport(error=httpx.ConnectError("refused")))
        with pytest.raises(PortalUnreachable, match=f"{START}: ConnectError: refused"):
            await probe.check({"name": "x", "url": START})

    # On 2026-11-15 curl got 403 from Cloudflare boards and 429 from golang.cafe and
    # rustjobs.dev, and the browser still filled forms on golang.cafe and rustjobs.dev.
    @pytest.mark.parametrize("status", [200, 403, 429, 500])
    async def test_other_answers_let_the_browser_try(self, status: int) -> None:
        await StartUrlProbe(RecordingTransport(status)).check(
            {"name": "x", "url": START}
        )

    async def test_checks_the_search_url_when_set(self) -> None:
        transport = RecordingTransport(OK)
        await StartUrlProbe(transport).check(
            {"name": "x", "url": "https://example.com/", "search_url": START}
        )
        assert transport.urls == [START]

    # hackajob's start page answers 404 to a logged-out GET but works in the
    # browser profile, which is logged in.
    async def test_portal_with_login_is_not_checked(self) -> None:
        transport = RecordingTransport(NOT_FOUND)
        await StartUrlProbe(transport).check(
            {"name": "x", "url": START, "credentials": {"username": "$U"}}
        )
        assert transport.urls == []


class FakeProbe:
    def __init__(self, unreachable: set[str]) -> None:
        self.unreachable = unreachable

    async def check(self, system: dict) -> None:
        if system["name"] in self.unreachable:
            raise PortalUnreachable(f"{system['name']}: HTTP 404")


class TestOrchestratorSkipsUnreachable:
    def _orchestrator(self, names: list[str]) -> Orchestrator:
        orch = TestProxyProviders._orchestrator(
            [{"name": name, "url": f"https://{name}.example.com/"} for name in names]
        )
        orch.url_probe = FakeProbe({"gone"})
        return orch

    async def test_no_browser_and_no_retry_for_a_gone_portal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = self._orchestrator(["gone", "fine"])
        built: list[str] = []

        def make_runner(system: dict) -> MagicMock:
            built.append(system["name"])
            return MagicMock(run=AsyncMock(return_value="applied"))

        monkeypatch.setattr(orch, "make_runner", make_runner)

        await orch.run()

        run_done = next(
            call.kwargs
            for call in orch.app_logger.emit.call_args_list
            if call.args and call.args[0] == "run_done"
        )
        assert (built, run_done["failed"]) == (["fine"], ["gone"])

    async def test_single_portal_run_is_checked_too(self) -> None:
        orch = self._orchestrator(["gone"])
        with pytest.raises(PortalUnreachable):
            await orch.run_portal(orch.active[0])
