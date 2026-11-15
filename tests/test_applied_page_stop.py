"""AppliedPageStop: no form actions on a job the trackers already hold."""

from unittest.mock import MagicMock

import pytest
from browser_use.browser.views import BrowserStateSummary
from browser_use.tools.service import Tools

from fill import AppliedPageStop, JsonlTracker, StepCallback, UnmarkedSubmits

JOB = "https://jobs.acme.example/1001"
OTHER_JOB = "https://jobs.acme.example/2002"


@pytest.fixture
def stop(
    applied_tracker: JsonlTracker,
    attempted_tracker: JsonlTracker,
    mock_app_logger: MagicMock,
) -> AppliedPageStop:
    return AppliedPageStop(
        applied_tracker, attempted_tracker, mock_app_logger.portal("acme")
    )


class TestAppliedPageStop:
    FIELD_INDEX = 3
    BUTTON_INDEX = 4
    GO_BACK = {"go_back": {}}
    FILL = {"input": {"index": FIELD_INDEX, "text": "x", "clear": True}}
    SEND = {"click": {"index": BUTTON_INDEX}}
    # what the model asks for in this step; a test may replace it
    raw_actions = (FILL, SEND, GO_BACK)

    def _actions(self) -> list:
        action_model = Tools().registry.create_action_model()
        return [action_model.model_validate(raw) for raw in self.raw_actions]

    def _agent(self) -> MagicMock:
        agent = MagicMock()
        agent._message_manager.state.agent_history_items = []
        return agent

    async def _step_on(
        self, url: str, stop: AppliedPageStop, mock_app_logger: MagicMock
    ) -> tuple[list, list]:
        actions = self._actions()
        callback = StepCallback("acme", mock_app_logger, applied_stop=stop)
        callback.agent_ref = self._agent()
        state = MagicMock(spec=BrowserStateSummary, url=url, screenshot=None)
        output = MagicMock(action=actions, next_goal="", evaluation_previous_goal="")
        await callback.on_step(state, output, step_num=1)
        notes = callback.agent_ref._message_manager.state.agent_history_items
        return actions, notes

    @pytest.mark.asyncio
    async def test_applied_job_keeps_only_navigation(
        self,
        stop: AppliedPageStop,
        applied_tracker: JsonlTracker,
        mock_app_logger: MagicMock,
    ) -> None:
        await applied_tracker.append({"job_url": JOB})

        actions, notes = await self._step_on(JOB + "/", stop, mock_app_logger)

        kept = [action.model_dump(exclude_unset=True) for action in actions]
        assert kept == [self.GO_BACK]
        assert JOB in notes[0].system_message

    @pytest.mark.asyncio
    async def test_attempted_job_is_stopped_too(
        self,
        stop: AppliedPageStop,
        attempted_tracker: JsonlTracker,
        mock_app_logger: MagicMock,
    ) -> None:
        await attempted_tracker.append({"job_url": JOB})

        actions, _ = await self._step_on(JOB, stop, mock_app_logger)

        kept = [action.model_dump(exclude_unset=True) for action in actions]
        assert kept == [self.GO_BACK]

    @pytest.mark.asyncio
    async def test_a_step_with_only_form_actions_goes_back(
        self,
        stop: AppliedPageStop,
        applied_tracker: JsonlTracker,
        mock_app_logger: MagicMock,
    ) -> None:
        # browser_use does not count a step whose action list is empty, so the step
        # number repeats and the after-step screenshot gets the previous number
        await applied_tracker.append({"job_url": JOB})
        self.raw_actions = (self.FILL, self.SEND)

        actions, _ = await self._step_on(JOB, stop, mock_app_logger)

        kept = [action.model_dump(exclude_unset=True) for action in actions]
        assert kept == [self.GO_BACK]

    @pytest.mark.asyncio
    async def test_guessed_unmarked_submit_does_not_lock_the_page(
        self,
        stop: AppliedPageStop,
        attempted_tracker: JsonlTracker,
        mock_app_logger: MagicMock,
    ) -> None:
        # UnmarkedSubmits records any page that POSTed to its own host, which can be a
        # listing (Ashby loads its board with same-host GraphQL POSTs)
        await attempted_tracker.append(
            {"job_url": JOB, "reason": UnmarkedSubmits.REASON}
        )

        actions, notes = await self._step_on(JOB, stop, mock_app_logger)

        assert len(actions) == len(self._actions())
        assert notes == []

    @pytest.mark.asyncio
    async def test_new_job_is_left_alone(
        self,
        stop: AppliedPageStop,
        applied_tracker: JsonlTracker,
        mock_app_logger: MagicMock,
    ) -> None:
        await applied_tracker.append({"job_url": JOB})

        actions, notes = await self._step_on(OTHER_JOB, stop, mock_app_logger)

        assert len(actions) == len(self._actions())
        assert notes == []
