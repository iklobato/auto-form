"""CDPDebugger — response status, navigation, JS errors, popups, dialogs."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fill import CDPDebugger, NetworkRecorder, Paths, add_cdp_handler


def _events_lines(portal: str) -> list[dict]:
    """Read the per-portal cdp.jsonl file as a list of dicts."""
    path = Paths.APPLICATIONS / portal / "cdp.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── Response handler ─────────────────────────────────────────────────────────


class TestResponseHandler:
    def test_logs_2xx_at_debug_no_warning(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        d = CDPDebugger("acme", mock_app_logger)
        d._on_request_index(
            {"requestId": "r1", "request": {"method": "GET", "url": "https://acme/x"}}
        )
        d._on_response(
            {
                "requestId": "r1",
                "response": {
                    "url": "https://acme/x",
                    "status": 200,
                    "mimeType": "text/html",
                },
            }
        )
        records = _events_lines("acme")
        assert records[-1]["status"] == 200

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503])
    def test_logs_4xx_5xx_as_warning(
        self, status: int, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        d = CDPDebugger("acme", mock_app_logger)
        d._on_request_index(
            {
                "requestId": "r1",
                "request": {"method": "POST", "url": "https://acme/api"},
            }
        )
        d._on_response(
            {
                "requestId": "r1",
                "response": {
                    "url": "https://acme/api",
                    "status": status,
                    "mimeType": "application/json",
                },
            }
        )
        # Confirms emit() was called for the http_error event
        emit_calls = [
            c.kwargs
            for c in mock_app_logger.emit.call_args_list
            if c.args and c.args[0] == "http_error"
        ]
        assert len(emit_calls) == 1
        assert emit_calls[0]["status"] == status

    def test_drops_noise_responses(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        d = CDPDebugger("acme", mock_app_logger, noise_fragments=("google-analytics",))
        d._on_request_index(
            {
                "requestId": "r1",
                "request": {"method": "GET", "url": "https://google-analytics.com/x"},
            }
        )
        d._on_response(
            {
                "requestId": "r1",
                "response": {
                    "url": "https://google-analytics.com/x",
                    "status": 200,
                    "mimeType": "",
                },
            }
        )
        assert _events_lines("acme") == []

    def test_pairs_request_method_with_response(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        d = CDPDebugger("acme", mock_app_logger)
        d._on_request_index(
            {
                "requestId": "r1",
                "request": {"method": "POST", "url": "https://acme/api/apply"},
            }
        )
        d._on_response(
            {
                "requestId": "r1",
                "response": {
                    "url": "https://acme/api/apply",
                    "status": 201,
                    "mimeType": "application/json",
                },
            }
        )
        rec = _events_lines("acme")[-1]
        assert rec["method"] == "POST"
        assert rec["status"] == 201


# ── Loading-failed handler ───────────────────────────────────────────────────


class TestLoadingFailed:
    def test_logs_real_failure(self, tmp_app_root: Path, mock_app_logger: MagicMock):
        d = CDPDebugger("acme", mock_app_logger)
        d._on_request_index(
            {"requestId": "r1", "request": {"method": "GET", "url": "https://acme/x"}}
        )
        d._on_loading_failed(
            {
                "requestId": "r1",
                "errorText": "net::ERR_NAME_NOT_RESOLVED",
                "canceled": False,
            }
        )
        rec = _events_lines("acme")[-1]
        assert rec["error"] == "net::ERR_NAME_NOT_RESOLVED"
        assert rec["canceled"] is False
        # emitted to events.jsonl
        emit_kinds = [c.args[0] for c in mock_app_logger.emit.call_args_list]
        assert "network_failure" in emit_kinds

    def test_canceled_is_quieter(self, tmp_app_root: Path, mock_app_logger: MagicMock):
        # Cancelled requests are usually deliberate (user navigated away) — log to file but no warning.
        d = CDPDebugger("acme", mock_app_logger)
        d._on_request_index(
            {"requestId": "r1", "request": {"method": "GET", "url": "https://acme/x"}}
        )
        d._on_loading_failed(
            {"requestId": "r1", "errorText": "net::ERR_ABORTED", "canceled": True}
        )
        emit_kinds = [c.args[0] for c in mock_app_logger.emit.call_args_list]
        assert "network_failure" not in emit_kinds


# ── Navigation / popup / dialog ──────────────────────────────────────────────


class TestNavigation:
    def test_main_frame_logged_at_info(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        d = CDPDebugger("acme", mock_app_logger)
        d._on_navigated({"frame": {"url": "https://acme/jobs", "id": "main"}})
        rec = _events_lines("acme")[-1]
        assert rec["url"] == "https://acme/jobs"
        assert rec["is_main_frame"] is True

    def test_subframe_logged_only(self, tmp_app_root: Path, mock_app_logger: MagicMock):
        d = CDPDebugger("acme", mock_app_logger)
        d._on_navigated(
            {
                "frame": {
                    "url": "https://ad.com/iframe",
                    "id": "iframe-1",
                    "parentId": "main",
                }
            }
        )
        rec = _events_lines("acme")[-1]
        assert rec["is_main_frame"] is False


class TestTargetCreated:
    def test_popup_emits_event(self, tmp_app_root: Path, mock_app_logger: MagicMock):
        d = CDPDebugger("acme", mock_app_logger)
        d._on_target_created(
            {"targetInfo": {"type": "popup", "url": "https://oauth.example/login"}}
        )
        emit_kinds = [c.args[0] for c in mock_app_logger.emit.call_args_list]
        assert "popup_created" in emit_kinds

    def test_service_worker_does_not_emit(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        d = CDPDebugger("acme", mock_app_logger)
        d._on_target_created(
            {"targetInfo": {"type": "service_worker", "url": "https://acme/sw.js"}}
        )
        # Persisted to file but not emitted as an interesting event
        assert "popup_created" not in [
            c.args[0] for c in mock_app_logger.emit.call_args_list
        ]


class TestExceptionAndDialog:
    def test_js_exception_logged(self, tmp_app_root: Path, mock_app_logger: MagicMock):
        d = CDPDebugger("acme", mock_app_logger)
        d._on_exception(
            {
                "exceptionDetails": {
                    "text": "Uncaught TypeError",
                    "exception": {"description": "TypeError: x is undefined"},
                }
            }
        )
        emit_kinds = [c.args[0] for c in mock_app_logger.emit.call_args_list]
        assert "js_exception" in emit_kinds

    def test_dialog_logged(self, tmp_app_root: Path, mock_app_logger: MagicMock):
        d = CDPDebugger("acme", mock_app_logger)
        d._on_dialog({"type": "confirm", "message": "Are you sure you want to leave?"})
        emit_kinds = [c.args[0] for c in mock_app_logger.emit.call_args_list]
        assert "js_dialog" in emit_kinds


# ── Attach idempotency ───────────────────────────────────────────────────────


class TestAttach:
    @pytest.mark.asyncio
    async def test_subscribes_to_all_event_streams(
        self, tmp_app_root: Path, mock_app_logger: MagicMock, cdp_browser: MagicMock
    ):
        d = CDPDebugger("acme", mock_app_logger)
        client = (await cdp_browser.get_or_create_cdp_session()).cdp_client

        await d.attach_if_needed(cdp_browser)

        client.send.Network.enable.assert_awaited_once()
        client.send.Page.enable.assert_awaited_once()
        client.send.Runtime.enable.assert_awaited_once()
        assert set(client._event_registry.get_registered_methods()) == {
            "Network.requestWillBeSent",
            "Network.responseReceived",
            "Network.loadingFailed",
            "Page.frameNavigated",
            "Target.targetCreated",
            "Runtime.exceptionThrown",
            "Page.javascriptDialogOpening",
        }

    @pytest.mark.asyncio
    async def test_attach_is_idempotent_per_session(
        self, tmp_app_root: Path, mock_app_logger: MagicMock, cdp_browser: MagicMock
    ):
        d = CDPDebugger("acme", mock_app_logger)
        client = (await cdp_browser.get_or_create_cdp_session()).cdp_client

        await d.attach_if_needed(cdp_browser)
        await d.attach_if_needed(cdp_browser)

        assert client.send.Network.enable.await_count == 1


class TestHandlerCoexistence:
    """cdp_use keeps one handler per event: attaching must not evict anyone."""

    @pytest.mark.asyncio
    async def test_keeps_browser_use_dialog_handler(
        self, tmp_app_root: Path, mock_app_logger: MagicMock, cdp_browser: MagicMock
    ):
        client = (await cdp_browser.get_or_create_cdp_session()).cdp_client
        accepted = []
        client._event_registry.register(
            "Page.javascriptDialogOpening",
            lambda event, sid=None: accepted.append(event),
        )

        await CDPDebugger("acme", mock_app_logger).attach_if_needed(cdp_browser)
        await client._event_registry.handle_event(
            "Page.javascriptDialogOpening", {"type": "confirm", "message": "Submit?"}
        )

        assert accepted == [{"type": "confirm", "message": "Submit?"}]
        assert _events_lines("acme")[-1]["kind"] == "js_dialog"

    @pytest.mark.asyncio
    async def test_network_recorder_still_captures_post_after_debugger_attaches(
        self, tmp_app_root: Path, mock_app_logger: MagicMock, cdp_browser: MagicMock
    ):
        client = (await cdp_browser.get_or_create_cdp_session()).cdp_client
        rec = NetworkRecorder("acme", mock_app_logger)

        await rec.attach_if_needed(cdp_browser)
        await CDPDebugger("acme", mock_app_logger).attach_if_needed(cdp_browser)
        await client._event_registry.handle_event(
            "Network.requestWillBeSent",
            {
                "requestId": "r1",
                "request": {"method": "POST", "url": "https://acme/apply"},
            },
        )

        assert [c["url"] for c in rec.recent] == ["https://acme/apply"]

    @pytest.mark.asyncio
    async def test_failing_subscriber_does_not_block_the_next(
        self, tmp_app_root: Path, mock_app_logger: MagicMock, cdp_browser: MagicMock
    ):
        client = (await cdp_browser.get_or_create_cdp_session()).cdp_client
        seen = []

        def broken(event, sid=None):
            raise KeyError("boom")

        add_cdp_handler(client, "Page.frameNavigated", broken)
        add_cdp_handler(
            client, "Page.frameNavigated", lambda e, sid=None: seen.append(e)
        )
        await client._event_registry.handle_event("Page.frameNavigated", {"frame": {}})

        assert seen == [{"frame": {}}]
