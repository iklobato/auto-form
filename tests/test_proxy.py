"""Proxy providers, decorators, and factory."""

import logging
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from fill import (
    BrightDataProvider,
    CachingProxyProvider,
    LoggingProxyProvider,
    Proxy,
    ProxyListProvider,
    ProxyProviderFactory,
    StaticProxyProvider,
    WebshareProvider,
)

# ── Proxy value object ───────────────────────────────────────────────────────


class TestProxyToBrowserProfile:
    def test_includes_scheme_host_port_and_creds(self):
        p = Proxy(
            host="1.2.3.4",
            port=8080,
            username="u",
            password="p",
            scheme="http",
            label="x",
        )
        assert p.to_browser_profile() == {
            "server": "http://1.2.3.4:8080",
            "username": "u",
            "password": "p",
        }

    def test_omits_credentials_when_absent(self):
        p = Proxy(host="1.2.3.4", port=8080)
        assert p.to_browser_profile() == {
            "server": "http://1.2.3.4:8080",
            "username": None,
            "password": None,
        }


# ── StaticProxyProvider ──────────────────────────────────────────────────────


class TestStaticProxyProvider:
    def test_acquire_returns_configured_proxy(self):
        prov = StaticProxyProvider(
            host="proxy.example.com", port=3128, username="u", password="p"
        )
        proxy = prov.acquire("any-portal")
        assert proxy.host == "proxy.example.com"
        assert proxy.port == 3128
        assert proxy.username == "u"
        assert proxy.password == "p"

    def test_rejects_empty_host(self):
        with pytest.raises(ValueError):
            StaticProxyProvider(host="", port=3128)


# ── ProxyListProvider ────────────────────────────────────────────────────────


class TestProxyListProvider:
    def test_parses_host_port(self, tmp_path: Path):
        path = tmp_path / "proxies.txt"
        path.write_text("1.1.1.1:8080\n2.2.2.2:8081\n")
        prov = ProxyListProvider(path=path)
        p1 = prov.acquire("portal-a")
        p2 = prov.acquire("portal-b")
        assert (p1.host, p1.port) == ("1.1.1.1", 8080)
        assert (p2.host, p2.port) == ("2.2.2.2", 8081)

    def test_parses_host_port_user_pass(self, tmp_path: Path):
        path = tmp_path / "proxies.txt"
        path.write_text("1.1.1.1:8080:alice:s3cret\n")
        prov = ProxyListProvider(path=path)
        p = prov.acquire("portal")
        assert p.username == "alice"
        assert p.password == "s3cret"

    def test_round_robins(self, tmp_path: Path):
        path = tmp_path / "proxies.txt"
        path.write_text("1.1.1.1:1\n2.2.2.2:2\n")
        prov = ProxyListProvider(path=path)
        seen = [prov.acquire("p").host for _ in range(4)]
        assert seen == ["1.1.1.1", "2.2.2.2", "1.1.1.1", "2.2.2.2"]

    def test_ignores_blank_and_comments(self, tmp_path: Path):
        path = tmp_path / "proxies.txt"
        path.write_text("# header\n\n1.1.1.1:8080\n")
        prov = ProxyListProvider(path=path)
        assert prov.acquire("p").host == "1.1.1.1"

    def test_rejects_empty_file(self, tmp_path: Path):
        path = tmp_path / "proxies.txt"
        path.write_text("# only comments\n\n")
        with pytest.raises(ValueError):
            ProxyListProvider(path=path)

    def test_rejects_malformed_line(self, tmp_path: Path):
        path = tmp_path / "proxies.txt"
        path.write_text("garbage\n")
        with pytest.raises(ValueError):
            ProxyListProvider(path=path)


# ── WebshareProvider ─────────────────────────────────────────────────────────


class TestWebshareProvider:
    def test_acquire_builds_proxy_from_api(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v2/proxy/list/"
            assert request.headers["Authorization"] == "Token tkn"
            assert request.url.params.get("mode") == "direct"
            assert request.url.params.get("country_code__in") == "us"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "proxy_address": "5.5.5.5",
                            "port": 1234,
                            "username": "u",
                            "password": "p",
                            "country_code": "US",
                        }
                    ]
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        prov = WebshareProvider(token="tkn", country="us", http=client)
        proxy = prov.acquire("linkedin")
        assert proxy.host == "5.5.5.5"
        assert proxy.port == 1234
        assert proxy.username == "u"
        assert proxy.label == "webshare:US"

    def test_raises_when_api_returns_no_results(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"results": []})
            )
        )
        prov = WebshareProvider(token="tkn", http=client)
        with pytest.raises(RuntimeError):
            prov.acquire("linkedin")

    def test_propagates_http_errors(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(401, json={"detail": "bad token"})
            )
        )
        prov = WebshareProvider(token="bad", http=client)
        with pytest.raises(httpx.HTTPStatusError):
            prov.acquire("linkedin")

    def test_rejects_empty_token(self):
        with pytest.raises(ValueError):
            WebshareProvider(token="")


# ── BrightDataProvider ───────────────────────────────────────────────────────


class TestBrightDataProvider:
    def test_builds_composite_username(self):
        prov = BrightDataProvider(
            customer="hl_abc", zone="residential", password="pw", country="us"
        )
        p = prov.acquire("portal")
        assert p.username == "brd-customer-hl_abc-zone-residential-country-us"
        assert p.password == "pw"
        assert p.host == BrightDataProvider.DEFAULT_HOST

    def test_username_without_country(self):
        prov = BrightDataProvider(customer="hl_abc", zone="dc", password="pw")
        assert prov.acquire("portal").username == "brd-customer-hl_abc-zone-dc"

    def test_rejects_missing_credentials(self):
        with pytest.raises(ValueError):
            BrightDataProvider(customer="", zone="z", password="pw")


# ── Decorators ───────────────────────────────────────────────────────────────


class TestLoggingProxyProvider:
    def test_logs_and_returns_inner_proxy(self):
        inner = MagicMock()
        inner.acquire.return_value = Proxy(host="h", port=1, label="x")
        log = MagicMock(spec=logging.Logger)
        prov = LoggingProxyProvider(inner, log)
        assert prov.acquire("acme") is inner.acquire.return_value
        inner.acquire.assert_called_once_with("acme")
        assert log.info.called

    def test_does_not_swallow_exceptions(self):
        inner = MagicMock()
        inner.acquire.side_effect = RuntimeError("nope")
        prov = LoggingProxyProvider(inner, MagicMock(spec=logging.Logger))
        with pytest.raises(RuntimeError):
            prov.acquire("acme")


class TestCachingProxyProvider:
    def test_caches_within_ttl(self):
        clock = MagicMock(side_effect=[0.0, 5.0])
        inner = MagicMock()
        inner.acquire.return_value = Proxy(host="h", port=1)
        prov = CachingProxyProvider(inner, ttl_s=10.0, clock=clock)
        prov.acquire("acme")
        prov.acquire("acme")
        assert inner.acquire.call_count == 1

    def test_refreshes_after_ttl(self):
        clock = MagicMock(side_effect=[0.0, 11.0])
        inner = MagicMock()
        inner.acquire.return_value = Proxy(host="h", port=1)
        prov = CachingProxyProvider(inner, ttl_s=10.0, clock=clock)
        prov.acquire("acme")
        prov.acquire("acme")
        assert inner.acquire.call_count == 2

    def test_cache_is_per_portal(self):
        clock = MagicMock(side_effect=[0.0, 1.0])
        inner = MagicMock()
        inner.acquire.return_value = Proxy(host="h", port=1)
        prov = CachingProxyProvider(inner, ttl_s=10.0, clock=clock)
        prov.acquire("acme")
        prov.acquire("globex")
        assert inner.acquire.call_count == 2

    def test_rejects_non_positive_ttl(self):
        with pytest.raises(ValueError):
            CachingProxyProvider(MagicMock(), ttl_s=0)


# ── ProxyProviderFactory ─────────────────────────────────────────────────────


class TestProxyProviderFactory:
    def test_returns_none_for_empty_config(self):
        log = MagicMock(spec=logging.Logger)
        assert ProxyProviderFactory.build(None, log=log) is None
        assert ProxyProviderFactory.build({}, log=log) is None
        assert ProxyProviderFactory.build({"kind": "none"}, log=log) is None

    def test_unknown_kind_raises(self):
        log = MagicMock(spec=logging.Logger)
        with pytest.raises(ValueError):
            ProxyProviderFactory.build({"kind": "magic"}, log=log)

    def test_builds_static_provider(self):
        log = MagicMock(spec=logging.Logger)
        prov = ProxyProviderFactory.build(
            {
                "kind": "static",
                "host": "h",
                "port": 1234,
                "username": "u",
                "password": "p",
            },
            log=log,
        )
        proxy = prov.acquire("acme")
        assert (proxy.host, proxy.port, proxy.username) == ("h", 1234, "u")

    def test_static_reads_credentials_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MY_PROXY_USER", "envuser")
        monkeypatch.setenv("MY_PROXY_PASS", "envpass")
        prov = ProxyProviderFactory.build(
            {
                "kind": "static",
                "host": "h",
                "port": 1,
                "username_env": "MY_PROXY_USER",
                "password_env": "MY_PROXY_PASS",
            },
            log=MagicMock(spec=logging.Logger),
        )
        proxy = prov.acquire("acme")
        assert proxy.username == "envuser"
        assert proxy.password == "envpass"

    def test_webshare_requires_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("WEBSHARE_TOKEN", raising=False)
        with pytest.raises(RuntimeError):
            ProxyProviderFactory.build(
                {"kind": "webshare"},
                log=MagicMock(spec=logging.Logger),
            )

    def test_brightdata_requires_all_fields(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("BRIGHTDATA_CUSTOMER", raising=False)
        monkeypatch.delenv("BRIGHTDATA_PASSWORD", raising=False)
        with pytest.raises(RuntimeError):
            ProxyProviderFactory.build(
                {"kind": "brightdata", "zone": "residential"},
                log=MagicMock(spec=logging.Logger),
            )

    def test_caching_decorator_applied_when_ttl_set(self):
        prov = ProxyProviderFactory.build(
            {"kind": "static", "host": "h", "port": 1, "cache_s": 30},
            log=MagicMock(spec=logging.Logger),
        )
        # Logging wraps Caching wraps Static — both decorators present.
        assert isinstance(prov, LoggingProxyProvider)
        assert isinstance(prov._inner, CachingProxyProvider)


class TestProxyListParsing:
    def test_skips_indented_comment(self, tmp_path: Path):
        f = tmp_path / "p.txt"
        f.write_text("  # backup pool\n1.1.1.1:80\n")
        assert ProxyListProvider(path=f).acquire("x").host == "1.1.1.1"

    def test_password_may_contain_colon(self, tmp_path: Path):
        f = tmp_path / "p.txt"
        f.write_text("1.2.3.4:8080:user:pa:ss\n")
        proxy = ProxyListProvider(path=f).acquire("x")
        assert (proxy.username, proxy.password) == ("user", "pa:ss")


class TestSocksAuth:
    def test_socks_with_credentials_is_rejected(self):
        with pytest.raises(ValueError, match="SOCKS"):
            Proxy(host="x", port=1080, scheme="socks5", username="u", password="p")

    def test_socks_without_credentials_is_allowed(self):
        proxy = Proxy(host="x", port=1080, scheme="socks5")
        assert proxy.to_browser_profile()["server"] == "socks5://x:1080"


class TestWebshareSelection:
    @staticmethod
    def _client(results: list[dict]) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"results": results})
            )
        )

    @staticmethod
    def _entry(addr: str, valid: bool = True) -> dict:
        return {
            "proxy_address": addr,
            "port": 80,
            "username": "u",
            "password": "p",
            "valid": valid,
            "country_code": "US",
        }

    def test_skips_invalid_and_rotates(self):
        prov = WebshareProvider(
            token="t",
            http=self._client(
                [
                    self._entry("1.1.1.1", valid=False),
                    self._entry("2.2.2.2"),
                    self._entry("3.3.3.3"),
                ]
            ),
        )
        hosts = [prov.acquire("x").host for _ in range(3)]
        assert hosts == ["2.2.2.2", "3.3.3.3", "2.2.2.2"]

    def test_all_invalid_raises(self):
        prov = WebshareProvider(
            token="t", http=self._client([self._entry("1.1.1.1", valid=False)])
        )
        with pytest.raises(RuntimeError, match="no valid proxies"):
            prov.acquire("x")

    def test_backbone_mode_is_rejected_at_build(self):
        with pytest.raises(ValueError, match="backbone"):
            WebshareProvider(token="t", mode="backbone")


class TestStaticUnsetEnv:
    def test_unset_host_var_is_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("PROXY_HOST_UNSET", raising=False)
        with pytest.raises(ValueError, match="host"):
            ProxyProviderFactory.build(
                {"kind": "static", "host": "${PROXY_HOST_UNSET}", "port": 8080},
                log=logging.getLogger("t"),
            )

    def test_unset_password_var_is_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("PROXY_PASS_UNSET", raising=False)
        with pytest.raises(ValueError, match="password"):
            ProxyProviderFactory.build(
                {
                    "kind": "static",
                    "host": "h",
                    "port": 8080,
                    "username": "u",
                    "password": "${PROXY_PASS_UNSET}",
                },
                log=logging.getLogger("t"),
            )

    def test_literal_password_with_dollar_is_kept(self):
        prov = ProxyProviderFactory.build(
            {
                "kind": "static",
                "host": "h",
                "port": 8080,
                "username": "u",
                "password": "pa$$w0rd",
            },
            log=logging.getLogger("t"),
        )
        assert prov.acquire("x").password == "pa$$w0rd"
