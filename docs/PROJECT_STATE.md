# auto-form project state

Read this first if you are an agent (or a person) picking up this repo. It says what the
code does today, what was learned the hard way, what was done, and what is still open.
Last updated: 2026-10-30.

This repo is public. Never put personal data here (names, emails, phone, resume file
names, anything from `knowledge/`, `systems*.yaml` or `.env`).

## 1. What this is

`fill.py` is a single-file Python app that applies to jobs. For each portal in a systems
YAML it opens a real Chrome through [browser_use](https://github.com/browser-use/browser-use),
gives an LLM (OpenRouter, default `google/gemini-2.5-flash-lite`) a task built from
`prompts/task.md` plus the profile in `knowledge/*.md`, and lets the agent search,
filter, fill the form, upload the resume and submit. It records what happened.

## 2. Commands

```bash
uv run pytest -q                                  # all tests, must be green
uv run python fill.py --help                      # flags
uv run python fill.py --list                      # portals in systems.yaml
uv run python fill.py --system NAME --dry-run --headless   # safe single-portal run
uv run python fill.py --systems /path/other.yaml --system NAME --dry-run
uv run python fill.py --summary                   # applications on disk
```

- `--dry-run` fills forms but cannot submit: the browser blocks it (see 5.1). Use it for
  every test. A run without `--dry-run` sends real applications under the user's name.
- `--headless` gets HTTP 403 from Cloudflare on some boards (We Work Remotely). Drop it
  there; a visible Chrome got through on 2026-10-26.
- To test one job without touching the user's portal list, write a temp systems YAML with
  one entry and pass `--systems`. `YamlStore` may write credentials into that file.

## 3. Repo map

| Path | Tracked | What it is |
|---|---|---|
| `fill.py` | yes | the whole app (about 2,800 lines) |
| `prompts/task.md` | yes | task template; `TaskBuilder` fills `{placeholders}` |
| `tests/` | yes | pytest suite; `tests/fixtures/forms.py` has form fixtures |
| `README.md` | yes | user docs and CLI flags |
| `.env.example` | yes | env template |
| `.env` | no | secrets: `OPENROUTER_TOKEN`, `RESUME_PATH`, `PORTAL_ACCOUNT_PASSWORD`, `LINKEDIN_SSO_*`, per-portal `*_USERNAME/_PASSWORD` |
| `systems.yaml` | no | default portal list (55 entries). Holds plaintext portal passwords |
| `systems-all.yaml` | no | bigger list (70), only used with `--systems systems-all.yaml` |
| `knowledge/*.md` | no | the user's profile. **Every .md except README.md is pasted into the LLM prompt.** Do not put project notes here |
| `applied.jsonl` / `attempted.jsonl` | no | trackers (see 5.4) |
| `applications/<portal>/` | no | per-application JSON, `INDEX.md`, `network/*_post.json`, `cdp.jsonl` |
| `logs/` | no | run logs and `events.jsonl` (one JSON event per line) |
| `browser_profiles/<portal>/` | no | Chrome profile per portal; also `portal_password` (0600) |
| `screenshots/<portal>/` | no | one PNG per agent step |

## 4. How a run works

`CLI.run` is the composition root. Flow:

1. `CLI` parses flags, loads `.env`, the systems YAML, builds `KnowledgeBase`,
   `LLMFactory` (OpenRouter client with `BackoffTransport`), trackers and recorders.
2. `Orchestrator` (for `--all`) or `CLI._run_single` (for `--system`) builds one
   `PortalRunner` per portal. `Orchestrator` builds every portal's proxy provider once at
   startup, runs portals under a semaphore (`--workers`, at least 1, default 5), then
   retries failed portals once.
3. `PortalRunner.run`: credentials (`CredentialsResolver` + generated portal password),
   task text (`TaskBuilder`), proxy (acquired in a thread), then `_build_agent`
   (browser profile, `NetworkRecorder`, `CDPDebugger`, optional `DryRunGuard`,
   `StepCallback`) and `AgentActions.register_all` (custom tools).
4. `browser_use.Agent.run()` loops. Before each step's actions execute, browser_use calls
   `StepCallback.on_step`, which attaches the guard and recorders, logs the step and saves
   a screenshot.
5. The agent calls the custom tools: `check_applied`, `mark_applied`, `mark_attempted`,
   `save_screenshot`, `save_note`, `save_credentials`.
6. `PortalRunner._run_agent` raises `AgentStoppedEarly` if the agent never called `done`
   (step or failure limit). That counts as a failure and is retried.

## 5. Behaviors you must keep (each has tests)

### 5.1 Dry run cannot submit (`DryRunGuard`)
- Enabled on the **browser target** with CDP `Fetch.enable` (pattern `*`), not on a page
  session. A page-session Fetch let a POST from a cross-origin iframe reach the server in a
  real-Chrome test; the browser-level one blocked top-level, iframe and new-tab submits.
- Every non-noise POST/PUT/PATCH fails with `BlockedByClient`, except a GraphQL body whose
  every operation is a plain `query` (Ashby loads its form with GraphQL queries). Mutations,
  mixed documents, missing query text and unparsable bodies stay blocked.
- Attached from `StepCallback.on_step` **outside** its try/except: if it cannot attach,
  the step's actions are dropped instead of running unguarded.
- `--dry-run` with an authenticated proxy is refused (browser_use's proxy-auth handler
  would continue paused requests).
- The prompt gets one `TaskBuilder.DRY_RUN_NOTICE`: fill, do not submit, call
  `mark_applied`, move on. In a dry run `mark_applied` and `mark_attempted` write nothing,
  and `mark_applied` replies that the job is done (a "not recorded" reply caused a loop).
  `save_credentials` (the sign-up POST was blocked, so the account does not exist) and
  `save_note` (`knowledge/*.md` goes into every prompt) also write nothing. The notice
  says a failed upload is expected, so the agent does not report it as an error.
- Known cost: sites that use POST for reads other than GraphQL queries (for example Algolia
  search) may show nothing in a dry run.

### 5.2 CDP handlers are chained (`add_cdp_handler`)
`cdp_use` keeps **one handler per event name**, shared by every session on the client. A
plain `client.register.X.y(cb)` replaces browser_use's own watchdogs (dialog auto-accept,
downloads) and our recorders. Always subscribe through `add_cdp_handler`, which fans out to
every callback and isolates a failing one. Never `await` a CDP command inside an event
handler (its reply comes on the same read loop): schedule it with `asyncio.create_task`.

### 5.3 Submit evidence
`NetworkRecorder` saves each non-noise POST/PUT/PATCH to
`applications/<portal>/network/<datetime with microseconds>_<requestId>_<method>.json`
and keeps a ring buffer. `check_applied` returning `not_applied` calls `start_job()` to
clear it, so `mark_applied` attaches only the current job's POSTs. Analytics hosts live in
`NetworkRecorder.NOISE_FRAGMENTS`; add new ones there (the guard uses the same list).

### 5.4 Trackers and duplicates
`applied.jsonl` and `attempted.jsonl` are append-only JSONL with `flock`. `check_applied`
compares `job_url_key()`, not the raw URL: scheme, host case, `www.`, trailing slash,
fragment and tracking params (`utm_*`, `lever-source`, `gh_src`, ...) are ignored; job id
params such as `gh_jid` are kept. An attempted URL is skipped forever.

### 5.5 Credentials
- `YamlStore.update_credentials` takes a `flock` on `<yaml>.lock`, writes a temp file in
  the same dir, fsyncs, keeps the file mode and `os.replace`s. `*.yaml.lock` and
  `.*.yaml.*` are git-ignored (the temp file holds passwords).
- Without `PORTAL_ACCOUNT_PASSWORD`, the password for new portal accounts is generated
  once per portal and stored in `browser_profiles/<portal>/portal_password` before the agent
  sees it, so every run and retry uses the same one.

### 5.6 Failure handling
- `Orchestrator` retries only recoverable errors (`TimeoutError`, `OSError`,
  `RuntimeError`, `httpx.HTTPError`). Any other exception in one portal (a bug) is logged
  as `portal_crash`, not retried, the batch and summary finish, then it is re-raised.
- `StepCallback` errors (other than the guard) are logged as `step_callback_error` and
  never reach the agent.
- `BackoffTransport` retries 429/5xx, caps `Retry-After` at `max_wait`, stops when the
  next sleep would pass 45 s in total (browser_use cancels an LLM call after 75 s for the
  default gemini model; slowest logged step was 19.8 s), and closes each discarded response.
- `PortalRunner._clean_stale_locks` raises `ProfileInUse` if the profile's `SingletonLock`
  points at a live Chrome on this host; it only removes locks from dead or foreign pids.

## 6. Things learned (read before debugging)

- browser_use returns the history instead of raising when it hits `max_steps` or
  `max_failures`. Check `history.is_done()`.
- browser_use calls the step callback between the LLM reply and executing its actions; an
  exception there drops that step's actions.
- `time.strftime` has no `%f`. The old capture names only changed every 10 s and
  overwrote each other.
- Ashby: the page loads through `POST /api/non-user-graphql` queries (`ApiJobPosting`,
  `ApiOrganizationFromHostedJobsPageName`), autosaves each field with `ApiSetFormValue`
  (a mutation) and starts uploads with `ApiCreateFileUploadHandle` (a mutation).
- We Work Remotely: Cloudflare returns 403 to headless Chrome; a visible window passed.
  When stuck, the agent wandered to Google and hit its captcha.
- Remotive hides the apply link behind a paid "Unlock" wall.
- Most company boards in `systems.yaml` have no remote roles right now (Seasats, Parallel,
  Array Labs are on-site; Lucid Bots has 0 jobs). With the remote-only rule the agent
  correctly finds nothing there.
- `gemini-2.5-flash-lite` ignores the anti-loop rules already in `prompts/task.md`
  ("Maximum 2 scrolls between listing-opens", "Re-navigating ... over and over"): in the
  visible WWR run (`events.jsonl` run `20261025_235357`) 14 of 21 logged steps were
  scroll-only, over the same short list. May 2026 runs show the same (22 of 43 in
  `20260513_221619`). `google/gemini-2.5-flash` on the same portal: 5 of 23.
- Public ATS APIs are the fastest way to find open roles without a browser:
  `https://boards-api.greenhouse.io/v1/boards/<co>/jobs`,
  `https://api.ashbyhq.com/posting-api/job-board/<co>`,
  `https://api.lever.co/v0/postings/<co>?mode=json`.
- GitGuardian scans every commit of a PR; it flagged a made-up test password. Keep test
  secrets short or built at runtime (`"x$y"`), not password-looking literals.
- browser_use has its own loop detector, but its nudges start at 5 repeats and say to
  carry on if making progress; flash-lite scrolled through them. `ScrollLoopBrake` adds a
  note after every 3rd scroll-only step in a row. It writes to
  `agent._message_manager.state.agent_history_items` because context messages added in
  the step callback are cleared before the next LLM call, and `Agent.add_new_task`
  replaces the task. A browser_use upgrade can break this: `tests/test_scroll_loop_brake.py`
  checks it against the real `MessageManager`.
- browser_use drops actions past `max_actions_per_step` without telling the model
  (`agent/service.py`, `parsed.action[: max_actions_per_step]`); its memory then says the
  form is filled. With the default 5, flash-lite left 10 of 14 fields empty.
- browser_use's `input` action has `clear` (`'1=clear, 0=append'`); flash-lite sent
  `clear: False` when it typed into a field again, so the value went in twice.
- The editor quality hook runs `ruff --fix` and `black` on every edited file and deletes
  imports that are unused at that moment: add an import in the same edit as its first use.

## 7. Live test record

All runs were `--dry-run`. Nothing was ever written to `applied.jsonl` or `attempted.jsonl`
by these runs.

| Date | Portal | Mode | Result |
|---|---|---|---|
| 2026-10-25 | seasats | headless | 9 steps, all 5 roles on-site, no form |
| 2026-10-26 | lucid_bots (Greenhouse) | headless | 0 jobs (API agrees); guard blocked Snowplow analytics, page still loaded |
| 2026-10-26 | weworkremotely | headless | Cloudflare 403, no form |
| 2026-10-26 | weworkremotely | visible | passed Cloudflare; 28 analytics POSTs blocked and recorded as `network_post` (first since 2026-05-13); only match rejected on timezone |
| 2026-10-26 | Supabase "SDK Engineer - Python" (Ashby, temp YAML) | headless | form filled; 24 `ApiSetFormValue` blocked; agent did not click submit; looped `mark_applied` 7+ times (fixed) |
| 2026-10-26 | same | headless | 15 steps, `mark_applied` twice then `done`; 17 `ApiSetFormValue` and 1 `ApiCreateFileUploadHandle` blocked; agent reported the upload as an error |
| 2026-10-26 | weworkremotely, `--model google/gemini-2.5-flash` (run `20261026_004642`) | visible | 23 logged steps, 5 scroll-only (flash-lite: 21 / 14). Accepted the Proxify role that flash-lite rejected on timezone, followed it to `talent.proxify.io/apply`, filled it and uploaded the resume; its "Next" button never enabled, so no POST went to Proxify. Blocked: WWR `job_apply_count` and a Google conversion ping |
| 2026-10-26 | weworkremotely, cost run, flash-lite (run `20261026_005544`) | visible | **US$ 0.0117** (key `usage` 461.284374135 to 461.296102585), 9 steps, 5 scroll-only, 244 s, no form |
| 2026-10-26 | weworkremotely, cost run, flash (run `20261026_010014`) | visible | **US$ 0.0800** (461.298752735 to 461.378750505), 27 steps, 6 scroll-only, 178 s, no form |
| 2026-10-30 | Supabase "SDK Engineer - Python" (Ashby, temp YAML), new upload notice | headless | 15 steps, one `upload_file`, not retried; 1 `ApiCreateFileUploadHandle` blocked; final result: "the resume upload was attempted (and failed, as expected in a dry run)" |
| 2026-10-30 | weworkremotely, flash-lite, `ScrollLoopBrake` (run `20261030_114910`) | visible | 29 logged steps, 7 scroll-only; the brake fired twice and the next step was a click both times; reached the Bitovi form on `applytojob.com`, filled it, uploaded the resume, `done`. Cost not measured |

Local end-to-end runs (2026-10-30, not dry, submits went only to a local server on
`127.0.0.1:8765` that serves two forms built from `tests/fixtures/forms.py`; the app ran
from a sandbox dir with links to `fill.py`, `prompts`, `.env` and `knowledge`, so the
real trackers were not touched; submitted fields compared with `00_form_answers.md`):

| Model | `--max-actions` | Result |
|---|---|---|
| flash-lite | 5 (default) | 4 of 14 fields filled; browser validation blocked the submit; agent said `done` with success; no POST |
| flash-lite | 12 | job 1 sent; phone, LinkedIn and website sent twice (it typed again with `clear: False`); Gender, Veteran, Disability wrong; no `mark_applied`; job 2 never opened |
| flash | 12 | both jobs sent; job 1: 12 of 14 right (Veteran and Disability "I prefer not to answer"); job 2: all fields right; `mark_applied` passed the form URL for job 2 |
| flash, same trackers again | 12 | job 1 skipped (`already_applied`); job 2 **sent a second time** (fixed since, see section 9) |

After the duplicate fix (same day, fresh sandbox trackers, cost from the key `usage`):

| Run | Result | Cost |
|---|---|---|
| A: flash, `--max-actions 12` | 16 steps; both jobs sent; records keep the listing URL as `job_url` and the form URL as `apply_url`; no field sent twice; Veteran "I am not a veteran", Disability "No" | US$ 0.172 |
| B: same again | 8 steps; both jobs `already_applied`, **no POST** | US$ 0.068 |
| C: flash, `--dry-run`, fresh trackers | 48 steps; both forms filled; **no POST**, nothing in `applied.jsonl`, notes file unchanged (md5); the agent never clicked submit, so the guard had nothing to block | US$ 0.569 |
| D: flash-lite, `--max-actions 12` | 8 steps; job 1 sent, but no `check_applied` and no `mark_applied`, so nothing recorded; job 2 never opened | US$ 0.020 |

Cost runs are read from `GET https://openrouter.ai/api/v1/key` (`data.usage`, USD) before and
after one run; `/api/v1/activity` needs a management key. Usage also rose US$ 0.0027
between the two runs with nothing of ours running, so each number is good to about
US$ 0.003. One run per model is a small sample.

The agent obeys the dry-run notice and never clicks a final submit, so that click is
covered by `tests/test_dry_run_guard_chrome.py` instead: a real headless Chrome clicks a
form's submit button and sends a `fetch` POST to a local server; without the guard the
server records the POST, with it the server records nothing.

## 8. Done so far

- PR [#2](https://github.com/iklobato/auto-form/pull/2) (merged 2026-10-26): review fixes,
  one commit each, all with regression tests: CDP handler chaining, unique capture names,
  `AgentStoppedEarly`, atomic locked YAML writes, stable portal password, URL-normalized
  duplicate check, no attempts recorded in dry run, step-callback isolation, LLM backoff
  budget, black format commit, proxy fixes (provider built once, async acquire, Webshare
  valid/rotate, unset env vars, list parsing, SOCKS auth), crash isolation, `--workers`
  validation, one `resume_path` rule, per-job evidence, live-Chrome lock check, `DryRunGuard`.
  Its first version wrongly said the agent was never told about dry runs; the PR text was
  corrected and a comment explains it.
- PR [#3](https://github.com/iklobato/auto-form/pull/3) (merged 2026-10-26): Snowplow,
  `bzr.openai.com`, `t.co/i/adsctp`, `analytics.twitter.com` added to the noise list.
- PR [#4](https://github.com/iklobato/auto-form/pull/4) (merged 2026-10-26, 6 commits):
  GraphQL reads pass the guard; identity form-coverage tests check that a
  `00_form_answers.md` row is filled instead of a sample value (suite fully green); one
  dry-run prompt line instead of two opposite ones; README flags match `--help`; dry-run
  `mark_applied` reply says the job is done; this file and `CLAUDE.md`.
- Follow-up after PR #4 (2026-10-26): `google.com/measurement` (Google Ads conversion
  ping) added to the noise list; this file updated with the model comparison.
- Branch `fix/retry-upload-guard-scroll` (2026-10-30, one commit each, all with tests):
  LLM network errors retried within the 45 s budget (`RetryBudget`); dry-run notice says a
  blocked upload is expected; real-Chrome guard test; `ScrollLoopBrake`; `save_credentials`
  and `save_note` write nothing in a dry run; every 4xx/5xx logged as `http_error`;
  `--max-actions` must be 1 or more; stale comments and `check_applied` tool text fixed;
  E741 lint fixed; three more analytics hosts in the noise list; `save_credentials` for a
  portal missing from the YAML replies "not saved" and leaves the file alone; one
  `systems`/`sources` rule (`YamlStore.systems_key`); `ProfileInUse` is not retried;
  `browser_start` says `fresh` for a new profile.
- Measured: the guard does not slow screenshots (0.10 to 0.13 s with and without, 5 runs
  each on an Ashby page). Page loads were 1.1 to 1.5 s or about 8.2 s in both groups; the
  8 s is browser_use's own readiness timeout.

## 9. Open items and next steps

Each item says when it is done.

1. **Default model: decided 2026-10-26, kept `google/gemini-2.5-flash-lite`.** On WWR,
   `google/gemini-2.5-flash` cost 6.8 times more per run (US$ 0.0800 vs 0.0117; about
   2.3 times per step), and its better behavior (fewer scroll-only steps, reached a form)
   showed in 1 of 2 runs. Use `--model google/gemini-2.5-flash` for a portal where
   flash-lite stalls. Reopen if more runs show flash-lite missing real matches.
2. **Closed 2026-10-30:** dry-run upload error (notice changed, see section 7), guard on
   a real submit (real-Chrome test), LLM network-error retry, E741 lint.
3. **Scroll brake effect.** Fired and was followed by a click in 1 WWR run (section 7).
   Done when 3 or more flash-lite WWR runs are compared with the earlier ones.
4. **Review findings not fixed yet (2026-10-30):** `StepCallback` extracts the actions
   twice (`_extract_action_names` and `_extract_action_details`); `PortalRunner` is built
   the same way in `CLI._run_single` and `Orchestrator._make_runner`;
   `NetworkRecorder._seen_request_ids` and `CDPDebugger._request_index` grow without
   limit over a run. Done when each is fixed or dropped on purpose.
5. **Open from the local end-to-end runs:** typing twice with `clear: False` sends the
   value twice (fix idea: force `clear=True` on `input` in `StepCallback`); the default
   `--max-actions 5` cuts form fills short (fix idea: raise it, warn when actions are cut);
   Veteran/Disability came back "I prefer not to answer" in 2 of 3 runs although the
   profile says otherwise; flash-lite can submit without `mark_applied`, so the job is not
   recorded and the next run applies again (run D); records written before 2026-10-30 with a form URL as `job_url` still
   do not match their listing URL. Done when each is fixed or dropped on purpose, measured
   with the local portal.
6. **Same job on two portals** is not detected (different URLs). Done when there is a
   cross-portal key (for example company plus title) or an explicit decision not to.
7. **Screenshot timeouts on WWR** (`Clean screenshot timed out after 6 seconds`, 3 times)
   are not explained. Not caused by the guard on the Ashby page; never measured on WWR.
8. `knowledge/README.md` lists files that no longer exist (it is git-ignored, the user's
   copy). Tell the user, do not edit their profile.

## 10. Rules for agents working here

- Test with `--dry-run` only. Never run without it unless the user asks for real
  applications in that request.
- Never print or copy values from `.env`, `systems*.yaml` credentials or `knowledge/`.
  Refer to keys by name.
- Every behavior change needs a test that fails without it; run the full suite before
  committing. Commits are one logical change each, on a branch, never on `master`.
- Prefer the public ATS APIs (section 6) and a temp `--systems` file over editing
  `systems.yaml` when you need a specific job to test.
- Update this file when you change behavior, learn something non-obvious, or close an
  item in section 9.
