"""DryRunGuard against a real headless Chrome: a submit must never reach the server."""

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from browser_use.browser import BrowserSession
from browser_use.browser.profile import BrowserProfile

from fill import DryRunGuard

FORM_PAGE = b"""<html><body>
<form method="post" action="/apply"><input name="email" value="x">
<button id="submit" type="submit">Submit application</button></form>
</body></html>"""
HTTP_OK = 200
# Long enough for Chrome to send the request and the server thread to record it.
SETTLE_S = 1.5
CLICK_SUBMIT = "document.getElementById('submit').click()"
FETCH_POST = "fetch('/api/submit', {method: 'POST'}).catch(() => 0)"


class RecordingHTTPServer(HTTPServer):
    """HTTPServer whose FormHandler appends each POST path to `posts`."""

    posts: list[str]

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.server_port}/{path}"


class FormHandler(BaseHTTPRequestHandler):
    server: RecordingHTTPServer

    def do_GET(self) -> None:
        self.send_response(HTTP_OK)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(FORM_PAGE)

    def do_POST(self) -> None:
        self.server.posts.append(self.path)
        self.send_response(HTTP_OK)
        self.end_headers()


@pytest.fixture
def server() -> Iterator[RecordingHTTPServer]:
    httpd = RecordingHTTPServer(("127.0.0.1", 0), FormHandler)
    httpd.posts = []
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
async def browser(tmp_path: Path) -> AsyncIterator[BrowserSession]:
    profile = BrowserProfile(
        headless=True, user_data_dir=str(tmp_path / "profile"), keep_alive=False
    )
    session = BrowserSession(browser_profile=profile)
    await session.start()
    yield session
    await session.kill()


class TestGuardInRealChrome:
    async def run_js(self, session: BrowserSession, expression: str) -> None:
        cdp = await session.get_or_create_cdp_session()
        await cdp.cdp_client.send.Runtime.evaluate(
            params={"expression": expression, "awaitPromise": True},
            session_id=cdp.session_id,
        )

    async def test_server_gets_the_submit_without_the_guard(
        self, server: RecordingHTTPServer, browser: BrowserSession
    ) -> None:
        await browser.navigate_to(server.url())
        await self.run_js(browser, CLICK_SUBMIT)
        await asyncio.sleep(SETTLE_S)

        assert server.posts == ["/apply"]

    async def test_fetch_post_and_submit_click_are_blocked(
        self,
        server: RecordingHTTPServer,
        browser: BrowserSession,
        mock_app_logger: MagicMock,
    ) -> None:
        guard = DryRunGuard("acme", mock_app_logger)
        await guard.attach_if_needed(browser)
        await browser.navigate_to(server.url())
        await self.run_js(browser, FETCH_POST)
        await self.run_js(browser, CLICK_SUBMIT)
        await asyncio.sleep(SETTLE_S)

        assert server.posts == []
        assert guard.blocked == [server.url("api/submit"), server.url("apply")]
