"""TaskBuilder — template rendering, primary URL pick, login line, dry-run, expansion."""

import pytest

from fill import RunOptions, TaskBuilder


class TestPrimaryUrlSelection:
    def test_search_url_overrides_homepage_when_different(
        self, task_builder: TaskBuilder, sample_system: dict
    ):
        task = task_builder.build(sample_system, "K", RunOptions())
        assert "URL=https://acme.example.com/jobs?q=python" in task

    def test_falls_back_to_url_when_no_search_url(
        self, task_builder: TaskBuilder
    ):
        sys = {"name": "x", "url": "https://x.example.com/"}
        task = task_builder.build(sys, "K", RunOptions())
        assert "URL=https://x.example.com/" in task


class TestLoginLine:
    def test_present_when_credentials_exist(
        self, task_builder: TaskBuilder, sample_system: dict
    ):
        task = task_builder.build(sample_system, "K", RunOptions())
        assert "First, go to" in task
        assert "<username>" in task
        assert "<password>" in task

    def test_absent_without_credentials(self, task_builder: TaskBuilder):
        sys = {"name": "x", "url": "https://x.example.com/"}
        task = task_builder.build(sys, "K", RunOptions())
        assert "First, go to" not in task


class TestDryRun:
    def test_appends_dry_run_warning(self, task_builder: TaskBuilder, sample_system: dict):
        task = task_builder.build(sample_system, "K", RunOptions(dry_run=True))
        assert "Do NOT click Apply" in task

    def test_no_warning_when_apply_mode(self, task_builder: TaskBuilder, sample_system: dict):
        task = task_builder.build(sample_system, "K", RunOptions(dry_run=False))
        assert "Do NOT click Apply" not in task


class TestKeywordFilter:
    def test_filter_keyword_renders(self, task_builder: TaskBuilder, sample_system: dict):
        task = task_builder.build(sample_system, "K", RunOptions(filter_keyword="backend"))
        assert "backend" in task and "Only consider roles" in task


class TestEnvExpansion:
    def test_expands_env_vars_in_url(self, task_builder: TaskBuilder,
                                     monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACME_URL", "https://expanded.example.com/")
        sys = {"name": "x", "url": "$ACME_URL"}
        task = task_builder.build(sys, "K", RunOptions())
        assert "https://expanded.example.com/" in task


class TestResume:
    def test_resume_path_threaded_through(self, task_builder: TaskBuilder, sample_system: dict):
        task = task_builder.build(sample_system, "K",
                                  RunOptions(resume_path="/tmp/cv.pdf"))
        assert "/tmp/cv.pdf" in task

    def test_default_inner_when_no_resume(self, task_builder: TaskBuilder, sample_system: dict):
        task = task_builder.build(sample_system, "K", RunOptions())
        assert "no resume path configured" in task


class TestKnowledgeInjection:
    def test_knowledge_appears(self, task_builder: TaskBuilder, sample_system: dict):
        task = task_builder.build(sample_system, "MY KNOWLEDGE PAYLOAD", RunOptions())
        assert "MY KNOWLEDGE PAYLOAD" in task


class TestDefaultFilterNotes:
    def test_uses_default_when_system_has_none(self, task_builder: TaskBuilder):
        sys = {"name": "x", "url": "https://x.example.com/"}
        task = task_builder.build(sys, "K", RunOptions())
        # The DEFAULT_FILTER_NOTES contains "Product Manager" — proves default fired
        assert "Product Manager" in task

    def test_portal_filter_notes_override_default(
        self, task_builder: TaskBuilder, sample_system: dict
    ):
        # sample_system sets filter_notes="Skip Product Manager, Designer."
        task = task_builder.build(sample_system, "K", RunOptions())
        assert "Skip Product Manager, Designer." in task
