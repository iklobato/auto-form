"""SecretFieldGuard: a password placeholder is typed only into a password field.

browser_use swaps `<secret><name></secret>` for the real value when it types. On
2026-11-16 the agent typed the LinkedIn and portal password placeholders into an
Ashby application form (remote_game_jobs, steps 9 and 11); a real run would have
sent both passwords to the employer.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from browser_use.browser.views import BrowserStateSummary
from browser_use.tools.service import Tools

import fill
from fill import GuardedStep, PortalFacts, PortalRunner, SecretFieldGuard, StepCallback

FORM = "https://thatgamecompany.com/careers/?ashby_jid=x"
TEXT_FIELD = 514
PASSWORD_FIELD = 7
EMAIL = "<secret><linkedin_sso_email></secret>"
LINKEDIN_PASSWORD = "<secret><linkedin_sso_password></secret>"
PORTAL_PASSWORD = "<secret><portal_password></secret>"


class TestSecretFieldGuard:
    def _step(
        self, raw_actions: tuple, mock_app_logger: MagicMock
    ) -> tuple[list, list]:
        action_model = Tools().registry.create_action_model()
        actions = [action_model.model_validate(raw) for raw in raw_actions]
        fields = {
            TEXT_FIELD: SimpleNamespace(attributes={"type": "text"}),
            PASSWORD_FIELD: SimpleNamespace(attributes={"type": "password"}),
        }
        state = MagicMock(
            spec=BrowserStateSummary,
            url=FORM,
            screenshot=None,
            dom_state=SimpleNamespace(selector_map=fields),
        )
        callback = StepCallback("acme", mock_app_logger)
        callback.agent_ref = MagicMock()
        callback.agent_ref._message_manager.state.agent_history_items = []
        output = MagicMock(action=actions, next_goal="", evaluation_previous_goal="")
        return actions, state, callback, output

    async def _run(self, raw_actions: tuple, mock_app_logger: MagicMock) -> tuple:
        actions, state, callback, output = self._step(raw_actions, mock_app_logger)
        guard = SecretFieldGuard(mock_app_logger.portal("acme"))
        await GuardedStep((guard,), callback).on_step(state, output, step_num=1)
        kept = [action.model_dump(exclude_none=True) for action in actions]
        notes = callback.agent_ref._message_manager.state.agent_history_items
        return kept, notes

    def _typing(self, index: int, text: str) -> dict:
        return {"input": {"index": index, "text": text, "clear": True}}

    async def test_password_into_a_text_field_is_dropped(
        self, mock_app_logger: MagicMock
    ) -> None:
        kept, notes = await self._run(
            (self._typing(TEXT_FIELD, LINKEDIN_PASSWORD),), mock_app_logger
        )
        assert not any("input" in action for action in kept)
        assert "password" in notes[0].system_message

    async def test_portal_password_into_a_text_field_is_dropped(
        self, mock_app_logger: MagicMock
    ) -> None:
        kept, _ = await self._run(
            (self._typing(TEXT_FIELD, PORTAL_PASSWORD),), mock_app_logger
        )
        assert not any("input" in action for action in kept)

    async def test_password_into_a_password_field_runs(
        self, mock_app_logger: MagicMock
    ) -> None:
        kept, notes = await self._run(
            (self._typing(PASSWORD_FIELD, PORTAL_PASSWORD),), mock_app_logger
        )
        assert kept == [
            {"input": {"index": PASSWORD_FIELD, "text": PORTAL_PASSWORD, "clear": True}}
        ]
        assert notes == []

    async def test_the_email_placeholder_is_not_a_password(
        self, mock_app_logger: MagicMock
    ) -> None:
        kept, _ = await self._run((self._typing(TEXT_FIELD, EMAIL),), mock_app_logger)
        assert kept[0]["input"]["text"] == EMAIL

    async def test_other_actions_of_the_step_still_run(
        self, mock_app_logger: MagicMock
    ) -> None:
        click = {"click": {"index": TEXT_FIELD}}
        kept, _ = await self._run(
            (self._typing(TEXT_FIELD, LINKEDIN_PASSWORD), click), mock_app_logger
        )
        assert kept == [click]


class TestWiring:
    # Without this wiring the password check never runs in a real run.
    def test_steps_go_through_the_password_check(
        self, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_ctor = MagicMock()
        monkeypatch.setattr(fill, "Agent", agent_ctor)
        monkeypatch.setattr(fill, "BrowserProfile", MagicMock())

        self._runner(request)._build_agent(task="t", credentials={})

        hook = agent_ctor.call_args.kwargs["register_new_step_callback"]
        assert isinstance(hook.__self__, GuardedStep)

    def _runner(self, request: pytest.FixtureRequest) -> PortalRunner:
        fixture = request.getfixturevalue
        fixture("tmp_app_root")
        return PortalRunner(
            fixture("sample_system"),
            knowledge="K",
            llm=MagicMock(),
            opts=fixture("run_opts"),
            yaml_store=fixture("yaml_store"),
            task_builder=fixture("task_builder"),
            cred_resolver=fixture("cred_resolver"),
            applied=fixture("applied_tracker"),
            attempted=fixture("attempted_tracker"),
            recorder=fixture("recorder"),
            app_logger=fixture("mock_app_logger"),
            facts=PortalFacts(),
        )
