"""PortalRunner — credential injection (portal_password + LinkedIn SSO)."""

import os
import socket
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import fill
from fill import (
    AgentStoppedEarly,
    ApplicationRecorder,
    CredentialsResolver,
    JsonlTracker,
    PortalRunner,
    ProfileInUse,
    Proxy,
    RunOptions,
    TaskBuilder,
    YamlStore,
)


def _make_runner(
    system,
    *,
    opts,
    yaml_store,
    mock_app_logger,
    task_builder,
    cred_resolver,
    applied,
    attempted,
    recorder,
    proxy_provider=None,
) -> PortalRunner:
    return PortalRunner(
        system,
        knowledge="K",
        llm=MagicMock(),
        opts=opts,
        yaml_store=yaml_store,
        task_builder=task_builder,
        cred_resolver=cred_resolver,
        applied=applied,
        attempted=attempted,
        recorder=recorder,
        app_logger=mock_app_logger,
        proxy_provider=proxy_provider,
    )


class TestBuildCredentials:
    def test_includes_portal_credentials_when_configured(
        self,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
    ):
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )
        creds = runner._build_credentials()
        assert creds["<username>"] == "user@example.com"
        assert creds["<password>"] == "secret"

    def test_uses_portal_account_password_env_when_set(
        self,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
    ):
        clean_env.setenv("PORTAL_ACCOUNT_PASSWORD", "fixed-pw")
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )
        creds = runner._build_credentials()
        assert creds["<portal_password>"] == "fixed-pw"

    def test_generated_password_is_reused_across_runs(
        self,
        tmp_app_root: Path,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
    ):
        def new_runner():
            return _make_runner(
                sample_system,
                opts=run_opts,
                yaml_store=yaml_store,
                mock_app_logger=mock_app_logger,
                task_builder=task_builder,
                cred_resolver=cred_resolver,
                applied=applied_tracker,
                attempted=attempted_tracker,
                recorder=recorder,
            )

        first = new_runner()._build_credentials()["<portal_password>"]
        retry = new_runner()._build_credentials()["<portal_password>"]

        assert first == retry
        stored = fill.Paths.PROFILES / sample_system["name"] / "portal_password"
        assert stored.read_text() == first
        assert stored.stat().st_mode & 0o777 == 0o600

    def test_generates_random_when_no_env(
        self,
        tmp_app_root: Path,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
    ):
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )
        creds = runner._build_credentials()
        assert "<portal_password>" in creds
        assert len(creds["<portal_password>"]) >= 12  # token_urlsafe(12)

    def test_includes_linkedin_when_env_set(
        self,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
    ):
        clean_env.setenv("LINKEDIN_SSO_USERNAME", "li@x")
        clean_env.setenv("LINKEDIN_SSO_PASSWORD", "li-pwd")
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )
        creds = runner._build_credentials()
        assert creds["<linkedin_sso_email>"] == "li@x"
        assert creds["<linkedin_sso_password>"] == "li-pwd"

    def test_omits_linkedin_when_env_missing(
        self,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
    ):
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )
        creds = runner._build_credentials()
        assert "<linkedin_sso_email>" not in creds
        assert "<linkedin_sso_password>" not in creds


class TestCleanStaleLocks:
    def test_removes_singleton_lock_files(
        self,
        tmp_path: Path,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
    ):
        # Reproduces the bug from the killed-run scenario: stale Singleton*
        # symlinks prevent Chrome from starting on a profile.
        profile = tmp_path / "profile"
        profile.mkdir()
        (profile / "SingletonLock").symlink_to("dead-host-12345")
        (profile / "SingletonCookie").symlink_to("12345")
        (profile / "SingletonSocket").symlink_to("/tmp/dead.sock")
        (profile / "Default").mkdir()  # actual data, must NOT be deleted

        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )
        runner._clean_stale_locks(profile)

        assert not (profile / "SingletonLock").is_symlink()
        assert not (profile / "SingletonCookie").is_symlink()
        assert not (profile / "SingletonSocket").is_symlink()
        # Real data preserved
        assert (profile / "Default").is_dir()

    @pytest.mark.parametrize(
        "lock_target, in_use",
        [
            (f"{socket.gethostname()}-{os.getpid()}", True),
            (f"{socket.gethostname()}-99999999", False),
        ],
        ids=["live-chrome-same-host", "dead-pid-same-host"],
    )
    def test_only_stale_locks_are_removed(
        self,
        lock_target: str,
        in_use: bool,
        tmp_path: Path,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
    ):
        profile = tmp_path / "profile"
        profile.mkdir()
        (profile / "SingletonLock").symlink_to(lock_target)
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )

        if in_use:
            with pytest.raises(ProfileInUse, match=str(os.getpid())):
                runner._clean_stale_locks(profile)
            assert (profile / "SingletonLock").is_symlink()
        else:
            runner._clean_stale_locks(profile)
            assert not (profile / "SingletonLock").is_symlink()

    def test_no_locks_present_is_noop(
        self,
        tmp_path: Path,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
    ):
        profile = tmp_path / "profile"
        profile.mkdir()
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )
        runner._clean_stale_locks(profile)  # no exception


class TestRunAgentOutcome:
    @staticmethod
    def _agent_returning(*, is_done: bool) -> MagicMock:
        history = MagicMock()
        history.is_done.return_value = is_done
        history.final_result.return_value = "applied to 2 roles" if is_done else None
        history.history = [object()] * 3
        agent = MagicMock()
        agent.run = AsyncMock(return_value=history)
        return agent

    @pytest.mark.asyncio
    async def test_stopped_without_done_is_a_failure(
        self,
        tmp_app_root: Path,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
    ):
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )
        with pytest.raises(AgentStoppedEarly, match="3 steps"):
            await runner._run_agent(self._agent_returning(is_done=False))

    @pytest.mark.asyncio
    async def test_done_returns_final_result(
        self,
        tmp_app_root: Path,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
    ):
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )
        assert await runner._run_agent(self._agent_returning(is_done=True)) == (
            "applied to 2 roles"
        )


class TestProxyWiring:
    """The browser profile must carry proxy info iff a provider was injected."""

    @staticmethod
    def _patch_browser(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
        profile_ctor = MagicMock(return_value=MagicMock(name="profile"))
        agent_ctor = MagicMock(return_value=MagicMock(name="agent"))
        monkeypatch.setattr(fill, "BrowserProfile", profile_ctor)
        monkeypatch.setattr(fill, "Agent", agent_ctor)
        return profile_ctor, agent_ctor

    @pytest.mark.asyncio
    async def test_proxy_kwarg_passed_when_provider_set(
        self,
        tmp_app_root: Path,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
        monkeypatch: pytest.MonkeyPatch,
    ):
        profile_ctor, _ = self._patch_browser(monkeypatch)
        acquired_on = []

        def acquire(portal: str) -> Proxy:
            acquired_on.append(threading.current_thread())
            return Proxy(host="5.5.5.5", port=8080, username="u", password="p")

        provider = MagicMock()
        provider.acquire.side_effect = acquire
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            proxy_provider=provider,
        )
        monkeypatch.setattr(runner, "_run_agent", AsyncMock(return_value="done"))

        await runner.run()

        provider.acquire.assert_called_once_with(sample_system["name"])
        # Off the event loop thread, so a slow provider API cannot stall other portals
        assert acquired_on and acquired_on[0] is not threading.main_thread()
        kwargs = profile_ctor.call_args.kwargs
        assert kwargs["proxy"] == {
            "server": "http://5.5.5.5:8080",
            "username": "u",
            "password": "p",
        }

    def test_portal_resume_overrides_run_resume_for_uploads(
        self,
        tmp_app_root: Path,
        tmp_path: Path,
        sample_system: dict,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
        monkeypatch: pytest.MonkeyPatch,
    ):
        _, agent_ctor = self._patch_browser(monkeypatch)
        run_cv, portal_cv = tmp_path / "run.pdf", tmp_path / "portal.pdf"
        run_cv.write_bytes(b"%PDF")
        portal_cv.write_bytes(b"%PDF")
        system = {**sample_system, "resume_path": str(portal_cv)}
        opts = RunOptions(resume_path=str(run_cv))
        runner = _make_runner(
            system,
            opts=opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )

        runner._build_agent(task="t", credentials={})

        assert agent_ctor.call_args.kwargs["available_file_paths"] == [str(portal_cv)]
        assert opts.resume_for(system) == str(portal_cv)

    def test_dry_run_refuses_authenticated_proxy(
        self,
        tmp_app_root: Path,
        sample_system: dict,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
        monkeypatch: pytest.MonkeyPatch,
    ):
        self._patch_browser(monkeypatch)
        runner = _make_runner(
            sample_system,
            opts=RunOptions(dry_run=True),
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
        )
        with pytest.raises(ValueError, match="authenticated proxy"):
            runner._build_agent(
                task="t",
                credentials={},
                proxy=Proxy(host="h", port=80, username="u", password="p"),
            )

    def test_no_proxy_kwarg_when_provider_absent(
        self,
        tmp_app_root: Path,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
        monkeypatch: pytest.MonkeyPatch,
    ):
        profile_ctor, _ = self._patch_browser(monkeypatch)
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            proxy_provider=None,
        )
        runner._build_agent(task="t", credentials={})
        assert "proxy" not in profile_ctor.call_args.kwargs

    @pytest.mark.asyncio
    async def test_provider_failure_propagates(
        self,
        tmp_app_root: Path,
        sample_system: dict,
        run_opts: RunOptions,
        yaml_store: YamlStore,
        mock_app_logger: MagicMock,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker,
        attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder,
        clean_env: pytest.MonkeyPatch,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # No fallbacks: a failed acquire MUST bubble up; the runner does not
        # fall back to a direct connection.
        self._patch_browser(monkeypatch)
        provider = MagicMock()
        provider.acquire.side_effect = RuntimeError("upstream gone")
        runner = _make_runner(
            sample_system,
            opts=run_opts,
            yaml_store=yaml_store,
            mock_app_logger=mock_app_logger,
            task_builder=task_builder,
            cred_resolver=cred_resolver,
            applied=applied_tracker,
            attempted=attempted_tracker,
            recorder=recorder,
            proxy_provider=provider,
        )
        with pytest.raises(RuntimeError, match="upstream gone"):
            await runner.run()


@pytest.fixture
def fresh_runner(request: pytest.FixtureRequest) -> PortalRunner:
    """A runner for a portal that has never run: its profile dir does not exist."""
    request.getfixturevalue("clean_env")
    return _make_runner(
        request.getfixturevalue("sample_system"),
        opts=request.getfixturevalue("run_opts"),
        yaml_store=request.getfixturevalue("yaml_store"),
        mock_app_logger=request.getfixturevalue("mock_app_logger"),
        task_builder=request.getfixturevalue("task_builder"),
        cred_resolver=request.getfixturevalue("cred_resolver"),
        applied=request.getfixturevalue("applied_tracker"),
        attempted=request.getfixturevalue("attempted_tracker"),
        recorder=request.getfixturevalue("recorder"),
    )


class TestBrowserStartSession:
    # run() writes the generated portal password into the profile dir first
    async def test_first_run_reports_a_fresh_profile(
        self,
        fresh_runner: PortalRunner,
        mock_app_logger: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, agent_ctor = TestProxyWiring._patch_browser(monkeypatch)
        agent_ctor.return_value = TestRunAgentOutcome._agent_returning(is_done=True)

        await fresh_runner.run()

        starts = [
            call.kwargs["session"]
            for call in mock_app_logger.emit.call_args_list
            if call.args and call.args[0] == "browser_start"
        ]
        assert starts == ["fresh"]


class TestRunRecordsUnmarkedSubmit:
    # flash-lite sent a form and called done without mark_applied (2026-10-30)
    async def test_form_post_without_mark_applied_is_recorded_at_run_end(
        self,
        fresh_runner: PortalRunner,
        attempted_tracker: JsonlTracker,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, agent_ctor = TestProxyWiring._patch_browser(monkeypatch)
        agent = TestRunAgentOutcome._agent_returning(is_done=True)
        agent.run.side_effect = BrowserSubmits(agent_ctor, agent).submit_then_stop
        agent_ctor.return_value = agent

        await fresh_runner.run()

        assert attempted_tracker.load_keys() == {SUBMIT_PAGE}


SUBMIT_PAGE = "https://jobs.acme.example/1001/apply"


class BrowserSubmits:
    """Agent.run stand-in: the page sends a form POST, then the agent stops."""

    def __init__(self, agent_ctor: MagicMock, agent: MagicMock) -> None:
        self.agent_ctor = agent_ctor
        self.agent = agent

    async def submit_then_stop(self) -> MagicMock:
        step_callback = self.agent_ctor.call_args.kwargs["register_new_step_callback"]
        step_callback.__self__.network_recorder._on_request(
            {
                "requestId": "r1",
                "documentURL": SUBMIT_PAGE,
                "type": "Document",
                "request": {"method": "POST", "url": SUBMIT_PAGE},
            }
        )
        return self.agent.run.return_value
