"""LLMFactory — token check, model resolution, env hygiene."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from fill import LLMFactory


class TestCreate:
    def test_raises_without_token(self, clean_env: pytest.MonkeyPatch):
        with pytest.raises(RuntimeError, match="OPENROUTER_TOKEN"):
            LLMFactory.create()

    def test_explicit_model_wins(self, clean_env: pytest.MonkeyPatch):
        clean_env.setenv("OPENROUTER_TOKEN", "token")
        clean_env.setenv("OPENROUTER_MODEL", "should-not-win")
        with patch("fill.ChatOpenRouter") as mock_chat:
            LLMFactory.create(model="explicit-model")
        kwargs = mock_chat.call_args.kwargs
        assert kwargs["model"] == "explicit-model"

    def test_env_model_used_when_no_explicit(self, clean_env: pytest.MonkeyPatch):
        clean_env.setenv("OPENROUTER_TOKEN", "token")
        clean_env.setenv("OPENROUTER_MODEL", "from-env")
        with patch("fill.ChatOpenRouter") as mock_chat:
            LLMFactory.create()
        assert mock_chat.call_args.kwargs["model"] == "from-env"

    def test_default_model_used_as_last_resort(self, clean_env: pytest.MonkeyPatch):
        clean_env.setenv("OPENROUTER_TOKEN", "token")
        with patch("fill.ChatOpenRouter") as mock_chat:
            LLMFactory.create()
        assert mock_chat.call_args.kwargs["model"] == LLMFactory.DEFAULT_MODEL

    def test_strips_anthropic_api_key(self, clean_env: pytest.MonkeyPatch):
        # OAuth tokens conflict with the SDK auth path — must be removed.
        clean_env.setenv("OPENROUTER_TOKEN", "token")
        clean_env.setenv("ANTHROPIC_API_KEY", "leaked-key")
        import os
        with patch("fill.ChatOpenRouter"):
            LLMFactory.create()
        assert "ANTHROPIC_API_KEY" not in os.environ

    def test_passes_api_key_to_client(self, clean_env: pytest.MonkeyPatch):
        clean_env.setenv("OPENROUTER_TOKEN", "my-token-123")
        with patch("fill.ChatOpenRouter") as mock_chat:
            LLMFactory.create()
        assert mock_chat.call_args.kwargs["api_key"] == "my-token-123"

    def test_disables_internal_retries(self, clean_env: pytest.MonkeyPatch):
        # We do retries inside BackoffTransport — ChatOpenRouter must not double-retry.
        clean_env.setenv("OPENROUTER_TOKEN", "t")
        with patch("fill.ChatOpenRouter") as mock_chat:
            LLMFactory.create()
        assert mock_chat.call_args.kwargs["max_retries"] == 0
