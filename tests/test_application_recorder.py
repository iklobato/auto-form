"""ApplicationRecorder — slugify, JSON snapshot, INDEX.md table generation."""

import json
from pathlib import Path

import pytest

from fill import ApplicationRecorder, Paths


class TestSlugify:
    @pytest.mark.parametrize("text,expected", [
        ("Senior Backend Engineer", "senior_backend_engineer"),
        ("Hello, World!", "hello_world"),
        ("___leading_trailing___", "leading_trailing"),
        ("multiple    spaces", "multiple_spaces"),
        ("", "untitled"),
        ("!!!", "untitled"),
    ])
    def test_basic_cases(self, text: str, expected: str):
        assert ApplicationRecorder._slugify(text) == expected

    def test_truncates_to_maxlen(self):
        long = "a" * 100
        out = ApplicationRecorder._slugify(long, maxlen=10)
        assert len(out) == 10


class TestWrite:
    def _record(self, **overrides) -> dict:
        base = {
            "portal": "acme",
            "job_url": "https://acme/jobs/1",
            "job_title": "Senior Backend Engineer",
            "salary": "$120k–$160k",
            "location": "Remote",
            "applied_at": "2026-05-13T21:38:33",
        }
        base.update(overrides)
        return base

    def test_creates_portal_directory(self, recorder: ApplicationRecorder):
        recorder.write(self._record())
        assert (Paths.APPLICATIONS / "acme").is_dir()

    def test_writes_json_snapshot(self, recorder: ApplicationRecorder):
        path = recorder.write(self._record())
        loaded = json.loads(path.read_text())
        assert loaded["job_title"] == "Senior Backend Engineer"
        assert loaded["salary"] == "$120k–$160k"

    def test_filename_includes_timestamp_and_slug(self, recorder: ApplicationRecorder):
        path = recorder.write(self._record())
        assert "20260513_213833" in path.name
        assert "senior_backend_engineer" in path.name

    def test_creates_index_with_header_first_time(self, recorder: ApplicationRecorder):
        recorder.write(self._record())
        index = (Paths.APPLICATIONS / "acme" / "INDEX.md").read_text()
        assert "# Applications — acme" in index
        assert "| When | Title | Salary | Location | URL | Record |" in index

    def test_appends_to_index_without_duplicate_header(
        self, recorder: ApplicationRecorder
    ):
        recorder.write(self._record(job_title="A"))
        recorder.write(self._record(job_title="B",
                                    applied_at="2026-05-13T21:40:00"))
        index = (Paths.APPLICATIONS / "acme" / "INDEX.md").read_text()
        assert index.count("# Applications — acme") == 1
        assert "A" in index and "B" in index

    def test_escapes_pipes_in_title(self, recorder: ApplicationRecorder):
        recorder.write(self._record(job_title="Senior | Backend"))
        index = (Paths.APPLICATIONS / "acme" / "INDEX.md").read_text()
        assert "Senior \\| Backend" in index

    def test_em_dash_for_missing_optional_fields(
        self, recorder: ApplicationRecorder
    ):
        rec = self._record()
        rec["salary"] = ""
        rec["location"] = ""
        recorder.write(rec)
        index = (Paths.APPLICATIONS / "acme" / "INDEX.md").read_text()
        assert "—" in index
