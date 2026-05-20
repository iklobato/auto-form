"""YamlStore — load + atomic credential updates."""

from pathlib import Path

import pytest
import yaml

from fill import YamlStore


class TestYamlStoreLoad:
    def test_load_returns_parsed_dict(self, systems_yaml_file: Path):
        store = YamlStore(systems_yaml_file)
        data = store.load()
        assert isinstance(data, dict)
        assert data["systems"][0]["name"] == "acme"

    def test_load_handles_sources_schema(self, tmp_path: Path):
        path = tmp_path / "all.yaml"
        path.write_text(yaml.dump({"sources": [{"name": "x"}]}))
        store = YamlStore(path)
        assert store.load()["sources"][0]["name"] == "x"


class TestUpdateCredentials:
    @pytest.mark.asyncio
    async def test_writes_username_and_password(self, systems_yaml_file: Path):
        store = YamlStore(systems_yaml_file)
        await store.update_credentials("acme", "new@example.com", "newpass")
        reloaded = store.load()
        creds = reloaded["systems"][0]["credentials"]
        assert creds["username"] == "new@example.com"
        assert creds["password"] == "newpass"

    @pytest.mark.asyncio
    async def test_creates_credentials_block_when_missing(self, tmp_path: Path):
        path = tmp_path / "s.yaml"
        path.write_text(yaml.dump({"systems": [{"name": "x", "url": "https://x"}]}))
        store = YamlStore(path)
        await store.update_credentials("x", "u", "p")
        loaded = store.load()
        assert loaded["systems"][0]["credentials"] == {"username": "u", "password": "p"}

    @pytest.mark.asyncio
    async def test_removes_env_pointers_when_setting_direct(self, tmp_path: Path):
        path = tmp_path / "s.yaml"
        path.write_text(yaml.dump({"systems": [{
            "name": "x",
            "credentials": {"username_env": "X_USER", "password_env": "X_PWD"},
        }]}))
        store = YamlStore(path)
        await store.update_credentials("x", "u@x", "pw")
        creds = store.load()["systems"][0]["credentials"]
        assert "username_env" not in creds
        assert "password_env" not in creds
        assert creds["username"] == "u@x"

    @pytest.mark.asyncio
    async def test_unknown_system_does_not_raise(self, systems_yaml_file: Path):
        store = YamlStore(systems_yaml_file)
        # Should silently no-op rather than raise — the agent could call this
        # mid-flight after the user removed the entry from the yaml.
        await store.update_credentials("nonexistent", "u", "p")

    @pytest.mark.asyncio
    async def test_works_with_sources_schema(self, tmp_path: Path):
        path = tmp_path / "all.yaml"
        path.write_text(yaml.dump({"sources": [{"name": "x"}]}))
        store = YamlStore(path)
        await store.update_credentials("x", "u", "p")
        assert store.load()["sources"][0]["credentials"]["username"] == "u"
