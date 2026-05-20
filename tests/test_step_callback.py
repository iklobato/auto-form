"""StepCallback — log line, screenshot persistence, action-name extraction."""

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from browser_use.browser.views import BrowserStateSummary

from fill import Paths, StepCallback


def _png_b64() -> str:
    """1×1 transparent PNG, base64-encoded."""
    raw = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01"
           b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    return base64.b64encode(raw).decode()


class TestActionNameExtraction:
    def test_extracts_from_pydantic_actions(self):
        # When agent_output.action is a list of pydantic-like objects with model_dump.
        action = MagicMock()
        action.model_dump.return_value = {"click": {"index": 5}, "wait": None}
        names = StepCallback._extract_action_names(MagicMock(action=[action]))
        # `wait` is None → excluded (exclude_none in real model_dump)
        assert names == ["click", "wait"]  # MagicMock returns full dict; we test the wiring

    def test_extracts_from_dict_actions(self):
        out = MagicMock(action=[{"click": {"index": 1}, "scroll": None}])
        names = StepCallback._extract_action_names(out)
        assert "click" in names

    def test_handles_empty_action_list(self):
        out = MagicMock(action=[])
        assert StepCallback._extract_action_names(out) == []

    def test_handles_none_action_attr(self):
        out = MagicMock(action=None)
        assert StepCallback._extract_action_names(out) == []


class TestOnStep:
    @pytest.mark.asyncio
    async def test_saves_screenshot_when_present(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        cb = StepCallback("acme", mock_app_logger)
        state = MagicMock(spec=BrowserStateSummary)
        state.url = "https://acme/jobs"
        state.screenshot = _png_b64()
        agent_output = MagicMock(action=[], next_goal="apply", evaluation_previous_goal="")

        await cb.on_step(state, agent_output, step_num=1)

        files = list((Paths.SCREENSHOTS / "acme").glob("step0001_*.png"))
        assert len(files) == 1
        assert files[0].read_bytes().startswith(b"\x89PNG")

    @pytest.mark.asyncio
    async def test_no_screenshot_no_file(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        cb = StepCallback("acme", mock_app_logger)
        state = MagicMock(spec=BrowserStateSummary)
        state.url = "https://acme/jobs"
        state.screenshot = None
        await cb.on_step(state, MagicMock(action=[], next_goal="", evaluation_previous_goal=""),
                         step_num=1)
        assert list((Paths.SCREENSHOTS / "acme").glob("*.png")) == []

    @pytest.mark.asyncio
    async def test_emits_step_event(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        cb = StepCallback("acme", mock_app_logger)
        state = MagicMock(spec=BrowserStateSummary)
        state.url = "https://acme/x"
        state.screenshot = None
        await cb.on_step(state, MagicMock(action=[], next_goal="g", evaluation_previous_goal=""),
                         step_num=42)
        mock_app_logger.emit.assert_called_once()
        kwargs = mock_app_logger.emit.call_args.kwargs
        assert kwargs["portal"] == "acme"
        assert kwargs["step"] == 42
        assert kwargs["url"] == "https://acme/x"

    @pytest.mark.asyncio
    async def test_unknown_url_default(
        self, tmp_app_root: Path, mock_app_logger: MagicMock
    ):
        cb = StepCallback("acme", mock_app_logger)
        state = MagicMock(spec=BrowserStateSummary)
        state.url = None
        state.screenshot = None
        await cb.on_step(state, MagicMock(action=[], next_goal="", evaluation_previous_goal=""),
                         step_num=1)
        assert mock_app_logger.emit.call_args.kwargs["url"] == "unknown"
