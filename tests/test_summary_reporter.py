"""SummaryReporter — handles missing dir, empty dir, malformed records."""

import json
import logging
from pathlib import Path


from fill import Paths, SummaryReporter


class TestPrint:
    def test_handles_missing_directory(self, tmp_app_root: Path, caplog):
        # Paths.APPLICATIONS doesn't exist yet
        with caplog.at_level(logging.INFO, logger="auto-form"):
            SummaryReporter(root=Paths.APPLICATIONS).print([])
        assert any("does not exist" in r.message for r in caplog.records)

    def test_handles_empty_directory(self, tmp_app_root: Path, caplog):
        Paths.APPLICATIONS.mkdir(parents=True)
        with caplog.at_level(logging.INFO, logger="auto-form"):
            SummaryReporter(root=Paths.APPLICATIONS).print([])
        assert any("is empty" in r.message for r in caplog.records)

    def test_lists_applications_per_portal(self, tmp_app_root: Path, caplog):
        portal_dir = Paths.APPLICATIONS / "acme"
        portal_dir.mkdir(parents=True)
        (portal_dir / "20260513_213833_a.json").write_text(
            json.dumps(
                {
                    "applied_at": "2026-05-13T21:38:33",
                    "job_title": "Senior BE",
                    "salary": "$120k",
                    "location": "Remote",
                }
            )
        )
        with caplog.at_level(logging.INFO, logger="auto-form"):
            SummaryReporter(root=Paths.APPLICATIONS).print(
                [{"name": "acme", "description": "Acme Inc"}]
            )
        msg = "\n".join(r.message for r in caplog.records)
        assert "acme" in msg
        assert "Senior BE" in msg
        assert "Acme Inc" in msg

    def test_skips_malformed_records(self, tmp_app_root: Path, caplog):
        portal_dir = Paths.APPLICATIONS / "acme"
        portal_dir.mkdir(parents=True)
        (portal_dir / "broken.json").write_text("{this is not valid json")
        with caplog.at_level(logging.INFO, logger="auto-form"):
            SummaryReporter(root=Paths.APPLICATIONS).print([])
        # Doesn't raise — total = 0
        assert any("TOTAL: 0" in r.message for r in caplog.records)
