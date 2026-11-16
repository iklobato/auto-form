"""StepCallback — log line, screenshot persistence, action-name extraction."""

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from browser_use.browser.views import BrowserStateSummary
from browser_use.tools.service import Tools

from fill import DryRunGuard, Paths, ShotPhase, StepCallback, StepShots


def _png_b64() -> str:
    """1×1 transparent PNG, base64-encoded."""
    raw = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return base64.b64encode(raw).decode()


@pytest.fixture
def run_shots(tmp_app_root: Path) -> StepShots:
    return StepShots(Paths.SCREENSHOTS / "acme" / "test-run")


class TestStepShots:
    def test_a_retried_portal_gets_its_own_folder(self, tmp_app_root: Path) -> None:
        base = Paths.SCREENSHOTS / "acme" / "test-run"
        first = StepShots(base).save(1, ShotPhase.BEFORE, b"first")
        retry = StepShots(base).save(1, ShotPhase.BEFORE, b"retry")

        assert first.read_bytes() == b"first"
        assert retry == base.with_name("test-run-2") / "step0001_before.png"


class TestActionNameExtraction:
    """Action names as logged in the `step` event."""

    async def _logged_actions(self, mock_app_logger: MagicMock, action: object) -> list:
        state = MagicMock(
            spec=BrowserStateSummary, url="https://acme/x", screenshot=None
        )
        output = MagicMock(action=action, next_goal="", evaluation_previous_goal="")
        await StepCallback("acme", mock_app_logger).on_step(state, output, step_num=1)
        return mock_app_logger.emit.call_args.kwargs["actions"]

    @pytest.mark.asyncio
    async def test_extracts_from_pydantic_actions(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ) -> None:
        action = MagicMock()
        action.model_dump.return_value = {"click": {"index": 5}}
        assert await self._logged_actions(mock_app_logger, [action]) == ["click"]

    @pytest.mark.asyncio
    async def test_dict_actions_skip_none_values(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ) -> None:
        action = {"click": {"index": 1}, "scroll": None}
        assert await self._logged_actions(mock_app_logger, [action]) == ["click"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", [[], None])
    async def test_no_actions(
        self, action: list | None, tmp_app_root: Path, mock_app_logger: MagicMock
    ) -> None:
        assert await self._logged_actions(mock_app_logger, action) == []


class TestOnStepFailureIsolation:
    @pytest.mark.asyncio
    async def test_cdp_attach_failure_does_not_reach_the_agent(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        recorder = MagicMock()
        recorder.attach_if_needed = AsyncMock(
            side_effect=ValueError("No valid agent focus available")
        )
        cb = StepCallback("acme", mock_app_logger, network_recorder=recorder)
        cb.agent_ref = MagicMock()
        state = MagicMock(spec=BrowserStateSummary)
        state.url = "https://acme/jobs"
        state.screenshot = None

        await cb.on_step(state, MagicMock(action=[]), step_num=3)

        errors = [
            c.kwargs
            for c in mock_app_logger.emit.call_args_list
            if c.args and c.args[0] == "step_callback_error"
        ]
        assert errors == [
            {
                "portal": "acme",
                "step": 3,
                "error_type": "ValueError",
                "error": "No valid agent focus available",
            }
        ]


class TestOnStep:
    @pytest.mark.asyncio
    async def test_saves_screenshot_when_present(
        self, tmp_app_root: Path, mock_app_logger: MagicMock, run_shots: StepShots
    ):
        cb = StepCallback("acme", mock_app_logger, shots=run_shots)
        state = MagicMock(spec=BrowserStateSummary)
        state.url = "https://acme/jobs"
        state.screenshot = _png_b64()
        agent_output = MagicMock(
            action=[], next_goal="apply", evaluation_previous_goal=""
        )

        await cb.on_step(state, agent_output, step_num=1)

        run_dir = Paths.SCREENSHOTS / "acme" / "test-run"
        assert [shot.name for shot in run_dir.iterdir()] == ["step0001_before.png"]
        assert (run_dir / "step0001_before.png").read_bytes().startswith(b"\x89PNG")

    @pytest.mark.asyncio
    async def test_no_screenshot_no_file(
        self, tmp_app_root: Path, mock_app_logger: MagicMock, run_shots: StepShots
    ):
        cb = StepCallback("acme", mock_app_logger, shots=run_shots)
        state = MagicMock(spec=BrowserStateSummary)
        state.url = "https://acme/jobs"
        state.screenshot = None
        await cb.on_step(
            state,
            MagicMock(action=[], next_goal="", evaluation_previous_goal=""),
            step_num=1,
        )
        assert list((Paths.SCREENSHOTS / "acme").glob("*.png")) == []

    @pytest.mark.asyncio
    async def test_emits_step_event(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        cb = StepCallback("acme", mock_app_logger)
        state = MagicMock(spec=BrowserStateSummary)
        state.url = "https://acme/x"
        state.screenshot = None
        await cb.on_step(
            state,
            MagicMock(action=[], next_goal="g", evaluation_previous_goal=""),
            step_num=42,
        )
        mock_app_logger.emit.assert_called_once()
        kwargs = mock_app_logger.emit.call_args.kwargs
        assert kwargs["portal"] == "acme"
        assert kwargs["step"] == 42
        assert kwargs["url"] == "https://acme/x"

    @pytest.mark.asyncio
    async def test_unknown_url_default(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        cb = StepCallback("acme", mock_app_logger)
        state = MagicMock(spec=BrowserStateSummary)
        state.url = None
        state.screenshot = None
        await cb.on_step(
            state,
            MagicMock(action=[], next_goal="", evaluation_previous_goal=""),
            step_num=1,
        )
        assert mock_app_logger.emit.call_args.kwargs["url"] == "unknown"


class FakeScreenshotSession:
    """Stands in for the CDP session behind a screenshot (Page.captureScreenshot)."""

    session_id = "s1"

    def __init__(self, reply: dict, delay_s: float = 0.0) -> None:
        self.reply = reply
        self.delay_s = delay_s

    @property
    def cdp_client(self) -> "FakeScreenshotSession":
        return self

    @property
    def send(self) -> "FakeScreenshotSession":
        return self

    @property
    def Page(self) -> "FakeScreenshotSession":  # noqa: N802 - CDP domain name
        return self

    async def get_or_create_cdp_session(self) -> "FakeScreenshotSession":
        return self

    async def captureScreenshot(  # noqa: N802 - CDP method name
        self, params: dict, session_id: str
    ) -> dict:
        await asyncio.sleep(self.delay_s)
        return self.reply


class TestAfterStepScreenshot:
    """on_step_end saves what the page looks like after the step's actions ran."""

    # browser_use bumps n_steps before on_step_end, so step 1 has ended at 2
    STEPS_AFTER_FIRST = 2
    # longer than the patched limit below, so the capture never answers in time
    HANG_S = 5.0
    SHORT_LIMIT_S = 0.05

    def _agent(self, session: FakeScreenshotSession | None) -> MagicMock:
        return MagicMock(
            browser_session=session, state=MagicMock(n_steps=self.STEPS_AFTER_FIRST)
        )

    @pytest.mark.asyncio
    async def test_saves_after_shot_next_to_before_shot(
        self, tmp_app_root: Path, mock_app_logger: MagicMock, run_shots: StepShots
    ) -> None:
        agent = self._agent(FakeScreenshotSession({"data": _png_b64()}))

        await StepCallback("acme", mock_app_logger, shots=run_shots).on_step_end(agent)

        shot = Paths.SCREENSHOTS / "acme" / "test-run" / "step0001_after.png"
        assert shot.read_bytes() == base64.b64decode(_png_b64())

    @pytest.mark.asyncio
    async def test_a_capture_that_never_answers_is_cut_off(
        self,
        mock_app_logger: MagicMock,
        run_shots: StepShots,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # browser_use awaits on_step_end outside its step timeout
        monkeypatch.setattr(StepShots, "TIMEOUT_S", self.SHORT_LIMIT_S)
        agent = self._agent(FakeScreenshotSession({}, delay_s=self.HANG_S))

        await StepCallback("acme", mock_app_logger, shots=run_shots).on_step_end(agent)

        event = mock_app_logger.emit.call_args
        assert event.args == ("step_callback_error",)
        assert (event.kwargs["error_type"], event.kwargs["step"]) == ("TimeoutError", 1)

    @pytest.mark.asyncio
    async def test_a_reply_without_data_is_reported_not_raised(
        self, tmp_app_root: Path, mock_app_logger: MagicMock, run_shots: StepShots
    ) -> None:
        agent = self._agent(FakeScreenshotSession({}))

        await StepCallback("acme", mock_app_logger, shots=run_shots).on_step_end(agent)

        event = mock_app_logger.emit.call_args
        assert event.kwargs["error"] == "the browser returned no screenshot data"
        assert list(Paths.SCREENSHOTS.rglob("*.png")) == []

    @pytest.mark.asyncio
    async def test_no_browser_session_no_file(
        self, tmp_app_root: Path, mock_app_logger: MagicMock, run_shots: StepShots
    ) -> None:
        await StepCallback("acme", mock_app_logger, shots=run_shots).on_step_end(
            self._agent(None)
        )

        assert list(Paths.SCREENSHOTS.rglob("*.png")) == []


class TestInputAlwaysClears:
    """flash-lite sent clear: False when it typed into a field again (2026-10-30)."""

    FIELD_INDEX = 3
    BUTTON_INDEX = 4
    PHONE = "555-0100"

    def _actions(self, raw_actions: list[dict]) -> list:
        action_model = Tools().registry.create_action_model()
        return [action_model.model_validate(raw) for raw in raw_actions]

    @pytest.mark.asyncio
    async def test_append_input_is_turned_into_clear(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ) -> None:
        typed = {"index": self.FIELD_INDEX, "text": self.PHONE}
        click = {"click": {"index": self.BUTTON_INDEX}}
        actions = self._actions([{"input": {**typed, "clear": False}}, click])
        state = MagicMock(
            spec=BrowserStateSummary, url="https://acme/x", screenshot=None
        )
        output = MagicMock(action=actions, next_goal="", evaluation_previous_goal="")

        await StepCallback("acme", mock_app_logger).on_step(state, output, step_num=1)

        assert [action.model_dump(exclude_unset=True) for action in actions] == [
            {"input": {**typed, "clear": True}},
            click,
        ]


class TestDryRunGuard:
    @staticmethod
    def _attached_guard(mock_app_logger: MagicMock) -> tuple[DryRunGuard, MagicMock]:
        client = MagicMock()
        client.send.Fetch.failRequest = AsyncMock()
        client.send.Fetch.continueRequest = AsyncMock()
        guard = DryRunGuard("acme", mock_app_logger)
        guard._client = client
        return guard, client

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method, url, blocked",
        [
            ("POST", "https://boards.greenhouse.io/acme/jobs/1/submit", True),
            ("PUT", "https://acme.breezy.hr/api/apply", True),
            ("GET", "https://acme.breezy.hr/p/123", False),
            ("POST", "https://www.google-analytics.com/collect", False),
            (
                "POST",
                "https://c.spl.greenhouse.io/com.snowplowanalytics.snowplow/tp2",
                False,
            ),
        ],
    )
    async def test_fails_only_real_writes(
        self,
        method: str,
        url: str,
        blocked: bool,
        tmp_app_root: Path,
        mock_app_logger: MagicMock,
    ):
        guard, client = self._attached_guard(mock_app_logger)

        guard._on_paused({"requestId": "f1", "request": {"method": method, "url": url}})
        await asyncio.gather(*guard._pending)

        if blocked:
            client.send.Fetch.failRequest.assert_awaited_once_with(
                params={"requestId": "f1", "errorReason": "BlockedByClient"},
                session_id=None,
            )
            client.send.Fetch.continueRequest.assert_not_called()
        else:
            client.send.Fetch.continueRequest.assert_awaited_once_with(
                params={"requestId": "f1"}, session_id=None
            )
            client.send.Fetch.failRequest.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "body, blocked",
        [
            # What Ashby sends to render the application page (seen 2026-10-26)
            (
                {
                    "operationName": "ApiJobPosting",
                    "query": "query ApiJobPosting($id: String!) { x }",
                },
                False,
            ),
            ([{"query": "query A { a }"}, {"query": "{ b }"}], False),
            ({"operationName": "Submit", "query": "mutation Submit { submit }"}, True),
            # A document that starts with a query but runs a named mutation
            ({"operationName": "B", "query": "query A { a } mutation B { b }"}, True),
            ([{"query": "query A { a }"}, {"query": "mutation B { b }"}], True),
            (
                {"operationName": "Persisted", "extensions": {"persistedQuery": {}}},
                True,
            ),
            ("not json", True),
        ],
        ids=[
            "ashby-read",
            "batch-reads",
            "mutation",
            "query-then-mutation",
            "batch-with-mutation",
            "no-query-text",
            "unparsable",
        ],
    )
    async def test_graphql_reads_pass_writes_stay_blocked(
        self, body, blocked: bool, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        guard, client = self._attached_guard(mock_app_logger)
        post_data = body if isinstance(body, str) else json.dumps(body)

        guard._on_paused(
            {
                "requestId": "g1",
                "request": {
                    "method": "POST",
                    "url": "https://jobs.ashbyhq.com/api/non-user-graphql",
                    "postData": post_data,
                },
            }
        )
        await asyncio.gather(*guard._pending)

        assert client.send.Fetch.failRequest.await_count == (1 if blocked else 0)
        assert client.send.Fetch.continueRequest.await_count == (0 if blocked else 1)

    @pytest.mark.asyncio
    async def test_attach_failure_reaches_the_agent(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        # A dry run must never act unguarded: browser_use drops the step's actions
        # when the callback raises, so the error must not be swallowed.
        guard = DryRunGuard("acme", mock_app_logger)
        cb = StepCallback("acme", mock_app_logger, dry_run_guard=guard)
        cb.agent_ref = MagicMock(browser_session=None)
        state = MagicMock(spec=BrowserStateSummary)
        state.url = "https://acme/jobs"
        state.screenshot = None

        with pytest.raises(RuntimeError, match="dry-run guard"):
            await cb.on_step(state, MagicMock(action=[]), step_num=1)
