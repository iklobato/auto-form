"""expand_env + _SuppressFilter."""

import logging

import pytest

from fill import _SuppressFilter, expand_env


class TestExpandEnv:
    def test_expands_set_variable(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MY_URL", "https://example.com")
        assert expand_env("$MY_URL/jobs") == "https://example.com/jobs"

    def test_expands_curly_brace_variable(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HOST", "h")
        assert expand_env("https://${HOST}/x") == "https://h/x"

    def test_unset_variable_left_intact(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("UNSET_VAR", raising=False)
        assert expand_env("$UNSET_VAR/x") == "$UNSET_VAR/x"

    def test_empty_string_returns_empty(self):
        assert expand_env("") == ""

    def test_none_input_returns_empty(self):
        assert expand_env(None) == ""

    def test_no_vars_returns_input_unchanged(self):
        assert expand_env("plain string") == "plain string"


class TestSuppressFilter:
    def test_drops_record_matching_fragment(self):
        filt = _SuppressFilter("noise", "junk")
        record = logging.LogRecord("x", logging.INFO, "p", 0,
                                   "this is noise here", None, None)
        assert filt.filter(record) is False

    def test_keeps_record_with_no_fragment_match(self):
        filt = _SuppressFilter("noise")
        record = logging.LogRecord("x", logging.INFO, "p", 0,
                                   "useful message", None, None)
        assert filt.filter(record) is True

    def test_no_fragments_keeps_everything(self):
        filt = _SuppressFilter()
        record = logging.LogRecord("x", logging.INFO, "p", 0,
                                   "anything", None, None)
        assert filt.filter(record) is True

    def test_handles_format_args(self):
        filt = _SuppressFilter("ERROR_X")
        record = logging.LogRecord("x", logging.INFO, "p", 0,
                                   "got %s while running", ("ERROR_X",), None)
        assert filt.filter(record) is False
