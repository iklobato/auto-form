"""Step checks run by GuardedStep: fake emails and a forgotten resume.

2026-11-16 dry run: the agent typed `henrique.lobato@example.com` into an Ashby
form (remote_game_jobs), and in a local run it never attached the resume, so the
browser refused the submit ("Please select a file.") and the agent gave up.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from browser_use.browser.views import BrowserStateSummary
from browser_use.tools.service import Tools

from fill import (
    GuardedStep,
    ReservedEmailGuard,
    ResumeReminder,
    StepCallback,
    UnknownSecretGuard,
)

FORM = "https://jobs.example-ats.io/acme/101/apply"
NEXT_FORM = "https://jobs.example-ats.io/acme/202/apply"
TEXT_FIELD = 3
FILE_FIELD = 9
RESUME = "/tmp/cv.pdf"


class StepRunner:
    """Runs steps through GuardedStep with the given checks and one fake agent."""

    CLICK = {"click": {"index": TEXT_FIELD}}
    UPLOAD = {"upload_file": {"index": FILE_FIELD, "path": RESUME}}

    def __init__(self, checks: tuple, callback: StepCallback) -> None:
        self.checks = checks
        self.callback = callback

    @classmethod
    def build(cls, checks: tuple, mock_app_logger: MagicMock) -> "StepRunner":
        callback = StepCallback("acme", mock_app_logger)
        callback.agent_ref = MagicMock()
        callback.agent_ref._message_manager.state.agent_history_items = []
        return cls(checks, callback)

    def typing(self, text: str) -> dict:
        return {"input": {"index": TEXT_FIELD, "text": text, "clear": True}}

    async def step(self, url: str, raw_actions: tuple) -> list:
        action_model = Tools().registry.create_action_model()
        actions = [action_model.model_validate(raw) for raw in raw_actions]
        fields = {
            TEXT_FIELD: SimpleNamespace(attributes={"type": "text"}),
            FILE_FIELD: SimpleNamespace(attributes={"type": "file"}),
        }
        state = MagicMock(
            spec=BrowserStateSummary,
            url=url,
            screenshot=None,
            dom_state=SimpleNamespace(selector_map=fields),
        )
        output = MagicMock(action=actions, next_goal="", evaluation_previous_goal="")
        await GuardedStep(self.checks, self.callback).on_step(state, output, 1)
        return [action.model_dump(exclude_none=True) for action in actions]

    def notes(self) -> list[str]:
        items = self.callback.agent_ref._message_manager.state.agent_history_items
        return [item.system_message for item in items]


class TestReservedEmailGuard:
    @pytest.fixture
    def runner(self, mock_app_logger: MagicMock) -> StepRunner:
        guard = ReservedEmailGuard(mock_app_logger.portal("acme"))
        return StepRunner.build((guard,), mock_app_logger)

    @pytest.mark.parametrize(
        "text",
        [
            "henrique.lobato@example.com",
            "me@EXAMPLE.org",
            "a@b.example.net",
            "x@y.test",
            "x@y.invalid",
        ],
    )
    async def test_a_reserved_domain_email_is_dropped(
        self, runner: StepRunner, text: str
    ) -> None:
        kept = await runner.step(FORM, (runner.typing(text), runner.CLICK))
        assert kept == [runner.CLICK]
        assert "profile" in runner.notes()[0]

    @pytest.mark.parametrize(
        "text", ["me@proton.me", "<secret><linkedin_sso_email></secret>", "example"]
    )
    async def test_other_text_runs(self, runner: StepRunner, text: str) -> None:
        kept = await runner.step(FORM, (runner.typing(text),))
        assert kept[0]["input"]["text"] == text


class TestUnknownSecretGuard:
    # rustjobs on 2026-11-16 typed `<secret>Henrique</secret>`; browser_use only swaps
    # names it knows, so the tags would go into the form as text.
    @pytest.fixture
    def runner(self, mock_app_logger: MagicMock) -> StepRunner:
        guard = UnknownSecretGuard(
            ("<linkedin_sso_email>", "<portal_password>"),
            mock_app_logger.portal("acme"),
        )
        return StepRunner.build((guard,), mock_app_logger)

    @pytest.mark.parametrize(
        "text", ["<secret>Henrique</secret>", "<secret><first_name></secret>"]
    )
    async def test_an_unknown_placeholder_is_dropped(
        self, runner: StepRunner, text: str
    ) -> None:
        kept = await runner.step(FORM, (runner.typing(text), runner.CLICK))
        assert kept == [runner.CLICK]
        assert "plain value" in runner.notes()[0]

    @pytest.mark.parametrize(
        "text", ["<secret><linkedin_sso_email></secret>", "Henrique"]
    )
    async def test_a_known_placeholder_or_plain_text_runs(
        self, runner: StepRunner, text: str
    ) -> None:
        kept = await runner.step(FORM, (runner.typing(text),))
        assert kept[0]["input"]["text"] == text


class TestResumeReminder:
    @pytest.fixture
    def runner(self, mock_app_logger: MagicMock) -> StepRunner:
        reminder = ResumeReminder(RESUME, mock_app_logger.portal("acme"))
        return StepRunner.build((reminder,), mock_app_logger)

    async def test_a_file_field_without_an_upload_gets_one_note(
        self, runner: StepRunner
    ) -> None:
        await runner.step(FORM, (runner.typing("Henrique"),))
        await runner.step(FORM, (runner.CLICK,))
        notes = runner.notes()
        assert len(notes) == 1
        assert RESUME in notes[0]

    async def test_no_note_once_the_resume_is_uploaded(
        self, runner: StepRunner
    ) -> None:
        await runner.step(FORM, (runner.UPLOAD,))
        await runner.step(FORM, (runner.CLICK,))
        assert runner.notes() == []

    async def test_each_form_page_is_checked(self, runner: StepRunner) -> None:
        await runner.step(FORM, (runner.UPLOAD,))
        await runner.step(NEXT_FORM, (runner.CLICK,))
        assert len(runner.notes()) == 1

    async def test_the_step_actions_are_not_changed(self, runner: StepRunner) -> None:
        kept = await runner.step(FORM, (runner.CLICK,))
        assert kept == [runner.CLICK]
