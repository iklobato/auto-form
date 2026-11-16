"""LoneDone: tell the agent when browser_use drops a `done` that was not alone.

browser_use runs `done` only as the first action of a step (agent/service.py,
"Done action is allowed only as a single action") and says nothing to the model.
In the feed run of 2026-11-15 remotive_feed sent `mark_attempted, done` 9 steps
in a row.
"""

from unittest.mock import MagicMock

from browser_use.browser.views import BrowserStateSummary
from browser_use.tools.service import Tools

from fill import LoneDone, StepCallback

PAGE = "https://remotive.com/remote-jobs/software-development/x-1"


class TestLoneDone:
    DONE = {"done": {"text": "finished", "success": True}}
    CLICK = {"click": {"index": 4}}

    async def _notes_after(
        self, raw_actions: tuple, mock_app_logger: MagicMock
    ) -> list:
        action_model = Tools().registry.create_action_model()
        actions = [action_model.model_validate(raw) for raw in raw_actions]
        callback = StepCallback(
            "acme", mock_app_logger, lone_done=LoneDone(mock_app_logger.portal("acme"))
        )
        callback.agent_ref = MagicMock()
        callback.agent_ref._message_manager.state.agent_history_items = []
        state = MagicMock(spec=BrowserStateSummary, url=PAGE, screenshot=None)
        output = MagicMock(action=actions, next_goal="", evaluation_previous_goal="")
        await callback.on_step(state, output, step_num=1)
        return callback.agent_ref._message_manager.state.agent_history_items

    async def test_done_after_another_action_gets_a_note(
        self, mock_app_logger: MagicMock
    ) -> None:
        notes = await self._notes_after((self.CLICK, self.DONE), mock_app_logger)
        assert len(notes) == 1
        assert "done must be the only action" in notes[0].system_message

    async def test_done_alone_gets_no_note(self, mock_app_logger: MagicMock) -> None:
        assert await self._notes_after((self.DONE,), mock_app_logger) == []

    async def test_a_step_without_done_gets_no_note(
        self, mock_app_logger: MagicMock
    ) -> None:
        assert await self._notes_after((self.CLICK,), mock_app_logger) == []
