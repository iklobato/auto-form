# auto-form

auto-form applies to jobs for you. For each job site in a YAML list it opens a real
Chrome through [browser_use](https://github.com/browser-use/browser-use), gives an LLM
(through [OpenRouter](https://openrouter.ai)) your profile, and lets it search the
site, pick matching roles, fill the apply form, upload your resume and submit. It
records every application, every attempt it gave up on, and the requests it sent.

> **A run without `--dry-run` sends real applications under your name.** Start with
> `--dry-run` (section 4). It fills the forms but the browser blocks every submit.

Working on the code (person or AI agent)? Read
[docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) first, then the latest file in
[docs/sessions/](docs/sessions/).

## 1. What you need

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Google Chrome (browser_use finds and starts it)
- An OpenRouter API key with credit
- Your resume as a PDF

## 2. Set up (once)

```bash
git clone git@github.com:iklobato/auto-form.git && cd auto-form
uv sync
cp .env.example .env        # then fill it in, see section 7
```

Then create the three local things the app reads. All three are git-ignored, so your
data never goes into the repo:

| Path | What to put there | Section |
|---|---|---|
| `.env` | API key, resume path, optional passwords | 7 |
| `systems.yaml` | the job sites to visit | 8 |
| `knowledge/*.md` | your profile: answers to form questions, work history, skills, cover letters | 9 |

## 3. Check the setup

```bash
uv run python fill.py --list              # the sites in systems.yaml; ✓ = has a URL
uv run pytest                             # all tests (no LLM, no cost)
uv run pytest tests/test_form_coverage.py -v   # does your profile answer common form questions?
```

`test_form_coverage.py` reads your real `knowledge/` and fails for each common form
question your profile cannot answer. Fix those rows before a real run.

## 4. First run: dry run on one site

```bash
uv run python fill.py --system weworkremotely --dry-run
```

What happens: Chrome opens, the agent searches, opens matching roles, fills the form
and uploads your resume, but every POST/PUT/PATCH that is not analytics is blocked by
the browser, so nothing is sent. In a dry run nothing is written to `applied.jsonl`,
`attempted.jsonl`, your profile notes or the logins in `systems.yaml`.

Then look at what it did:

```bash
ls screenshots/weworkremotely/            # one PNG per step
tail -n 50 logs/<run_id>.log              # the run id is printed at start
grep "DRY RUN: blocked" logs/<run_id>.log # what the browser stopped
```

Some sites return HTTP 403 to a headless Chrome (We Work Remotely does). Leave
`--headless` off for those.

## 5. Real runs

```bash
# one site
uv run python fill.py --system weworkremotely --model google/gemini-2.5-flash --max-actions 12

# every site in the file, 2 at a time, no window
uv run python fill.py --all --workers 2 --headless --model google/gemini-2.5-flash --max-actions 12
```

**Which model and `--max-actions`.** The defaults are `google/gemini-2.5-flash-lite` and
5 actions per step. In a local end-to-end test on 2026-10-30 these defaults sent no
application: the agent filled 4 of 14 fields, the browser blocked the submit, and the
agent said it worked. browser_use drops actions past `--max-actions` without telling the
model. With `--model google/gemini-2.5-flash --max-actions 12` both test forms were
sent with the right answers. flash costs more: about US$ 0.17 for two forms in that
test, and US$ 0.08 against US$ 0.012 for flash-lite on one We Work Remotely run. Use
flash for sites where you expect a form; flash-lite is fine for scanning. Details:
[docs/sessions/2026-10-30.md](docs/sessions/2026-10-30.md).

**Duplicates.** Before applying, the agent calls `check_applied`, which skips a job
already in `applied.jsonl` or `attempted.jsonl` (URLs are compared without tracking
params, `www.`, trailing slash). A run can be stopped and started again. Known gap: an
agent that never calls `check_applied` can still apply twice (seen once with
flash-lite).

## 6. After a real run, check

```bash
uv run python fill.py --summary           # applications per site, from applications/
```

| Look for | Where | Means |
|---|---|---|
| `"submit_seen": false` | `applied.jsonl` | the agent said it applied, but no form submit was seen. Check by hand, it may not have been sent |
| `"reason": "submitted_not_marked"` | `attempted.jsonl` | a form was sent but the agent did not confirm it. It may have gone out; it will not be retried |
| other `reason` values (`captcha`, `login_required`, `external_redirect`, ...) | `attempted.jsonl` | the agent gave up on that job; it is skipped from now on |
| the POST that was sent | `applications/<site>/network/*_post.json` | the exact request body, to check what the employer received |
| `HTTP 4xx` / `http_error` | run log, `logs/events.jsonl` | a server refused something, maybe the form |

To retry a job that ended in `attempted.jsonl`, delete its line from that file.

## 7. Environment variables (`.env`)

| Name | Needed | What for |
|---|---|---|
| `OPENROUTER_TOKEN` | yes | the LLM key |
| `RESUME_PATH` | yes | absolute path to the PDF to upload (a site can override it with `resume_path`) |
| `OPENROUTER_MODEL` | no | default model instead of `google/gemini-2.5-flash-lite`; `--model` wins over it |
| `PORTAL_ACCOUNT_PASSWORD` | no | password when the agent has to create an account. Without it one is generated per site and kept in `browser_profiles/<site>/portal_password` |
| `LINKEDIN_SSO_USERNAME`, `LINKEDIN_SSO_PASSWORD` | no | used when a site offers "Continue with LinkedIn" |
| any name you put in `username_env` / `password_env` / `token_env` | no | per-site logins and proxy keys from section 8 |

browser_use also reads `TIMEOUT_<EventName>` variables (for example
`TIMEOUT_BrowserConnectedEvent`) to give a slow machine more time.

## 8. Job sites (`systems.yaml`)

```yaml
systems:
  - name: weworkremotely                  # used with --system
    url: https://weworkremotely.com/
    search_url: https://weworkremotely.com/remote-jobs/search?term=python+backend
    description: We Work Remotely, backend roles
    search_instructions: >-
      Open each role in the list and check it.
    form_hints: >-
      Apply links often go to the company's own site.

  - name: flexjobs
    url: https://www.flexjobs.com/
    login_url: https://www.flexjobs.com/login
    credentials:
      username: you@example.com
      password_env: FLEXJOBS_PASSWORD     # read from .env, keeps the password out of the YAML
```

| Key | Needed | What it does |
|---|---|---|
| `name` | yes | the site id |
| `url` | yes | where the agent starts. A value like `$MY_URL` is read from the env; a site whose URL is empty or unset is skipped |
| `search_url` | no | a listing page already filtered for you; used instead of searching |
| `description` | no | shown by `--list` |
| `search_instructions`, `form_hints` | no | extra instructions for this site |
| `filter_notes` | no | which titles to skip; replaces the default (skip product, design, sales, mobile, ... roles) |
| `application_notes` | no | extra rules for the apply step on this site |
| `best_for` | no | tells the agent what the site is good for |
| `login_url`, `account_required_to_view` | no | for sites that need a login |
| `credentials` | no | `username` / `password`, or `username_env` / `password_env` to read them from `.env`. When the agent creates an account it saves the login here |
| `resume_path` | no | a different resume PDF for this site |
| `proxy` | no | a proxy for this site (below) |

A top-level `proxy:` block is the default for every site. Kinds:

```yaml
proxy: {kind: static, host: 1.2.3.4, port: 8080, username_env: PROXY_USER, password_env: PROXY_PASS}
proxy: {kind: list, path: proxies.txt}             # one host:port per line, rotates
proxy: {kind: webshare, token_env: WEBSHARE_TOKEN, country: US}
proxy: {kind: brightdata, zone: my_zone, country: US}   # BRIGHTDATA_CUSTOMER, BRIGHTDATA_PASSWORD
proxy: {kind: none}
```

Add `cache_s: 600` to reuse one proxy for 10 minutes. `--dry-run` refuses a proxy with a
username and password (its auth handling would let blocked requests through).

The older top-level key `sources:` works the same as `systems:`.

## 9. Your profile (`knowledge/`)

Every `.md` file in `knowledge/` except `README.md` is pasted into the agent's prompt on
every run, so keep it to things about you. A layout that works:

```
knowledge/
  00_form_answers.md   | Field label | Answer | table: name, email, phone, links, work
                       authorization, salary, years of experience, EEO answers ...
  01_personal.md       location, time zone, languages
  02_work.md           current role and history
  03_skills.md         languages, frameworks, cloud, databases
  04_projects.md       projects worth mentioning
  05_freelance.md      contract terms and rate
  06_cover_letters.md  cover letters the agent can pick from and trim
  07_agent_notes.md    notes the agent adds with save_note after a real run
```

The agent copies `00_form_answers.md` answers word for word. Write them the way the
forms list the options (for example `I am not a veteran` if that is the option text):
in the 2026-10-30 test both models picked "I prefer not to answer" for veteran and
disability questions whose profile answers did not match the option text.

## 10. All flags

```
--all                  run every site in the file
--system NAME          run one site
--list                 list the sites and stop
--summary              print applications per site from applications/ and stop
--systems PATH         the site list (default systems.yaml)
--dry-run              fill forms, never submit (the browser blocks it)
--headless             no Chrome window (some sites answer 403)
--workers N            sites at the same time with --all, 1 or more (default 5)
--model ID             OpenRouter model id (default google/gemini-2.5-flash-lite)
--max-actions N        actions per LLM step, 1 or more (default 5; 12 works better for forms)
--fast                 browser_use flash mode: no self-check step, about 30% faster
--filter WORD          only roles whose title or description has WORD
--note "TEXT"          extra context added to the prompt for this run
--verbose, -v          debug logs: full URLs, action params, the agent's reasoning
--trace, -vv           also browser_use, httpx and playwright internals
```

## 11. Testing one job, or the app itself

- One real job without touching your site list: write a temp YAML with one entry
  whose `url` is that job, and run `--systems /tmp/one.yaml --system NAME --dry-run`.
  Public job board APIs list open roles without a browser:
  `https://boards-api.greenhouse.io/v1/boards/<company>/jobs`,
  `https://api.ashbyhq.com/posting-api/job-board/<company>`,
  `https://api.lever.co/v0/postings/<company>?mode=json`.
- The test suite: `uv run pytest`. Most tests use fakes and temp dirs.
  `tests/test_dry_run_guard_chrome.py` and `tests/test_network_recorder_chrome.py` start
  a real headless Chrome against local servers only (no LLM, no internet).
- A full local end-to-end test (a fake job site on `127.0.0.1` and a sandbox copy of
  the app) is described in [docs/sessions/2026-10-30.md](docs/sessions/2026-10-30.md),
  section 8.

## 12. What a run writes

```
logs/<run_id>.log                          the run log
logs/events.jsonl                          one JSON event per line (steps, errors, blocks)
screenshots/<site>/stepNNNN_<time>.png     one screenshot per agent step
applications/<site>/<time>_<title>.json    one record per application
applications/<site>/INDEX.md               table of applications for that site
applications/<site>/network/*_post.json    every POST/PUT/PATCH sent (not analytics)
applications/<site>/cdp.jsonl              browser events: responses, errors, popups
applied.jsonl                              applications sent (append-only)
attempted.jsonl                            jobs given up on, with a reason (append-only)
browser_profiles/<site>/                   Chrome profile per site (cookies, logins)
```

Everything above is git-ignored.

## License

MIT.
