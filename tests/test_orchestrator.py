"""Orchestrator — active-portal selection only (full run integration is too heavy)."""

import pytest

from fill import Orchestrator


class TestSelectActive:
    @pytest.mark.parametrize("url,is_active", [
        ("https://acme.example.com/", True),
        ("", False),
        (None, False),
        ("$WORKDAY_URL", False),  # unexpanded env var → skip
    ])
    def test_filters_unconfigured_urls(self, url, is_active,
                                       monkeypatch: pytest.MonkeyPatch):
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
