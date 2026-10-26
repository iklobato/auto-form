"""Sample apply-form schemas drawn from real-world portal patterns.

Each fixture is a list of fields. A field declares:
    - label:     the question text the agent will see on screen
    - required:  whether the form blocks submit if blank
    - kind:      "text" | "textarea" | "dropdown" | "radio" | "checkbox" | "file" | "email" | "phone"
    - options:   for dropdown/radio — the allowed choices (None = free-form)
    - expect_in: a substring that the answer MUST contain for the agent to be considered "correct"

The expect_in values map to fields in the user's local knowledge/00_form_answers.md and
the rest of their knowledge base. The coverage test verifies that for every
(label, expect_in) pair, the loaded knowledge actually contains that expected
substring (case-insensitive).

The placeholder values below ("Test Candidate", "test@example.com", etc.) are
for the public test suite. A real deployment overrides knowledge/ with the
user's actual identity — fork this file and adjust expect_in to match your own
profile if you want test_form_coverage to pass on real data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FormField:
    label: str
    kind: str
    expect_in: str
    required: bool = True
    options: tuple[str, ...] | None = None


# ── Fixture 1: a representative internal-portal form ────────────────────────

SAMPLE_INTERNAL_FORM = [
    FormField("Upload Your Resume/CV", kind="file", expect_in="resume", required=True),
    FormField(
        "Target job title",
        kind="text",
        expect_in="Senior Backend Engineer",
        required=True,
    ),
    FormField("Your full name", kind="text", expect_in="Test Candidate", required=True),
    FormField(
        "Experience Level",
        kind="dropdown",
        options=(
            "Entry (0-2 years)",
            "Mid (3-5 years)",
            "Senior (6-10 years)",
            "Lead (10+ years)",
        ),
        expect_in="Senior",
        required=True,
    ),
    FormField(
        "Job Status",
        kind="dropdown",
        options=("Open To New Opportunities", "Actively Looking", "Not Looking"),
        expect_in="Open To New Opportunities",
        required=True,
    ),
    FormField(
        "Preferred Salary Range",
        kind="dropdown",
        options=("$0 - $5,000 USD", "$5,000 - $10,000 USD", "$10,000 - $25,000 USD"),
        expect_in="10,000",
        required=True,
    ),
]


# ── Fixture 2: Greenhouse-style application form ────────────────────────────

GREENHOUSE_FORM = [
    FormField("First Name", kind="text", expect_in="Test"),
    FormField("Last Name", kind="text", expect_in="Candidate"),
    FormField("Email", kind="email", expect_in="test@example.com"),
    FormField("Phone", kind="phone", expect_in="+1"),
    FormField("Resume/CV", kind="file", expect_in="resume"),
    FormField("LinkedIn Profile", kind="text", expect_in="linkedin.com/in/"),
    FormField("GitHub Profile", kind="text", expect_in="github.com/"),
    FormField("Website", kind="text", expect_in="example.com"),
    FormField(
        "Are you legally authorized to work in the US?",
        kind="radio",
        options=("Yes", "No"),
        expect_in="No",
    ),
    FormField(
        "Will you now or in the future require sponsorship?",
        kind="radio",
        options=("Yes", "No"),
        expect_in="No",
    ),
]


# ── Fixture 3: Lever / Ashby-style form with cover letter ───────────────────

LEVER_FORM = [
    FormField("Full name", kind="text", expect_in="Test Candidate"),
    FormField("Email", kind="email", expect_in="test@example.com"),
    FormField("Current company", kind="text", expect_in="Acme"),
    FormField("Current location", kind="text", expect_in="Remote"),
    FormField("Resume", kind="file", expect_in="resume"),
    FormField("LinkedIn URL", kind="text", expect_in="linkedin.com"),
    FormField(
        "How did you hear about this role?",
        kind="dropdown",
        options=("Internal job board", "LinkedIn", "Referral", "Other"),
        expect_in="Internal",
    ),
    FormField("Cover letter", kind="textarea", expect_in="Python", required=False),
]


# ── Fixture 4: Workday-style multi-step (compensation + experience) ─────────

WORKDAY_COMPENSATION = [
    FormField("Years of professional experience", kind="text", expect_in="10"),
    FormField("Years of Python experience", kind="text", expect_in="10"),
    FormField("Years of Django experience", kind="text", expect_in="8"),
    FormField(
        "Highest level of education completed",
        kind="dropdown",
        options=("High School", "Associate's", "Bachelor's", "Master's", "PhD"),
        expect_in="Bachelor",
    ),
    FormField("Current job title", kind="text", expect_in="Senior Backend Engineer"),
    FormField("Expected annual salary (USD)", kind="text", expect_in="$"),
    FormField(
        "Are you a people manager?", kind="radio", options=("Yes", "No"), expect_in="No"
    ),
]


# ── Fixture 5: Ashby with technical skill checkboxes ────────────────────────

ASHBY_TECHNICAL = [
    FormField(
        "Primary programming language",
        kind="dropdown",
        options=("Python", "JavaScript", "Go", "Java", "Ruby"),
        expect_in="Python",
    ),
    FormField(
        "Frameworks you've used (check all)",
        kind="checkbox",
        options=("Django", "FastAPI", "Flask", "Rails", "Spring"),
        expect_in="Django",
    ),
    FormField(
        "Cloud platforms (check all)",
        kind="checkbox",
        options=("AWS", "GCP", "Azure", "DigitalOcean"),
        expect_in="AWS",
    ),
    FormField(
        "Are you comfortable with async communication?",
        kind="radio",
        options=("Yes", "No"),
        expect_in="Yes",
    ),
    FormField(
        "Have you worked on a fully remote team?",
        kind="radio",
        options=("Yes", "No"),
        expect_in="Yes",
    ),
]


# ── Fixture 6: EEO / demographic (voluntary) ────────────────────────────────

EEO_FORM = [
    FormField(
        "Veteran status",
        kind="dropdown",
        options=("I am a veteran", "I am not a veteran", "I prefer not to answer"),
        expect_in="not a veteran",
        required=False,
    ),
    FormField(
        "Disability status",
        kind="dropdown",
        options=("Yes, I have a disability", "No", "I prefer not to answer"),
        expect_in="No",
        required=False,
    ),
    FormField(
        "Gender",
        kind="dropdown",
        options=("Male", "Female", "Non-binary", "Prefer not to say"),
        expect_in="Prefer not to say",
        required=False,
    ),
    FormField(
        "Ethnicity", kind="dropdown", expect_in="Prefer not to say", required=False
    ),
]


# ── Master registry — used by the coverage test ──────────────────────────────

ALL_FORMS = {
    "internal_sample": SAMPLE_INTERNAL_FORM,
    "greenhouse": GREENHOUSE_FORM,
    "lever": LEVER_FORM,
    "workday_compensation": WORKDAY_COMPENSATION,
    "ashby_technical": ASHBY_TECHNICAL,
    "eeo": EEO_FORM,
}
