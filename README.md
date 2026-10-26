# auto-form

AI-powered job-application automation. Drives a real Chrome session through
[browser_use](https://github.com/browser-use/browser-use) + an LLM (OpenRouter
by default) to navigate job portals, find roles matching a profile, fill the
apply form, upload a resume, and record what happened.

## What it does

For each portal listed in `systems.yaml`:

1. Navigates to the portal's primary search URL.
2. Picks job postings that match the profile (title keywords, remote-only,
   skip-list filters).
3. Opens the apply form, fills every field by looking up answers in a
   markdown-based "knowledge base" (`knowledge/*.md`).
4. Uploads the resume PDF when a file input is detected.
5. Submits the form, captures a screenshot per step, logs CDP network POSTs to
   verify what was submitted, and writes a structured record:
   - `applied.jsonl` — successful submissions
   - `attempted.jsonl` — jobs the agent reached but gave up on, with a reason
     enum (`captcha`, `login_required`, `external_redirect`, `broken_form`,
     `restrictive_location`, …)
   - `applications/<portal>/<timestamp>_<slug>.json` — rich per-portal record
     with salary, location, requirements

Two anti-loop mechanisms keep the agent from burning the LLM budget on a
broken page:

- **Per-session visit counter** — the same URL checked 3× in one session
  triggers a `HARD-SKIP`.
- **"Always navigate after `mark_attempted`"** rule baked into the task
  prompt so the agent advances after marking a job dead.

## Repo layout

```
fill.py              Single-file engine (~1700 lines) — every class lives here.
prompts/task.md      The task prompt rendered into every browser_use Agent run.
tests/               pytest suite, plus fixtures of real-world form shapes.
pyproject.toml       uv-managed dependencies.
.env.example         Required environment variables.
```

Three things are **not** in this repo and must be supplied locally:

| Path | Purpose | How to populate |
|---|---|---|
| `.env` | API tokens, SSO creds, resume path | Copy `.env.example` and fill in |
| `systems.yaml` | List of portals to crawl + per-portal credentials | See "Portal config" below |
| `knowledge/` | Your résumé content, salary expectations, form answers | See "Knowledge base" below |

All three are gitignored.

## Quickstart

```bash
# 1. install
uv sync

# 2. configure credentials
cp .env.example .env
$EDITOR .env

# 3. write your portal list (see template below)
$EDITOR systems.yaml

# 4. add your knowledge base
mkdir -p knowledge
$EDITOR knowledge/01_personal.md
# ... etc

# 5. dry-run a single portal
uv run fill.py --systems systems.yaml --system remoteok --dry-run --verbose

# 6. real run, all portals
uv run fill.py --systems systems.yaml --all --workers 1 --headless
```

## CLI flags

```
--systems PATH       Portal list YAML (default: systems.yaml)
--all                Run every portal in the file
--system NAME        Run one portal, by its exact name
--filter WORD        Only consider roles whose title or description contains WORD
--workers N          Concurrent portals, 1 or more (default: 5; >1 needs care with CDP)
--headless           Run Chrome without a window (required for servers)
--dry-run            Fill forms but never submit (the browser blocks every submit)
--fast               Use a smaller/faster LLM model
--max-actions N      Max actions per LLM call (default: 5)
--model NAME         Override the default LLM (e.g. anthropic/claude-sonnet-4)
--verbose            DEBUG-level logging
--trace              TRACE-level (every CDP event)
--note "..."         Free-text note attached to every application record
```

## Environment variables

See `.env.example` for the full list. Minimum:

- `OPENROUTER_TOKEN` — the LLM used by browser_use's `Agent`
- `RESUME_PATH` — absolute path to the resume PDF that should be uploaded
- `LINKEDIN_SSO_USERNAME` / `LINKEDIN_SSO_PASSWORD` — used when a portal
  offers "Continue with LinkedIn"
- `PORTAL_ACCOUNT_PASSWORD` — fallback password when the agent self-registers

The `Agent` understands several `TIMEOUT_*` envs that bump browser_use's
internal timeouts when running on slow hardware (cloud VMs). See `.env.example`.

## Portal config (`systems.yaml`)

```yaml
systems:
  - name: weworkremotely
    url: https://weworkremotely.com/
    search_url: https://weworkremotely.com/categories/remote-back-end-programming-jobs
    description: WeWorkRemotely — backend listings
    search_instructions: |
      Browse the listed roles. Pick anything matching Python/Go/Node backend.
    form_hints: |
      External apply links go to the company's own ATS.

  - name: flexjobs
    url: https://www.flexjobs.com/
    credentials:
      username: you@example.com
      password_env: FLEXJOBS_PASSWORD   # reads from .env
```

`url` is the marketing page; `search_url` is the pre-filtered listings URL.
`credentials.password_env` keeps secrets in `.env` rather than in the YAML.

## Knowledge base (`knowledge/`)

Plain markdown files the agent reads at every Agent invocation. The structure
is up to you — anything in the directory gets concatenated and made available
in the task context. The reference layout is:

```
knowledge/
  00_form_answers.md   Quick-reference table of "Field → Answer"
  01_personal.md       Name, email, location, time zone
  02_work.md           Current role, experience summary
  03_skills.md         Languages, frameworks, cloud, databases
  04_projects.md       Open-source projects worth mentioning
  05_freelance.md      Freelance availability + rate
  06_cover_letters.md  Generic and per-portal cover letters
  07_agent_notes.md    Free-text notes the agent appends via `save_note`
```

The form-coverage test (`tests/test_form_coverage.py`) cross-references the
fixture form shapes in `tests/fixtures/forms.py` against the loaded
knowledge — pointing out when a portal asks something the knowledge base
doesn't answer. The test self-skips when `knowledge/` is empty.

## Running tests

```bash
uv run pytest                      # full suite
uv run pytest -k recorder          # just NetworkRecorder tests
uv run pytest tests/test_form_coverage.py -v   # validate knowledge coverage
```

All tests use `MagicMock(spec=Class)` and isolated `tmp_path` directories — no
network, no real LLM, no real browser.

## Output artifacts

A run produces:

```
screenshots/<portal>/<run-id>/step_NNN.png   Per-step screenshots
logs/<portal>/<run-id>.log                   Per-portal text log
applications/<portal>/<ts>_<slug>.json       Rich per-application record
applied.jsonl                                Append-only log of successes
attempted.jsonl                              Append-only log of give-ups
```

Everything except `pyproject.toml` / `uv.lock` is gitignored.

## License

MIT.
