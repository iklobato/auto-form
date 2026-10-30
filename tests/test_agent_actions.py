"""AgentActions — invoke each registered action and assert behaviour."""

import json
from unittest.mock import MagicMock

import pytest

from tests.conftest import FakeRegistry

from fill import (
    AgentActions,
    ApplicationRecorder,
    JsonlTracker,
    NetworkRecorder,
    Paths,
    YamlStore,
)


class TestRegisterAll:
    def test_registers_expected_set_without_yaml_store(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()
        assert set(fake_registry.actions) == {
            "check_applied",
            "mark_applied",
            "mark_attempted",
            "save_screenshot",
            "save_note",
        }

    def test_registers_save_credentials_when_yaml_store_present(
        self,
        mock_agent: MagicMock,
        fake_registry,
        yaml_store: YamlStore,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=yaml_store,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()
        assert "save_credentials" in fake_registry.actions


class TestCheckAppliedUrlVariants:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "variant",
        [
            "https://jobs.lever.co/acme/123/",
            "http://www.Jobs.Lever.co/acme/123",
            "https://jobs.lever.co/acme/123?utm_source=linkedin&lever-source=LinkedIn",
            "https://jobs.lever.co/acme/123#apply",
        ],
    )
    async def test_same_job_with_tracking_or_formatting_is_already_applied(
        self,
        variant: str,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        await applied_tracker.append({"job_url": "https://jobs.lever.co/acme/123"})
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()

        result = await fake_registry.actions["check_applied"](variant)
        assert result.extracted_content == "already_applied"

    @pytest.mark.asyncio
    async def test_different_job_id_param_is_not_applied(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        await applied_tracker.append({"job_url": "https://acme.com/careers?gh_jid=111"})
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()

        result = await fake_registry.actions["check_applied"](
            "https://acme.com/careers?gh_jid=222&utm_medium=x"
        )
        assert result.extracted_content == "not_applied"


class TestCheckApplied:
    @pytest.mark.asyncio
    async def test_already_applied_short_circuits(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        await applied_tracker.append({"job_url": "https://x/1"})
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()

        result = await fake_registry.actions["check_applied"]("https://x/1")
        assert result.extracted_content == "already_applied"

    @pytest.mark.asyncio
    async def test_already_attempted(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        await attempted_tracker.append({"job_url": "https://x/2"})
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()

        result = await fake_registry.actions["check_applied"]("https://x/2")
        assert result.extracted_content == "already_attempted"

    @pytest.mark.asyncio
    async def test_not_applied(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()
        result = await fake_registry.actions["check_applied"]("https://x/3")
        assert result.extracted_content == "not_applied"

    @pytest.mark.asyncio
    async def test_loop_detected_after_repeated_checks_on_attempted_url(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        # Reproduces the exact bug from the loop log: agent re-checks the same
        # already-attempted URL over and over. Counter must escalate.
        await attempted_tracker.append({"job_url": "https://x/looping"})
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()
        check = fake_registry.actions["check_applied"]

        r1 = await check("https://x/looping")
        r2 = await check("https://x/looping")
        r3 = await check("https://x/looping")
        assert r1.extracted_content == "already_attempted"
        assert r2.extracted_content == "already_attempted"
        assert r3.extracted_content.startswith("loop_detected")

    @pytest.mark.asyncio
    async def test_loop_detected_after_repeated_not_applied_checks(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        # Even for fresh URLs, calling check_applied 3+ times signals confusion.
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()
        check = fake_registry.actions["check_applied"]

        await check("https://x/new")
        await check("https://x/new")
        r3 = await check("https://x/new")
        assert r3.extracted_content.startswith("loop_detected")

    @pytest.mark.asyncio
    async def test_visit_counter_is_per_url_not_global(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        # Checking many DIFFERENT URLs must not trigger the loop signal.
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()
        check = fake_registry.actions["check_applied"]
        for i in range(10):
            r = await check(f"https://x/job{i}")
            assert r.extracted_content == "not_applied"


class TestMarkAttemptedDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_does_not_block_the_job_for_real_runs(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        AgentActions(
            mock_agent,
            "acme",
            dry_run=True,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()

        await fake_registry.actions["mark_attempted"](
            job_url="https://x/1",
            job_title="Senior BE",
            reason="login_required",
        )

        assert attempted_tracker.load_keys() == set()


class TestSubmissionEvidence:
    @pytest.mark.asyncio
    async def test_evidence_only_holds_posts_from_the_current_job(
        self,
        tmp_app_root,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        network = NetworkRecorder("acme", mock_app_logger)
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
            network_recorder=network,
        ).register_all()
        actions = fake_registry.actions

        def post(url: str) -> None:
            network._on_request(
                {"requestId": url, "request": {"method": "POST", "url": url}}
            )

        await actions["check_applied"]("https://x/job1")
        post("https://acme/job1/submit")
        await actions["check_applied"]("https://x/job2")
        post("https://acme/job2/submit")
        await actions["mark_applied"](job_url="https://x/job2", job_title="BE")

        record = json.loads(
            next((Paths.APPLICATIONS / "acme").glob("*.json")).read_text()
        )
        assert [p["url"] for p in record["recent_network_posts"]] == [
            "https://acme/job2/submit"
        ]


class TestMarkApplied:
    @pytest.mark.asyncio
    async def test_writes_record_and_appends_to_tracker(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()

        await fake_registry.actions["mark_applied"](
            job_url="https://x/1",
            job_title="Senior BE",
            salary="$120k",
            location="Remote",
        )
        assert "https://x/1" in applied_tracker.load_keys()
        json_files = list((Paths.APPLICATIONS / "acme").glob("*.json"))
        assert len(json_files) == 1
        rec = json.loads(json_files[0].read_text())
        assert rec["salary"] == "$120k" and rec["location"] == "Remote"

    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        AgentActions(
            mock_agent,
            "acme",
            dry_run=True,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()
        result = await fake_registry.actions["mark_applied"]("u", "t")
        assert applied_tracker.load_keys() == set()
        assert not Paths.APPLICATIONS.exists() or not list(
            Paths.APPLICATIONS.glob("**/*.json")
        )
        # The reply must read as finished, or the agent retries it in a loop
        assert result.extracted_content.startswith("Done: t counts as handled")
        assert "Do not call mark_applied again" in result.extracted_content


class TestMarkAttempted:
    @pytest.mark.asyncio
    async def test_writes_attempted_record(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()

        await fake_registry.actions["mark_attempted"](
            job_url="https://x/1",
            job_title="t",
            reason="external_redirect",
            notes="redirected to company.com",
        )
        assert "https://x/1" in attempted_tracker.load_keys()


class TestSaveNote:
    @pytest.mark.asyncio
    async def test_appends_note_to_file(
        self,
        mock_agent: MagicMock,
        fake_registry,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        Paths.NOTES.parent.mkdir(parents=True, exist_ok=True)
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=None,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()

        await fake_registry.actions["save_note"]("Form has 3 pages")
        text = Paths.NOTES.read_text()
        assert "## Portal: acme" in text
        assert "Form has 3 pages" in text


class TestSaveCredentials:
    @pytest.mark.asyncio
    async def test_writes_to_yaml_store(
        self,
        mock_agent: MagicMock,
        fake_registry,
        yaml_store: YamlStore,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        mock_app_logger: MagicMock,
    ):
        AgentActions(
            mock_agent,
            "acme",
            dry_run=False,
            yaml_store=yaml_store,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            app_logger=mock_app_logger,
        ).register_all()

        await fake_registry.actions["save_credentials"]("u@x", "newpw")
        creds = yaml_store.load()["systems"][0]["credentials"]
        assert creds["username"] == "u@x"
        assert creds["password"] == "newpw"


@pytest.fixture
def dry_run_registry(request: pytest.FixtureRequest) -> FakeRegistry:
    """Tools registered for a dry run, with a YAML store and the notes file present."""
    Paths.NOTES.parent.mkdir(parents=True, exist_ok=True)
    AgentActions(
        request.getfixturevalue("mock_agent"),
        "acme",
        dry_run=True,
        yaml_store=request.getfixturevalue("yaml_store"),
        applied=request.getfixturevalue("applied_tracker"),
        attempted=request.getfixturevalue("attempted_tracker"),
        recorder=request.getfixturevalue("recorder"),
        app_logger=request.getfixturevalue("mock_app_logger"),
    ).register_all()
    return request.getfixturevalue("fake_registry")


@pytest.fixture
def ghost_portal_registry(request: pytest.FixtureRequest) -> FakeRegistry:
    """Tools of a real run for a portal that the systems YAML does not list."""
    AgentActions(
        request.getfixturevalue("mock_agent"),
        "ghost",
        dry_run=False,
        yaml_store=request.getfixturevalue("yaml_store"),
        applied=request.getfixturevalue("applied_tracker"),
        attempted=request.getfixturevalue("attempted_tracker"),
        recorder=request.getfixturevalue("recorder"),
        app_logger=request.getfixturevalue("mock_app_logger"),
    ).register_all()
    return request.getfixturevalue("fake_registry")


class TestSaveCredentialsUnknownPortal:
    async def test_replies_not_saved_and_keeps_the_yaml(
        self, ghost_portal_registry: FakeRegistry, yaml_store: YamlStore
    ) -> None:
        before = yaml_store.path.read_text()

        reply = await ghost_portal_registry.actions["save_credentials"]("u@x", "pw")

        assert yaml_store.path.read_text() == before
        assert reply.extracted_content.startswith("not saved: 'ghost' is not in ")


class TestDryRunWritesNoState:
    # The guard blocks the account-creation POST, so the account the agent "saves"
    # does not exist; writing it would overwrite the portal's real credentials.
    async def test_save_credentials_keeps_the_yaml(
        self, dry_run_registry: FakeRegistry, yaml_store: YamlStore
    ) -> None:
        before = yaml_store.path.read_text()

        reply = await dry_run_registry.actions["save_credentials"]("u@x", "newpw")

        assert yaml_store.path.read_text() == before
        assert reply.extracted_content.startswith("dry_run: not saved")

    # knowledge/*.md is pasted into every prompt, real runs included.
    async def test_save_note_keeps_the_notes_file(
        self, dry_run_registry: FakeRegistry
    ) -> None:
        Paths.NOTES.write_text("")

        reply = await dry_run_registry.actions["save_note"]("upload failed")

        assert Paths.NOTES.read_text() == ""
        assert reply.extracted_content.startswith("dry_run: not saved")
