"""Form-coverage tests.

For every (form fixture × required field), assert that the loaded knowledge
base contains the expected answer substring. This catches the bug where:

  - A new portal asks something the profile doesn't answer (e.g. "Years of Rust experience")
  - The knowledge file was edited and now lacks an answer for "Phone" or "Salary"
  - A dropdown options list changed and the agent's preferred answer is no longer offered

The test loads the REAL knowledge directory (not a mock) so it reflects what the
agent will actually see at runtime.
"""

from pathlib import Path

import pytest

from fill import KnowledgeBase
from tests.fixtures.forms import ALL_FORMS, FormField


# Module-level fixture: real on-disk knowledge base, loaded once per session.

@pytest.fixture(scope="module")
def real_knowledge() -> str:
    """Loads the actual knowledge/ directory the agent uses at runtime."""
    knowledge_dir = Path(__file__).parent.parent / "knowledge"
    return KnowledgeBase(dir_path=knowledge_dir).load()


def _flatten_forms() -> list[tuple[str, FormField]]:
    """Yield (form_name, field) for every field in every fixture form."""
    out = []
    for form_name, fields in ALL_FORMS.items():
        for field in fields:
            out.append((form_name, field))
    return out


# Pretty parameter id for `pytest -v`.
def _id(case):
    form_name, field = case
    return f"{form_name}::{field.label}"


class TestRequiredFieldsCovered:
    """For every required field, the expected answer must appear in the knowledge base."""

    @pytest.mark.parametrize(
        "case", _flatten_forms(), ids=_id,
    )
    def test_required_field_answer_in_knowledge(
        self, case: tuple[str, FormField], real_knowledge: str
    ):
        form_name, field = case
        if not field.required:
            pytest.skip("optional field — agent can leave blank")
        if field.kind == "file":
            pytest.skip("file fields are populated from RESUME_PATH env, not knowledge")
        assert field.expect_in.lower() in real_knowledge.lower(), (
            f"Form {form_name!r} field {field.label!r} expects answer containing "
            f"{field.expect_in!r}, but the knowledge base does not contain it. "
            f"Either add it to knowledge/00_form_answers.md or update the fixture."
        )


class TestDropdownOptionsAvailable:
    """For dropdown/radio fields with explicit options, the chosen answer must be
    one of the offered options (substring match)."""

    @pytest.mark.parametrize(
        "case", _flatten_forms(), ids=_id,
    )
    def test_expected_answer_matches_an_option(
        self, case: tuple[str, FormField]
    ):
        _, field = case
        if field.kind not in ("dropdown", "radio") or not field.options:
            pytest.skip("free-form or no options declared")
        matching = [opt for opt in field.options
                    if field.expect_in.lower() in opt.lower()]
        assert matching, (
            f"Field {field.label!r}: no option in {field.options!r} contains "
            f"the expected answer {field.expect_in!r}. The fixture is wrong, or "
            f"the portal changed its options."
        )


class TestRequiredFieldsHaveAnExpectation:
    """Sanity check: every fixture entry has an expect_in. Catches typos."""

    @pytest.mark.parametrize(
        "case", _flatten_forms(), ids=_id,
    )
    def test_expect_in_is_non_empty(self, case: tuple[str, FormField]):
        _, field = case
        assert field.expect_in, f"Field {field.label!r} declares no expected answer"
