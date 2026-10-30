"""NetworkRecorder against a real headless Chrome: which page sent a form submit."""

import asyncio
from unittest.mock import MagicMock

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
