"""PortalRunner — credential injection (portal_password + LinkedIn SSO)."""

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
