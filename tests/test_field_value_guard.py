"""FieldValueGuard: a fixed form field only gets the value from profile.yaml.

2026-11-16 local runs: the agent sent gender "Non-binary" where the profile says
"Prefer not to say", a salary number that is in no profile row, and "I prefer not
to answer" for veteran where the profile says "I am not a veteran".
"""

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from browser_use.browser.views import BrowserStateSummary
from browser_use.tools.service import Tools

from fill import FieldValueGuard, GuardedStep, StepCallback

FORM = "https://jobs.example-ats.io/acme/101/apply"


@dataclass(frozen=True)
class Typing:
    field: dict
    action: str
    text: str


class FormStep:
    """One step on a form with a single field described by its attributes."""

    FIELD = 7
    PROFILE = {
        "first_name": "Ada",
        "email": "ada.example@mail.io",
        "phone": "+1 555 010 0199",
        "linkedin": "https://www.linkedin.com/in/ada-example",
        "gender": "Prefer not to say",
        "veteran": "I am not a veteran",
        "disability": "No",
        "salary": "120,000 - 150,000 70 - 90 10,000 - 12,500",
    }

    def __init__(self, callback: StepCallback) -> None:
        self.callback = callback

    @classmethod
    def build(cls, mock_app_logger: MagicMock) -> "FormStep":
        callback = StepCallback("acme", mock_app_logger)
        callback.agent_ref = MagicMock()
        callback.agent_ref._message_manager.state.agent_history_items = []
        return cls(callback)

    async def type_into(self, typing: Typing) -> list:
        field, action, text = typing.field, typing.action, typing.text
        node = SimpleNamespace(
            attributes=field, ax_node=SimpleNamespace(name=field.get("label", ""))
        )
        state = MagicMock(
            spec=BrowserStateSummary,
            url=FORM,
            screenshot=None,
            dom_state=SimpleNamespace(selector_map={self.FIELD: node}),
        )
        raw = {action: {"index": self.FIELD, "text": text}}
        actions = [Tools().registry.create_action_model().model_validate(raw)]
        output = MagicMock(action=actions, next_goal="", evaluation_previous_goal="")
        guard = FieldValueGuard(dict(self.PROFILE), self.callback.plog)
        await GuardedStep((guard,), self.callback).on_step(state, output, 1)
        return actions

    def notes(self) -> list[str]:
        items = self.callback.agent_ref._message_manager.state.agent_history_items
        return [item.system_message for item in items]


@pytest.fixture
def form(mock_app_logger: MagicMock) -> FormStep:
    return FormStep.build(mock_app_logger)


class TestDropped:
    @pytest.mark.parametrize(
        "typing",
        [
            Typing({"label": "Gender"}, "select_dropdown", "Non-binary"),
            Typing(
                {"label": "Veteran status"}, "select_dropdown", "I prefer not to answer"
            ),
            Typing(
                {"label": "Disability status"},
                "select_dropdown",
                "I prefer not to answer",
            ),
            Typing({"name": "expected_salary"}, "input", "164500"),
            Typing({"label": "Desired salary"}, "input", "$10,000 - $12,000 per month"),
            Typing({"type": "email", "name": "email"}, "input", "someone@else.io"),
            Typing({"label": "First name"}, "input", "Henry"),
        ],
    )
    async def test_a_value_that_is_not_the_profile_value(
        self, form: FormStep, typing: Typing
    ) -> None:
        kept = await form.type_into(typing)
        assert [action.model_dump(exclude_none=True) for action in kept] == [
            {"wait": {"seconds": 1}}
        ]
        assert "my profile says" in form.notes()[0]


class TestKept:
    @pytest.mark.parametrize(
        "typing",
        [
            Typing({"label": "Gender"}, "select_dropdown", "Prefer not to answer"),
            Typing(
                {"label": "Veteran status"},
                "select_dropdown",
                "I am not a protected veteran",
            ),
            Typing(
                {"label": "Disability"},
                "select_dropdown",
                "No, I do not have a disability",
            ),
            Typing({"label": "Expected salary"}, "input", "10000 - 12500"),
            Typing({"type": "email"}, "input", "Ada.Example@mail.io"),
            Typing({"type": "email"}, "input", "<secret><linkedin_sso_email></secret>"),
            Typing({"type": "tel", "label": "Phone"}, "input", "15550100199"),
            Typing(
                {"label": "LinkedIn profile"}, "input", "linkedin.com/in/ada-example/"
            ),
            Typing(
                {"label": "Why do you want this job?"}, "input", "I like backend work."
            ),
        ],
    )
    async def test_the_profile_value_or_a_free_field(
        self, form: FormStep, typing: Typing
    ) -> None:
        assert len(await form.type_into(typing)) == 1
        assert form.notes() == []
