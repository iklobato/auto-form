"""YamlStore — load + atomic credential updates."""

import asyncio
import fcntl
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


class TestUpdateCredentialsSafety:
    @pytest.mark.asyncio
    async def test_waits_for_another_process_holding_the_lock(self, systems_yaml_file: Path):
        store = YamlStore(systems_yaml_file)
        lock_path = systems_yaml_file.with_name(systems_yaml_file.name + ".lock")
        with open(lock_path, "w") as other_process:
            fcntl.flock(other_process, fcntl.LOCK_EX)
            update = asyncio.create_task(store.update_credentials("acme", "u", "p"))
            await asyncio.sleep(0.3)
            assert "u" != store.load()["systems"][0].get("credentials", {}).get("username")
            fcntl.flock(other_process, fcntl.LOCK_UN)
        await asyncio.wait_for(update, timeout=5)
        assert store.load()["systems"][0]["credentials"]["username"] == "u"

    @pytest.mark.asyncio
    async def test_failed_write_keeps_original_and_leaves_no_temp_file(
        self, systems_yaml_file: Path, monkeypatch: pytest.MonkeyPatch
    ):
        before = systems_yaml_file.read_text()
        systems_yaml_file.chmod(0o640)

        def disk_full(fd):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("fill.os.fsync", disk_full)
        with pytest.raises(OSError):
            await YamlStore(systems_yaml_file).update_credentials("acme", "u", "p")

        assert systems_yaml_file.read_text() == before
        assert [p.name for p in systems_yaml_file.parent.iterdir()
                if p.name.startswith(".systems.yaml.")] == []

    @pytest.mark.asyncio
    async def test_keeps_file_permissions(self, systems_yaml_file: Path):
        systems_yaml_file.chmod(0o600)
        await YamlStore(systems_yaml_file).update_credentials("acme", "u", "p")
        assert systems_yaml_file.stat().st_mode & 0o777 == 0o600


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
