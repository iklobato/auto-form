"""CredentialsResolver — yaml direct values vs env-var pointers."""

import pytest

from fill import CredentialsResolver


class TestResolve:
    def test_direct_yaml_values_win(self, cred_resolver: CredentialsResolver):
        creds = cred_resolver.resolve({"username": "u@x", "password": "pw"})
        assert creds == {"<username>": "u@x", "<password>": "pw"}

    def test_env_var_fallback(
        self, cred_resolver: CredentialsResolver, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("X_USER", "u@x")
        monkeypatch.setenv("X_PWD", "pw")
        creds = cred_resolver.resolve(
            {"username_env": "X_USER", "password_env": "X_PWD"}
        )
        assert creds == {"<username>": "u@x", "<password>": "pw"}

    def test_direct_overrides_env(
        self, cred_resolver: CredentialsResolver, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("X_USER", "from_env")
        creds = cred_resolver.resolve(
            {"username": "from_yaml", "username_env": "X_USER"}
        )
        assert creds["<username>"] == "from_yaml"

    def test_missing_env_skips_field(
        self, cred_resolver: CredentialsResolver, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("UNSET_USER", raising=False)
        creds = cred_resolver.resolve({"username_env": "UNSET_USER"})
        assert "<username>" not in creds

    def test_empty_config_returns_empty(self, cred_resolver: CredentialsResolver):
        assert cred_resolver.resolve({}) == {}
