"""NetworkRecorder against a real headless Chrome: which POSTs it sees and from where."""

import asyncio
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest
from browser_use.browser import BrowserSession

from fill import NetworkRecorder
from tests.test_dry_run_guard_chrome import (  # noqa: F401 (fixtures)
    CLICK_SUBMIT,
    SETTLE_S,
    RecordingHTTPServer,
    TestGuardInRealChrome as GuardTests,
    browser,
    server,
)


class TestSubmitPage:
    # A form submit is a Document request whose documentURL is the POST target
    # (/apply), not the page that held the form (/).
    async def test_form_submit_is_tied_to_the_page_that_held_the_form(
        self,
        server: RecordingHTTPServer,  # noqa: F811
        browser: BrowserSession,  # noqa: F811
        mock_app_logger: MagicMock,
    ) -> None:
        recorder = NetworkRecorder("acme", mock_app_logger)
        await recorder.attach_if_needed(browser)
        await browser.navigate_to(server.url())
        await GuardTests().run_js(browser, CLICK_SUBMIT)
        await asyncio.sleep(SETTLE_S)

        submits = [(post["url"], post["page_url"]) for post in recorder.recent]
        assert submits == [(server.url("apply"), server.url())]


HTTP_OK = 200
FRAME_POSTS = ("/frame/api", "/frame/submit")
FRAME_WAIT_S = 30.0
POLL_S = 0.25
# The iframe sends its POSTs by itself, 0.3 s and 0.9 s after it loads.
FRAME_PAGE = b"""<html><body><form id="f" method="post" action="/frame/submit">
<input name="email" value="x"></form><script>
setTimeout(() => fetch("/frame/api", {method: "POST", body: "a=1"}), 300);
setTimeout(() => document.getElementById("f").submit(), 900);
</script></body></html>"""


class CrossSiteServer(ThreadingHTTPServer):
    """Serves a page on 127.0.0.1 whose iframe loads from localhost: another site,
    so Chrome runs the iframe in its own process (how sites embed Greenhouse)."""

    posts: list[str]

    def url(self, host: str, path: str = "") -> str:
        return f"http://{host}:{self.server_port}/{path}"

    async def wait_for_posts(self, count: int) -> None:
        # The iframe on localhost loaded ~11 s after the page in a test run.
        for _ in range(int(FRAME_WAIT_S / POLL_S)):
            if len(self.posts) >= count:
                return
            await asyncio.sleep(POLL_S)


class CrossSiteHandler(BaseHTTPRequestHandler):
    server: CrossSiteServer

    def do_GET(self) -> None:
        frame_src = self.server.url("localhost", "frame")
        parent = f'<html><body><iframe src="{frame_src}"></iframe></body></html>'
        page = FRAME_PAGE if self.path == "/frame" else parent.encode()
        self.send_response(HTTP_OK)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:
        self.server.posts.append(self.path)
        self.send_response(HTTP_OK)
        self.end_headers()


@pytest.fixture
def cross_site() -> Iterator[CrossSiteServer]:
    httpd = CrossSiteServer(("127.0.0.1", 0), CrossSiteHandler)
    httpd.posts = []
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


class TestCrossSiteIframe:
    async def test_posts_from_a_cross_site_iframe_are_recorded(
        self,
        cross_site: CrossSiteServer,
        browser: BrowserSession,  # noqa: F811
        mock_app_logger: MagicMock,
    ) -> None:
        recorder = NetworkRecorder("acme", mock_app_logger)
        await recorder.attach_if_needed(browser)
        await browser.navigate_to(cross_site.url("127.0.0.1"))
        await cross_site.wait_for_posts(len(FRAME_POSTS))
        await asyncio.sleep(SETTLE_S)

        frame = cross_site.url("localhost", "frame")
        seen = [(post["url"], post["page_url"]) for post in recorder.recent]
        assert cross_site.posts == list(FRAME_POSTS)
        assert seen == [
            (cross_site.url("localhost", "frame/api"), frame),
            (cross_site.url("localhost", "frame/submit"), frame),
        ]
