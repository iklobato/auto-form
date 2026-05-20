"""NetworkRecorder — POST capture, noise filtering, body parsing, ring buffer."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from fill import NetworkRecorder, Paths


# ── Filtering ────────────────────────────────────────────────────────────────

class TestFiltering:
    def test_ignores_get_requests(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        rec._on_request({
            "request": {"method": "GET", "url": "https://acme/api"}
        })
        assert rec.recent == []

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
    def test_captures_interesting_methods(
        self, method: str, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        rec._on_request({
            "request": {"method": method, "url": "https://acme/api/apply",
                        "postData": '{"name":"Henrique"}',
                        "headers": {"Content-Type": "application/json"}}
        })
        assert len(rec.recent) == 1
        assert rec.recent[0]["method"] == method

    @pytest.mark.parametrize("noisy_url", [
        "https://www.google-analytics.com/collect",
        "https://x.googletagmanager.com/gtm.js",
        "https://api.segment.io/v1/track",
        "https://stats.doubleclick.net/x",
        "https://app.intercom.io/messages",
        "https://www.facebook.com/tr/?ev=PageView",
        "https://acme.com/beacon/track",
        "https://app.convertkit.com/forms/123/visit",
        "https://www.google.com/recaptcha/api2/clr",
        "https://acme.com/cdn-cgi/rum",
        "https://www.google.com/rmkt/collect/123",
    ])
    def test_drops_noise_domains(
        self, noisy_url: str, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        rec._on_request({
            "request": {"method": "POST", "url": noisy_url,
                        "postData": "ignored", "headers": {}}
        })
        assert rec.recent == []

    def test_keeps_real_form_post(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        rec._on_request({
            "request": {"method": "POST",
                        "url": "https://boards.greenhouse.io/acme/applications",
                        "postData": "first_name=Henrique&last_name=Lobato",
                        "headers": {"content-type": "application/x-www-form-urlencoded"}}
        })
        assert len(rec.recent) == 1
        assert "greenhouse.io" in rec.recent[0]["url"]


# ── Body parsing ─────────────────────────────────────────────────────────────

class TestBodyParsing:
    def test_parses_json_body(self):
        body = '{"first_name":"Henrique","email":"x@y.com"}'
        parsed = NetworkRecorder._parse_body(body, {"Content-Type": "application/json"})
        assert parsed == {"first_name": "Henrique", "email": "x@y.com"}

    def test_parses_form_urlencoded(self):
        body = "first_name=Henrique&last_name=Lobato"
        parsed = NetworkRecorder._parse_body(
            body, {"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert parsed == {"first_name": ["Henrique"], "last_name": ["Lobato"]}

    def test_returns_none_for_unknown_ctype(self):
        parsed = NetworkRecorder._parse_body("raw bytes",
                                             {"Content-Type": "image/png"})
        assert parsed is None

    def test_returns_none_for_malformed_json(self):
        parsed = NetworkRecorder._parse_body("{invalid",
                                             {"Content-Type": "application/json"})
        assert parsed is None

    def test_returns_none_for_empty_body(self):
        assert NetworkRecorder._parse_body("", {}) is None

    def test_handles_lowercase_content_type_header(self):
        body = '{"x":1}'
        parsed = NetworkRecorder._parse_body(body, {"content-type": "application/json"})
        assert parsed == {"x": 1}


# ── Persistence ──────────────────────────────────────────────────────────────

class TestPersistence:
    def test_writes_capture_to_disk(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        rec._on_request({
            "request": {"method": "POST", "url": "https://acme/api/apply",
                        "postData": '{"x":1}',
                        "headers": {"Content-Type": "application/json"}}
        })
        files = list((Paths.APPLICATIONS / "acme" / "network").glob("*_post.json"))
        assert len(files) == 1
        loaded = json.loads(files[0].read_text())
        assert loaded["url"] == "https://acme/api/apply"
        assert loaded["parsed"] == {"x": 1}

    def test_caps_body_size(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        huge_body = "x" * 20000
        rec._on_request({
            "request": {"method": "POST", "url": "https://acme/api",
                        "postData": huge_body, "headers": {}}
        })
        cap = rec.recent[0]
        assert len(cap["body"]) == NetworkRecorder.BODY_CAP_BYTES
        assert cap["body_truncated"] is True
        assert cap["body_size"] == 20000


# ── Ring buffer ──────────────────────────────────────────────────────────────

class TestRingBuffer:
    def test_keeps_at_most_ring_buffer_size(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        for i in range(NetworkRecorder.RING_BUFFER_SIZE + 10):
            rec._on_request({
                "request": {"method": "POST", "url": f"https://acme/api/{i}",
                            "postData": "", "headers": {}}
            })
        assert len(rec.recent) == NetworkRecorder.RING_BUFFER_SIZE
        # First-in URLs evicted, latest preserved
        urls = [r["url"] for r in rec.recent]
        assert "https://acme/api/0" not in urls
        assert f"https://acme/api/{NetworkRecorder.RING_BUFFER_SIZE + 9}" in urls


# ── CDP attach ───────────────────────────────────────────────────────────────

class TestAttach:
    @pytest.mark.asyncio
    async def test_attach_subscribes_to_network_event(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        # Build a mock browser_session whose CDP API exposes Network.enable + register
        session = MagicMock()
        session.session_id = "sess-123"
        session.cdp_client = MagicMock()
        session.cdp_client.send = MagicMock()
        session.cdp_client.send.Network = MagicMock()
        session.cdp_client.send.Network.enable = AsyncMock()
        session.cdp_client.register = MagicMock()
        session.cdp_client.register.Network = MagicMock()
        session.cdp_client.register.Network.requestWillBeSent = MagicMock()
        browser = MagicMock()
        browser.get_or_create_cdp_session = AsyncMock(return_value=session)

        await rec.attach_if_needed(browser)

        session.cdp_client.send.Network.enable.assert_awaited_once_with(session_id="sess-123")
        session.cdp_client.register.Network.requestWillBeSent.assert_called_once()

    @pytest.mark.asyncio
    async def test_attach_is_idempotent_per_session(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        session = MagicMock()
        session.session_id = "sess-abc"
        session.cdp_client = MagicMock()
        session.cdp_client.send.Network.enable = AsyncMock()
        session.cdp_client.register.Network.requestWillBeSent = MagicMock()
        browser = MagicMock()
        browser.get_or_create_cdp_session = AsyncMock(return_value=session)

        await rec.attach_if_needed(browser)
        await rec.attach_if_needed(browser)
        await rec.attach_if_needed(browser)

        # Subscribe should only happen the first time
        assert session.cdp_client.send.Network.enable.await_count == 1
        assert session.cdp_client.register.Network.requestWillBeSent.call_count == 1

    @pytest.mark.asyncio
    async def test_attach_with_none_session_is_noop(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        await rec.attach_if_needed(None)  # no exception


class TestRequestIdDedupe:
    def test_duplicate_request_id_dropped(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        # CDP can fire requestWillBeSent twice for the same request when there
        # are both main-page and sub-frame handlers. Only the first should be kept.
        rec = NetworkRecorder("acme", mock_app_logger)
        event = {
            "requestId": "req-123",
            "request": {"method": "POST", "url": "https://acme/api/x",
                        "postData": "x=1", "headers": {}}
        }
        rec._on_request(event)
        rec._on_request(event)
        rec._on_request(event)
        assert len(rec.recent) == 1

    def test_different_request_ids_both_kept(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        rec = NetworkRecorder("acme", mock_app_logger)
        rec._on_request({
            "requestId": "req-A",
            "request": {"method": "POST", "url": "https://acme/api/x",
                        "postData": "", "headers": {}}
        })
        rec._on_request({
            "requestId": "req-B",
            "request": {"method": "POST", "url": "https://acme/api/x",
                        "postData": "", "headers": {}}
        })
        assert len(rec.recent) == 2
