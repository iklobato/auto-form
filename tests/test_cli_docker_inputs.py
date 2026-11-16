"""CLI inputs for the Docker image: --send, --profile and the data folder."""

import logging
import os
import shlex
import sys
from pathlib import Path

import pytest

from fill import CLI

EXAMPLE = Path(__file__).parent.parent / "profile.example.yaml"
REPO = Path(__file__).parent.parent


class TestSend:
    # A forgotten flag must never send real applications under someone's name.
    def test_without_send_it_is_a_dry_run(self) -> None:
        args = CLI._build_parser().parse_args(["--all"])
        assert CLI._build_opts(args).dry_run is True

    def test_send_turns_the_dry_run_off(self) -> None:
        args = CLI._build_parser().parse_args(["--all", "--send"])
        assert CLI._build_opts(args).dry_run is False

    def test_dry_run_and_send_together_are_refused(self) -> None:
        with pytest.raises(SystemExit):
            CLI._build_parser().parse_args(["--all", "--send", "--dry-run"])


class TestProfileFlag:
    def test_profile_yaml_becomes_the_knowledge(self) -> None:
        args = CLI._build_parser().parse_args(["--all", "--profile", str(EXAMPLE)])
        knowledge = CLI.knowledge(args, logging.getLogger("t"))
        assert "| First name | Ada |" in knowledge

    def test_the_profile_values_reach_the_run_options(self) -> None:
        args = CLI._build_parser().parse_args(["--all", "--profile", str(EXAMPLE)])
        assert CLI._build_opts(args).profile_fields["first_name"] == "Ada"

    def test_a_bad_profile_stops_with_its_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "profile.yaml"
        bad.write_text("identity: {}\n")
        args = CLI._build_parser().parse_args(["--all", "--profile", str(bad)])
        with pytest.raises(SystemExit, match="identity.first_name"):
            CLI._build_opts(args)


class TestFreshDataFolder:
    # First Docker run on 2026-11-16: /data/out did not exist yet and fill.py died
    # with FileNotFoundError: '/data/out/logs' before reading anything.
    def test_a_run_creates_the_missing_folders(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "new" / "out"
        log = tmp_path / "run.txt"
        command = (
            f"cd {shlex.quote(str(REPO))} && AUTO_FORM_DATA={shlex.quote(str(out_dir))} "
            f"{shlex.quote(sys.executable)} fill.py --list --systems portals/no-login.yaml"
            f" > {shlex.quote(str(log))} 2>&1"
        )
        assert os.system(command) == 0, log.read_text()
        assert (out_dir / "logs").is_dir()


class TestDataFolder:
    # Paths are set when fill is imported, so this runs a fresh interpreter.
    def test_outputs_follow_auto_form_data(self, tmp_path: Path) -> None:
        out = tmp_path / "printed.txt"
        code = "import fill; print(fill.Paths.APPLIED); print(fill.Paths.TASK_TEMPLATE)"
        command = (
            f"cd {shlex.quote(str(REPO))} && AUTO_FORM_DATA={shlex.quote(str(tmp_path))} "
            f"{shlex.quote(sys.executable)} -c {shlex.quote(code)} > {shlex.quote(str(out))} 2>&1"
        )
        assert os.system(command) == 0
        applied, template = out.read_text().split()
        assert applied == str(tmp_path / "applied.jsonl")
        assert template == str(REPO / "prompts" / "task.md")
