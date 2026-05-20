"""PortalRunner — credential injection (portal_password + LinkedIn SSO)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fill import (
    ApplicationRecorder,
    CredentialsResolver,
    JsonlTracker,
    PortalRunner,
    RunOptions,
    TaskBuilder,
    YamlStore,
)


def _make_runner(system, *, opts, yaml_store, mock_app_logger,
                 task_builder, cred_resolver, applied, attempted, recorder) -> PortalRunner:
    return PortalRunner(
        system, knowledge="K", llm=MagicMock(), opts=opts, yaml_store=yaml_store,
        task_builder=task_builder, cred_resolver=cred_resolver,
        applied=applied, attempted=attempted, recorder=recorder,
        app_logger=mock_app_logger,
    )


class TestBuildCredentials:
    def test_includes_portal_credentials_when_configured(
        self, sample_system: dict, run_opts: RunOptions, yaml_store: YamlStore,
        mock_app_logger: MagicMock, task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker, attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder, clean_env: pytest.MonkeyPatch,
    ):
        runner = _make_runner(sample_system, opts=run_opts, yaml_store=yaml_store,
                              mock_app_logger=mock_app_logger, task_builder=task_builder,
                              cred_resolver=cred_resolver, applied=applied_tracker,
                              attempted=attempted_tracker, recorder=recorder)
        creds = runner._build_credentials()
        assert creds["<username>"] == "user@example.com"
        assert creds["<password>"] == "secret"

    def test_uses_portal_account_password_env_when_set(
        self, sample_system: dict, run_opts: RunOptions, yaml_store: YamlStore,
        mock_app_logger: MagicMock, task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker, attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder, clean_env: pytest.MonkeyPatch,
    ):
        clean_env.setenv("PORTAL_ACCOUNT_PASSWORD", "fixed-pw")
        runner = _make_runner(sample_system, opts=run_opts, yaml_store=yaml_store,
                              mock_app_logger=mock_app_logger, task_builder=task_builder,
                              cred_resolver=cred_resolver, applied=applied_tracker,
                              attempted=attempted_tracker, recorder=recorder)
        creds = runner._build_credentials()
        assert creds["<portal_password>"] == "fixed-pw"

    def test_generates_random_when_no_env(
        self, sample_system: dict, run_opts: RunOptions, yaml_store: YamlStore,
        mock_app_logger: MagicMock, task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker, attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder, clean_env: pytest.MonkeyPatch,
    ):
        runner = _make_runner(sample_system, opts=run_opts, yaml_store=yaml_store,
                              mock_app_logger=mock_app_logger, task_builder=task_builder,
                              cred_resolver=cred_resolver, applied=applied_tracker,
                              attempted=attempted_tracker, recorder=recorder)
        creds = runner._build_credentials()
        assert "<portal_password>" in creds
        assert len(creds["<portal_password>"]) >= 12  # token_urlsafe(12)

    def test_includes_linkedin_when_env_set(
        self, sample_system: dict, run_opts: RunOptions, yaml_store: YamlStore,
        mock_app_logger: MagicMock, task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker, attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder, clean_env: pytest.MonkeyPatch,
    ):
        clean_env.setenv("LINKEDIN_SSO_USERNAME", "li@x")
        clean_env.setenv("LINKEDIN_SSO_PASSWORD", "li-pwd")
        runner = _make_runner(sample_system, opts=run_opts, yaml_store=yaml_store,
                              mock_app_logger=mock_app_logger, task_builder=task_builder,
                              cred_resolver=cred_resolver, applied=applied_tracker,
                              attempted=attempted_tracker, recorder=recorder)
        creds = runner._build_credentials()
        assert creds["<linkedin_sso_email>"] == "li@x"
        assert creds["<linkedin_sso_password>"] == "li-pwd"

    def test_omits_linkedin_when_env_missing(
        self, sample_system: dict, run_opts: RunOptions, yaml_store: YamlStore,
        mock_app_logger: MagicMock, task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker, attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder, clean_env: pytest.MonkeyPatch,
    ):
        runner = _make_runner(sample_system, opts=run_opts, yaml_store=yaml_store,
                              mock_app_logger=mock_app_logger, task_builder=task_builder,
                              cred_resolver=cred_resolver, applied=applied_tracker,
                              attempted=attempted_tracker, recorder=recorder)
        creds = runner._build_credentials()
        assert "<linkedin_sso_email>" not in creds
        assert "<linkedin_sso_password>" not in creds


class TestCleanStaleLocks:
    def test_removes_singleton_lock_files(
        self, tmp_path: Path, sample_system: dict, run_opts: RunOptions,
        yaml_store: YamlStore, mock_app_logger: MagicMock,
        task_builder: TaskBuilder, cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker, attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder, clean_env: pytest.MonkeyPatch,
    ):
        # Reproduces the bug from the killed-run scenario: stale Singleton*
        # symlinks prevent Chrome from starting on a profile.
        profile = tmp_path / "profile"
        profile.mkdir()
        (profile / "SingletonLock").symlink_to("dead-host-12345")
        (profile / "SingletonCookie").symlink_to("12345")
        (profile / "SingletonSocket").symlink_to("/tmp/dead.sock")
        (profile / "Default").mkdir()  # actual data, must NOT be deleted

        runner = _make_runner(sample_system, opts=run_opts, yaml_store=yaml_store,
                              mock_app_logger=mock_app_logger, task_builder=task_builder,
                              cred_resolver=cred_resolver, applied=applied_tracker,
                              attempted=attempted_tracker, recorder=recorder)
        runner._clean_stale_locks(profile)

        assert not (profile / "SingletonLock").is_symlink()
        assert not (profile / "SingletonCookie").is_symlink()
        assert not (profile / "SingletonSocket").is_symlink()
        # Real data preserved
        assert (profile / "Default").is_dir()

    def test_no_locks_present_is_noop(
        self, tmp_path: Path, sample_system: dict, run_opts: RunOptions,
        yaml_store: YamlStore, mock_app_logger: MagicMock,
        task_builder: TaskBuilder, cred_resolver: CredentialsResolver,
        applied_tracker: JsonlTracker, attempted_tracker: JsonlTracker,
        recorder: ApplicationRecorder, clean_env: pytest.MonkeyPatch,
    ):
        profile = tmp_path / "profile"
        profile.mkdir()
        runner = _make_runner(sample_system, opts=run_opts, yaml_store=yaml_store,
                              mock_app_logger=mock_app_logger, task_builder=task_builder,
                              cred_resolver=cred_resolver, applied=applied_tracker,
                              attempted=attempted_tracker, recorder=recorder)
        runner._clean_stale_locks(profile)  # no exception
