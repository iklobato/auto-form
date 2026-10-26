"""JsonlTracker — load_keys, append, malformed lines, concurrent appends."""

import asyncio
import json
from pathlib import Path

import pytest

from fill import JsonlTracker


class TestLoadKeys:
    def test_returns_empty_set_when_file_missing(self, tmp_path: Path):
        t = JsonlTracker(tmp_path / "nope.jsonl")
        assert t.load_keys() == set()

    def test_extracts_default_key(self, tmp_path: Path):
        f = tmp_path / "x.jsonl"
        f.write_text(
            json.dumps({"job_url": "https://a"})
            + "\n"
            + json.dumps({"job_url": "https://b"})
            + "\n"
        )
        assert JsonlTracker(f).load_keys() == {"https://a", "https://b"}

    def test_skips_malformed_lines(self, tmp_path: Path):
        f = tmp_path / "x.jsonl"
        f.write_text(
            json.dumps({"job_url": "https://a"})
            + "\n"
            + "not-json\n"
            + json.dumps({"missing_key": "x"})
            + "\n"
            + json.dumps({"job_url": "https://b"})
            + "\n"
        )
        assert JsonlTracker(f).load_keys() == {"https://a", "https://b"}

    def test_skips_blank_lines(self, tmp_path: Path):
        f = tmp_path / "x.jsonl"
        f.write_text("\n" + json.dumps({"job_url": "x"}) + "\n\n")
        assert JsonlTracker(f).load_keys() == {"x"}

    def test_supports_alternate_key(self, tmp_path: Path):
        f = tmp_path / "x.jsonl"
        f.write_text(json.dumps({"id": "abc"}) + "\n")
        assert JsonlTracker(f).load_keys(key="id") == {"abc"}


class TestAppend:
    @pytest.mark.asyncio
    async def test_writes_one_line_per_record(self, tmp_path: Path):
        f = tmp_path / "x.jsonl"
        t = JsonlTracker(f)
        await t.append({"job_url": "u1"})
        await t.append({"job_url": "u2"})
        assert f.read_text().count("\n") == 2

    @pytest.mark.asyncio
    async def test_round_trip_load(self, tmp_path: Path):
        t = JsonlTracker(tmp_path / "x.jsonl")
        await t.append({"job_url": "x", "extra": 42})
        assert t.load_keys() == {"x"}

    @pytest.mark.asyncio
    async def test_concurrent_appends_no_corruption(self, tmp_path: Path):
        # Two coroutines appending in parallel must produce two valid lines, in any order.
        t = JsonlTracker(tmp_path / "x.jsonl")
        await asyncio.gather(*[t.append({"job_url": f"u{i}"}) for i in range(20)])
        assert len(t.load_keys()) == 20

    @pytest.mark.asyncio
    async def test_unicode_preserved(self, tmp_path: Path):
        t = JsonlTracker(tmp_path / "x.jsonl")
        await t.append({"job_url": "u", "title": "Engenheiro – Sênior"})
        line = (tmp_path / "x.jsonl").read_text()
        assert "Engenheiro – Sênior" in line
