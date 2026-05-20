"""KnowledgeBase — alphabetical loading, README skip, caching."""

import logging
from pathlib import Path

from fill import KnowledgeBase, Paths


class TestKnowledgeBaseLoad:
    def test_loads_alphabetically(self, tmp_app_root: Path, silent_logger: logging.Logger):
        (Paths.KNOWLEDGE / "02_b.md").write_text("BBB")
        (Paths.KNOWLEDGE / "01_a.md").write_text("AAA")
        kb = KnowledgeBase(dir_path=Paths.KNOWLEDGE, log=silent_logger)
        text = kb.load()
        assert text.index("AAA") < text.index("BBB")

    def test_skips_readme_md(self, tmp_app_root: Path, silent_logger: logging.Logger):
        (Paths.KNOWLEDGE / "README.md").write_text("README CONTENT")
        (Paths.KNOWLEDGE / "01.md").write_text("REAL CONTENT")
        kb = KnowledgeBase(dir_path=Paths.KNOWLEDGE, log=silent_logger)
        text = kb.load()
        assert "README CONTENT" not in text
        assert "REAL CONTENT" in text

    def test_separator_between_files(self, tmp_app_root: Path, silent_logger: logging.Logger):
        (Paths.KNOWLEDGE / "01.md").write_text("A")
        (Paths.KNOWLEDGE / "02.md").write_text("B")
        kb = KnowledgeBase(dir_path=Paths.KNOWLEDGE, log=silent_logger)
        assert "\n\n---\n\n" in kb.load()

    def test_caches_after_first_load(self, tmp_app_root: Path, silent_logger: logging.Logger):
        f = Paths.KNOWLEDGE / "01.md"
        f.write_text("first")
        kb = KnowledgeBase(dir_path=Paths.KNOWLEDGE, log=silent_logger)
        first = kb.load()
        f.write_text("second — disk changed")
        assert kb.load() == first  # cache wins, doesn't re-read

    def test_empty_directory_returns_empty_string(
        self, tmp_app_root: Path, silent_logger: logging.Logger
    ):
        kb = KnowledgeBase(dir_path=Paths.KNOWLEDGE, log=silent_logger)
        assert kb.load() == ""
