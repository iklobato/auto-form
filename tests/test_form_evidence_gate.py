"""mark_applied refuses a job where no application form was filled.

Feed run of 2026-11-15: on remoteok every Apply went to /sign-up, the agent typed
there, then called mark_applied 13 times with 2 inputs in total and reported that
all 8 applications were simulated.
"""

from unittest.mock import MagicMock

import pytest

from tests.conftest import FakeRegistry

from fill import AgentActions, JsonlTracker, PortalFacts

JOB = "https://jobs.lever.co/acme/123/apply"
SIGN_UP = "https://remoteok.com/sign-up?redirect_url=x"
FILLED_FORM = ("input", "input", "upload_file")


class GateRun:
    """The tools of one run, with the PortalFacts the step callback would feed."""

    def __init__(self, request: pytest.FixtureRequest, facts: PortalFacts) -> None:
        self.request = request
        self.facts = facts

    def start_dry(self) -> FakeRegistry:
        return self._start({"dry_run": True})

    def start_real(self, network_recorder: object = None) -> FakeRegistry:
        return self._start({"dry_run": False, "network_recorder": network_recorder})

    def _start(self, mode: dict) -> FakeRegistry:
        fixture = self.request.getfixturevalue
        AgentActions(
            fixture("mock_agent"),
            "acme",
            yaml_store=None,
            applied=fixture("applied_tracker"),
            attempted=fixture("attempted_tracker"),
            recorder=fixture("recorder"),
            app_logger=fixture("mock_app_logger"),
            facts=self.facts,
            **mode,
        ).register_all()
        return fixture("fake_registry")


@pytest.fixture
def gate(request: pytest.FixtureRequest) -> GateRun:
    return GateRun(request, PortalFacts())


class TestDryRun:
    async def test_no_form_is_not_counted(self, gate: GateRun) -> None:
        tools = gate.start_dry()
        reply = await tools.actions["mark_applied"](JOB, "Engineer")
        assert reply.extracted_content.startswith("Not counted: no application form")

    async def test_typing_on_a_sign_up_page_is_not_a_form(self, gate: GateRun) -> None:
        tools = gate.start_dry()
        gate.facts.observe_step(SIGN_UP, ["input", "input", "click"])
        reply = await tools.actions["mark_applied"](JOB, "Engineer")
        assert reply.extracted_content.startswith("Not counted")

    async def test_a_filled_form_is_counted(self, gate: GateRun) -> None:
        tools = gate.start_dry()
        gate.facts.observe_step(JOB, list(FILLED_FORM))
        reply = await tools.actions["mark_applied"](JOB, "Engineer")
        assert reply.extracted_content.startswith("Done: Engineer counts as handled")


class TestRealRun:
    async def test_no_form_writes_nothing(
        self, gate: GateRun, applied_tracker: JsonlTracker
    ) -> None:
        tools = gate.start_real()
        await tools.actions["mark_applied"](JOB, "Engineer")
        assert applied_tracker.load_keys() == set()

    async def test_a_filled_form_is_recorded(
        self, gate: GateRun, applied_tracker: JsonlTracker
    ) -> None:
        tools = gate.start_real()
        gate.facts.observe_step(JOB, list(FILLED_FORM))
        await tools.actions["mark_applied"](JOB, "Engineer")
        assert applied_tracker.load_keys() == {JOB}

    # A one-click apply sends the form with no typing: the page POSTs to its own host.
    async def test_a_form_post_from_the_page_is_enough(
        self, gate: GateRun, applied_tracker: JsonlTracker
    ) -> None:
        recorder = MagicMock(
            recent=[{"url": JOB, "page_url": JOB, "resource_type": "Document"}]
        )
        tools = gate.start_real(recorder)
        await tools.actions["mark_applied"](JOB, "Engineer")
        assert applied_tracker.load_keys() == {JOB}

    # The form work of one job must not let the next job through.
    async def test_each_job_needs_its_own_form(
        self, gate: GateRun, applied_tracker: JsonlTracker
    ) -> None:
        tools = gate.start_real()
        gate.facts.observe_step(JOB, list(FILLED_FORM))
        await tools.actions["mark_applied"](JOB, "Engineer")
        await tools.actions["mark_applied"]("https://jobs.lever.co/acme/456", "Other")
        assert applied_tracker.load_keys() == {JOB}
