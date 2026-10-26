"""Orchestrator — active-portal selection only (full run integration is too heavy)."""

from unittest.mock import MagicMock

import pytest

from fill import Orchestrator


class TestSelectActive:
    @pytest.mark.parametrize(
        "url,is_active",
        [
            ("https://acme.example.com/", True),
            ("", False),
            (None, False),
            ("$WORKDAY_URL", False),  # unexpanded env var → skip
        ],
    )
    def test_filters_unconfigured_urls(
        self, url, is_active, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("WORKDAY_URL", raising=False)
        sys = {"name": "x", "url": url} if url is not None else {"name": "x"}
        active = Orchestrator._select_active([sys])
        assert (len(active) == 1) is is_active

    def test_keeps_expanded_env_url(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MY_URL", "https://expanded.example.com/")
        active = Orchestrator._select_active([{"name": "x", "url": "$MY_URL"}])
        assert len(active) == 1

    def test_handles_mix(self):
        systems = [
            {"name": "ok1", "url": "https://a.example.com/"},
            {"name": "missing"},
            {"name": "envvar", "url": "$NOT_SET_VAR"},
            {"name": "ok2", "url": "https://b.example.com/"},
        ]
        active = Orchestrator._select_active(systems)
        assert [s["name"] for s in active] == ["ok1", "ok2"]


class TestProxyProviders:
    @staticmethod
    def _orchestrator(systems: list[dict]) -> Orchestrator:
        collaborators = {
            name: MagicMock()
            for name in (
                "llm",
                "opts",
                "task_builder",
                "cred_resolver",
                "applied",
                "attempted",
                "recorder",
                "app_logger",
            )
        }
        return Orchestrator(
            systems, knowledge="K", max_workers=1, yaml_store=None, **collaborators
        )

    def test_bad_proxy_block_fails_at_startup(self):
        with pytest.raises(ValueError, match="Unknown proxy kind"):
            self._orchestrator(
                [
                    {
                        "name": "a",
                        "url": "https://a.example.com/",
                        "proxy": {"kind": "webshre"},
                    }
                ]
            )

    def test_retry_reuses_the_same_provider(self, tmp_path):
        proxies = tmp_path / "p.txt"
        proxies.write_text("1.1.1.1:80\n2.2.2.2:80\n")
        orch = self._orchestrator(
            [
                {
                    "name": "a",
                    "url": "https://a.example.com/",
                    "proxy": {"kind": "list", "path": str(proxies)},
                }
            ]
        )
        system = orch.active[0]
        first = orch._make_runner(system).proxy_provider.acquire("a").host
        retry = orch._make_runner(system).proxy_provider.acquire("a").host
        assert (first, retry) == ("1.1.1.1", "2.2.2.2")
