"""Shared fixtures. Isolate every test from disk state, env vars, and the network."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from cdp_use.cdp.registry import EventRegistry

import fill
from fill import (
    Agent,
    AppLogger,
    ApplicationRecorder,
    CredentialsResolver,
    JsonlTracker,
    KnowledgeBase,
    Paths,
    RunOptions,
    TaskBuilder,
    YamlStore,
)


# ── tmp directory + path patching ────────────────────────────────────────────

@pytest.fixture
def tmp_app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Re-point every `Paths.*` constant inside an isolated tmp directory."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (tmp_path / "prompts").mkdir()
    template = tmp_path / "prompts" / "task.md"
    template.write_text(_minimal_template())

    monkeypatch.setattr(Paths, "ROOT", tmp_path)
    monkeypatch.setattr(Paths, "KNOWLEDGE", knowledge)
    monkeypatch.setattr(Paths, "TASK_TEMPLATE", template)
    monkeypatch.setattr(Paths, "SCREENSHOTS", tmp_path / "screenshots")
    monkeypatch.setattr(Paths, "PROFILES", tmp_path / "browser_profiles")
    monkeypatch.setattr(Paths, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(Paths, "APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(Paths, "APPLIED", tmp_path / "applied.jsonl")
    monkeypatch.setattr(Paths, "ATTEMPTED", tmp_path / "attempted.jsonl")
    monkeypatch.setattr(Paths, "NOTES", knowledge / "07_agent_notes.md")
    return tmp_path


def _minimal_template() -> str:
    """A task.md template containing every placeholder our renderer uses."""
    return (
        "{login_line}URL={url}\n"
        "{search_url_line}{best_for_line}{account_line}\n"
        "SEARCH={search_instructions}\nFILTER={filter_notes_line}\n"
        "FORM={form_hints}\nAPP_NOTES={application_notes_line}\n"
        "RESUME={resume_line} INNER={resume_line_inner}\n"
        "FILTER_LINE={filter_line}\nNOTE_LINE={note_line}\n"
        "KNOWLEDGE={knowledge}\n"
    )


# ── env ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Strip all auto-form env vars so each test starts from a known state."""
    for key in (
        "OPENROUTER_TOKEN", "OPENROUTER_MODEL", "ANTHROPIC_API_KEY",
        "PORTAL_ACCOUNT_PASSWORD", "LINKEDIN_SSO_USERNAME", "LINKEDIN_SSO_PASSWORD",
        "RESUME_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


# ── logger ───────────────────────────────────────────────────────────────────

@pytest.fixture
def silent_logger() -> logging.Logger:
    """A real Logger that doesn't emit anywhere — useful for components that need a Logger."""
    logger = logging.getLogger("test")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    return logger


@pytest.fixture
def mock_app_logger(tmp_app_root: Path) -> MagicMock:
    """A MagicMock spec'd to AppLogger — captures `emit()` calls and returns real loggers."""
    mock = MagicMock(spec=AppLogger)
    mock.run_id = "test-run"
    mock.events_file = tmp_app_root / "logs" / "events.jsonl"
    mock.events_file.parent.mkdir(parents=True, exist_ok=True)
    mock.main.return_value = logging.getLogger("test.main")
    mock.portal.side_effect = lambda name: logging.getLogger(f"test.portal.{name}")
    return mock


# ── sample data ──────────────────────────────────────────────────────────────

@pytest.fixture
def sample_system() -> dict:
    """Realistic portal config used by tests that need a system dict."""
    return {
        "name": "acme",
        "url": "https://acme.example.com/",
        "search_url": "https://acme.example.com/jobs?q=python",
        "description": "Acme — example portal",
        "search_instructions": "Browse all roles.",
        "form_hints": "Fill all visible fields.",
        "filter_notes": "Skip Product Manager, Designer.",
        "application_notes": "Confirm remote.",
        "credentials": {"username": "user@example.com", "password": "secret"},
    }


@pytest.fixture
def systems_yaml_file(tmp_path: Path, sample_system: dict) -> Path:
    """Writes a temp systems.yaml with one portal and returns the path."""
    path = tmp_path / "systems.yaml"
    path.write_text(yaml.dump({"systems": [sample_system]}, sort_keys=False))
    return path


# ── trackers / stores ────────────────────────────────────────────────────────

@pytest.fixture
def applied_tracker(tmp_app_root: Path) -> JsonlTracker:
    return JsonlTracker(Paths.APPLIED)


@pytest.fixture
def attempted_tracker(tmp_app_root: Path) -> JsonlTracker:
    return JsonlTracker(Paths.ATTEMPTED)


@pytest.fixture
def recorder(tmp_app_root: Path) -> ApplicationRecorder:
    return ApplicationRecorder(root=Paths.APPLICATIONS)


@pytest.fixture
def yaml_store(systems_yaml_file: Path) -> YamlStore:
    return YamlStore(systems_yaml_file)


@pytest.fixture
def knowledge_base(tmp_app_root: Path, silent_logger: logging.Logger) -> KnowledgeBase:
    (Paths.KNOWLEDGE / "01_personal.md").write_text("Name: Test Candidate")
    (Paths.KNOWLEDGE / "02_work.md").write_text("Senior Backend Engineer")
    return KnowledgeBase(dir_path=Paths.KNOWLEDGE, log=silent_logger)


@pytest.fixture
def task_builder(tmp_app_root: Path, silent_logger: logging.Logger) -> TaskBuilder:
    return TaskBuilder(template_path=Paths.TASK_TEMPLATE, log=silent_logger)


@pytest.fixture
def cred_resolver(silent_logger: logging.Logger) -> CredentialsResolver:
    return CredentialsResolver(log=silent_logger)


@pytest.fixture
def run_opts() -> RunOptions:
    return RunOptions(dry_run=False, headless=True, fast=True, max_actions=5)


# ── browser_use / Agent mocks ────────────────────────────────────────────────

class FakeRegistry:
    """Captures `@registry.action(...)` decorations so tests can invoke them by name."""

    def __init__(self):
        self.actions: dict[str, callable] = {}

    def action(self, description: str):
        def decorator(func):
            self.actions[func.__name__] = func
            return func
        return decorator


@pytest.fixture
def fake_registry() -> FakeRegistry:
    return FakeRegistry()


@pytest.fixture
def mock_agent(fake_registry: FakeRegistry) -> MagicMock:
    """Agent stand-in whose `tools.registry.action` collects the decorated functions."""
    agent = MagicMock(spec=Agent)
    agent.tools = MagicMock()
    agent.tools.registry = fake_registry
    return agent


@pytest.fixture
def cdp_browser() -> MagicMock:
    """browser_session stand-in whose CDP client uses cdp_use's real registry,
    which keeps a single handler per event name across all sessions."""
    client = MagicMock()
    client._event_registry = EventRegistry()
    for domain in ("Network", "Page", "Runtime"):
        getattr(client.send, domain).enable = AsyncMock()
    client.send.Target.setDiscoverTargets = AsyncMock()
    session = MagicMock(session_id="s1", cdp_client=client)
    browser = MagicMock()
    browser.get_or_create_cdp_session = AsyncMock(return_value=session)
    return browser
