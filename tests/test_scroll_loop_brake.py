"""ScrollLoopBrake: a note reaches browser_use's agent history after a scroll streak."""

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from browser_use.agent.message_manager.service import MessageManager
from browser_use.agent.message_manager.views import MessageManagerState
from browser_use.browser.views import BrowserStateSummary
from browser_use.filesystem.file_system import FileSystem
from browser_use.llm.messages import SystemMessage

from fill import ScrollLoopBrake, StepCallback

SCROLL = ("scroll",)
NOTE_MARK = "Stop scrolling"
STREAKS = 2


@pytest.fixture
def agent(tmp_path: Path) -> SimpleNamespace:
    """Stand-in Agent that owns a real browser_use MessageManager."""
    manager = MessageManager(
        task="find a job",
        system_message=SystemMessage(content="system"),
        file_system=FileSystem(tmp_path),
        state=MessageManagerState(),
    )
    return SimpleNamespace(_message_manager=manager)


@pytest.fixture
def brake() -> ScrollLoopBrake:
    return ScrollLoopBrake(logging.getLogger("test.brake"))


class TestScrollLoopBrake:
    def history(self, agent: SimpleNamespace) -> str:
        return agent._message_manager.agent_history_description

    def test_note_after_the_limit_of_scroll_only_steps(
        self, brake: ScrollLoopBrake, agent: SimpleNamespace
    ) -> None:
        for _ in range(ScrollLoopBrake.MAX_SCROLL_ONLY_STEPS - 1):
            brake.observe(SCROLL, agent)
        assert NOTE_MARK not in self.history(agent)

        brake.observe(SCROLL, agent)

        assert NOTE_MARK in self.history(agent)

    def test_any_other_action_resets_the_streak(
        self, brake: ScrollLoopBrake, agent: SimpleNamespace
    ) -> None:
        for _ in range(ScrollLoopBrake.MAX_SCROLL_ONLY_STEPS - 1):
            brake.observe(SCROLL, agent)
        brake.observe(("scroll", "click"), agent)
        for _ in range(ScrollLoopBrake.MAX_SCROLL_ONLY_STEPS - 1):
            brake.observe(SCROLL, agent)

        assert NOTE_MARK not in self.history(agent)

    def test_repeats_the_note_while_the_streak_goes_on(
        self, brake: ScrollLoopBrake, agent: SimpleNamespace
    ) -> None:
        for _ in range(ScrollLoopBrake.MAX_SCROLL_ONLY_STEPS * STREAKS):
            brake.observe(SCROLL, agent)

        assert self.history(agent).count(NOTE_MARK) == STREAKS


class TestStepCallbackFeedsTheBrake:
    async def test_scroll_only_steps_reach_the_agent_history(
        self,
        mock_app_logger: MagicMock,
        brake: ScrollLoopBrake,
        agent: SimpleNamespace,
    ) -> None:
        # mock_app_logger also points Paths at a temp dir (via tmp_app_root).
        callback = StepCallback("acme", mock_app_logger, scroll_brake=brake)
        callback.agent_ref = agent
        state = MagicMock(spec=BrowserStateSummary, url="https://acme/jobs")
        state.screenshot = None
        scroll_step = MagicMock(action=[{"scroll": {"down": True}}])

        for step in range(1, ScrollLoopBrake.MAX_SCROLL_ONLY_STEPS + 1):
            await callback.on_step(state, scroll_step, step_num=step)

        assert NOTE_MARK in agent._message_manager.agent_history_description
