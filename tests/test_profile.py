"""profile.yaml: a validated, structured profile rendered to the prompt's Markdown."""

from pathlib import Path

import pytest
import yaml

from fill import Profile, ProfileError, ProfileFile

EXAMPLE = Path(__file__).parent.parent / "profile.example.yaml"


class ProfileWriter:
    """Writes a profile.yaml built from the shipped example, with changes."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def write(self, changes: dict) -> Path:
        profile = yaml.safe_load(EXAMPLE.read_text())
        for section, fields in changes.items():
            profile[section] = (
                {**profile[section], **fields} if isinstance(fields, dict) else fields
            )
        path = self.folder / "profile.yaml"
        path.write_text(yaml.safe_dump(profile))
        return path


@pytest.fixture
def writer(tmp_path: Path) -> ProfileWriter:
    return ProfileWriter(tmp_path)


class TestExample:
    def test_the_shipped_example_is_valid(self) -> None:
        profile = ProfileFile(EXAMPLE).load()
        assert isinstance(profile, Profile)


class TestValidation:
    def test_a_missing_required_field_names_it(self, writer: ProfileWriter) -> None:
        path = writer.write({"identity": {"email": None}})
        with pytest.raises(ProfileError, match="identity.email"):
            ProfileFile(path).load()

    def test_an_unknown_field_is_refused(self, writer: ProfileWriter) -> None:
        path = writer.write({"identity": {"emial": "x@y.z"}})
        with pytest.raises(ProfileError, match="emial"):
            ProfileFile(path).load()

    def test_a_missing_file_says_where(self, tmp_path: Path) -> None:
        with pytest.raises(ProfileError, match="profile.yaml"):
            ProfileFile(tmp_path / "profile.yaml").load()


class TestRender:
    def test_quick_reference_rows_carry_the_values(self, writer: ProfileWriter) -> None:
        path = writer.write({"identity": {"first_name": "Ada", "email": "ada@mail.io"}})
        text = ProfileFile(path).knowledge()
        assert "# Form Quick-Reference" in text
        assert "| First name | Ada |" in text
        assert "| Email | ada@mail.io |" in text

    def test_eeo_answers_are_rendered(self, writer: ProfileWriter) -> None:
        path = writer.write({"eeo": {"veteran": "I am not a veteran"}})
        assert "| Veteran status | I am not a veteran |" in ProfileFile(path).knowledge()

    def test_work_history_and_cover_letters_are_rendered(
        self, writer: ProfileWriter
    ) -> None:
        text = ProfileFile(writer.write({})).knowledge()
        assert "# Work history" in text
        assert "# Cover letters" in text


class TestFieldValues:
    # The values the per field check compares typed text against.
    def test_fixed_fields_are_exposed_by_key(self, writer: ProfileWriter) -> None:
        path = writer.write({"identity": {"email": "ada@mail.io"}, "eeo": {"gender": "Female"}})
        values = ProfileFile(path).load().field_values()
        assert (values["email"], values["gender"]) == ("ada@mail.io", "Female")
