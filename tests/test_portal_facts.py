"""PortalFacts: the portal outcome comes from what the agent did, not what it said."""

from unittest.mock import MagicMock

import pytest
from browser_use.browser.views import BrowserStateSummary

from fill import CDPDebugger, PortalFacts, PortalOutcome, StepCallback

JOB = "https://jobs.lever.co/acme/123/apply"
SIGN_UP = "https://board.example.com/sign-up?redirect_url=x"
BOARD = "https://board.example.com/jobs"
FORBIDDEN = 403
AIJOBS_MARK_APPLIED_STEPS = 23


class FactsFromSteps:
    def facts_after(self, steps: list[tuple[str, list[str]]]) -> PortalFacts:
        facts = PortalFacts()
        for url, actions in steps:
            facts.observe_step(url, actions)
        return facts


class TestOutcome(FactsFromSteps):
    def test_an_upload_is_a_filled_form(self) -> None:
        facts = self.facts_after([(JOB, ["upload_file"])])
        assert facts.outcome() is PortalOutcome.FORM_FILLED

    def test_three_inputs_are_a_filled_form(self) -> None:
        facts = self.facts_after(
            [(JOB, ["input", "input"]), (JOB, ["select_dropdown"])]
        )
        assert facts.outcome() is PortalOutcome.FORM_FILLED

    def test_two_inputs_do_not_decide(self) -> None:
        assert self.facts_after([(JOB, ["input", "input"])]).outcome() is (
            PortalOutcome.UNKNOWN
        )

    # aijobs on 2026-11-15: 23 mark_applied, no form action, final text "applied".
    def test_mark_applied_without_a_form_is_not_a_filled_form(self) -> None:
        facts = self.facts_after(
            [(BOARD, ["click", "mark_applied"])] * AIJOBS_MARK_APPLIED_STEPS
        )
        assert facts.outcome() is PortalOutcome.UNKNOWN

    # remoteok in the feed run: every Apply went to /sign-up, where it typed.
    def test_typing_on_a_sign_up_page_is_a_login_wall(self) -> None:
        facts = self.facts_after([(SIGN_UP, ["input", "input", "click"])])
        assert facts.outcome() is PortalOutcome.LOGIN_WALL

    @pytest.mark.parametrize("status", [403, 429, 503])
    def test_an_error_on_a_page_it_was_on_is_blocked(self, status: int) -> None:
        facts = self.facts_after([(BOARD, ["wait"])])
        facts.observe_response(BOARD, status)
        assert facts.outcome() is PortalOutcome.BLOCKED

    def test_an_error_on_a_subresource_does_not_decide(self) -> None:
        facts = self.facts_after([(BOARD, ["click"])])
        facts.observe_response("https://cdn.example.com/x.js", FORBIDDEN)
        assert facts.outcome() is PortalOutcome.UNKNOWN

    def test_a_filled_form_wins_over_an_earlier_block(self) -> None:
        facts = self.facts_after([(BOARD, ["wait"]), (JOB, ["upload_file"])])
        facts.observe_response(BOARD, FORBIDDEN)
        assert facts.outcome() is PortalOutcome.FORM_FILLED

    def test_counts_go_into_the_event(self) -> None:
        on_job = ["input", "upload_file"]
        facts = self.facts_after([(JOB, on_job), (SIGN_UP, ["input"])])
        assert facts.as_dict() == {
            "outcome": "form_filled",
            "form_actions": len(on_job),
            "uploads": 1,
            "auth_steps": 1,
            "pages": len({JOB, SIGN_UP}),
            "blocked_pages": 0,
        }


class TestFactsAreFed(FactsFromSteps):
    async def test_step_callback_reports_each_step(
        self, mock_app_logger: MagicMock
    ) -> None:
        facts = PortalFacts()
        state = MagicMock(spec=BrowserStateSummary, url=JOB, screenshot=None)
        output = MagicMock(
            action=[{"upload_file": {"index": 1}}],
            next_goal="",
            evaluation_previous_goal="",
        )
        callback = StepCallback("acme", mock_app_logger, facts=facts)

        await callback.on_step(state, output, step_num=1)

        assert facts.outcome() is PortalOutcome.FORM_FILLED

    def test_cdp_debugger_reports_error_responses(
        self, mock_app_logger: MagicMock
    ) -> None:
        facts = self.facts_after([(BOARD, ["wait"])])
        debugger = CDPDebugger("acme", mock_app_logger, facts=facts)

        debugger._on_response(
            {"requestId": "r1", "response": {"url": BOARD, "status": FORBIDDEN}}
        )

        assert facts.outcome() is PortalOutcome.BLOCKED
