# Obscura as the default browser, before/after screenshots, four open items

Date: 2026-11-15. Source: grill-me session. Status: built, then **Obscura removed** after
the local run; see "Revised after build" at the end. Decisions 15 to 21 shipped.

## Facts measured in this session (before any code change)

All runs: browser_use 0.12.6 over CDP to `obscura serve` v0.2.7 (stealth build), against
headless Chrome launched by browser_use. Probe scripts lived in a scratch dir, not the repo.

| Check | Obscura | Chrome headless |
|---|---|---|
| `BrowserSession.start()` | 0.29 to 0.38 s | 1.39 to 11.79 s |
| `navigate_to` Greenhouse GitLab board | 0.78 to 1.35 s | 0.65 to 8.29 s |
| type into search + read value back | `engineer` | `engineer` |
| click a job link | URL changed | URL changed |
| WWR search page title | `We Work Remotely: Advanced Remote Job Search` | `Just a moment...` (Cloudflare) |
| `DOM.setFileInputFiles` | fails without `--allow-file-access`; with it `files.length 1` | `files.length 1` |
| `Network.requestWillBeSent` for a fetch POST | seen | seen |
| `DryRunGuard` real test (`tests/test_dry_run_guard_chrome.py` logic) | **fails**: server got `['/api/submit', '/apply']` | passes |
| `Fetch.requestPaused` | fires only on the page session (`sid=True`), not the browser target | (guard test passes) |
| DOM text sent to the LLM, Greenhouse / Ashby / Lever | 7336 / 7058 / 36576 chars | 2456 / 1744 / 2649 chars |

Not measured: a full agent run on Obscura (time or cost), cross-site iframe sessions on
Obscura, `CDPDebugger` on Obscura. Obscura is not Chromium: its screenshots show what Obscura
renders (WWR rendered shifted left).

## Decision tree

| # | Decision | Answer | Why | Depends on |
|---|---|---|---|---|
| 1 | Which browser | Obscura | fast start, passes WWR Cloudflare headless | none |
| 2 | Where it plugs in | Replace Chrome as the default browser of `fill.py` (browser_use over CDP) | user choice; recommended was opt-in only | 1 |
| 3 | Chrome code | Kept behind `--browser chrome` | escape hatch when a site breaks on Obscura | 2 |
| 4 | Dry-run browser | `--dry-run` runs only on Chrome | the guard is proven there and fails on Obscura | 2 |
| 5 | `--dry-run` with Obscura | Error with a clear message; the user passes `--browser chrome` | user choice; recommended was auto-switch | 3, 4 |
| 6 | Obscura process | `fill.py` starts one `obscura serve` per portal (own free port, own `--storage-dir`, own `--proxy`), stops it at portal end; shell out per repo rules (no `subprocess`) | per-portal isolation like today | 2 |
| 7 | Binary lookup | `OBSCURA_BIN` env var, else `obscura` on `PATH`; clear error if missing; no local path in code | public repo | 6 |
| 8 | Version | Require Obscura >= 0.2.7 at start, clear error otherwise | user choice; recommended was no check | 7 |
| 9 | Stealth | `--stealth` on | passed Cloudflare; blocks tracker POSTs | 6 |
| 10 | Upload | `--allow-file-access` on, plus code that blocks `file://` navigation, with a test | resume upload needs it; must not leak `.env` / `knowledge/` into the prompt | 6 |
| 11 | Old Chrome sessions | Start fresh on Obscura; agent logs in again with stored credentials; `browser_profiles/` left on disk | no import path between the two stores | 6 |
| 12 | Validation before any real run | Local end-to-end run (local form server, sandbox trackers, not dry) through Obscura | dry-run cannot exercise Obscura (4) | 4 |
| 13 | Private network for 12 | Obscura env var `OBSCURA_ALLOW_PRIVATE_NETWORK=1` set only on that command | zero code, never on by default | 12 |
| 14 | Cost | Measure the same local run on Chrome and Obscura (OpenRouter key `usage`); user decides with the number | DOM text is 3 to 14 times bigger | 12 |
| 15 | Screenshots | One before (current) and one after each step (`on_step_end`) | final state of each step is saved for later checks | none |
| 16 | Screenshot layout | `screenshots/<portal>/<run_id>/stepNNNN_before.png` and `_after.png`; old files stay | one folder per run is easy to review | 15 |
| 17 | Improvement: `clear: False` | Force `clear=True` on every `input` action | value typed twice (session 2026-10-30, item 1) | none |
| 18 | Improvement: `--max-actions` | Default 12 | default 5 left 10 of 14 fields empty | none |
| 19 | Improvement: duplicate without `check_applied` | Each step, code checks the current URL against the trackers; if already applied or attempted, drop the step actions and tell the agent | works on both browsers, no Fetch needed | none |
| 20 | Improvement: review leftovers | Actions extracted once, one `PortalRunner` build path, bounded request-id sets | no behavior change | none |
| 21 | Delivery | One branch, one PR, one commit per change, each with its test | same as PRs #2 to #7 | none |

## Build order (for spec-driven)

1. Improvements 17, 18, 20 (independent, small).
2. Screenshots 15 and 16.
3. Duplicate check 19.
4. Obscura: process manager (6, 7, 8, 9), `--browser` flag (3), dry-run refusal (5), `file://` block (10).
5. Local end-to-end run on Obscura (12, 13) and cost comparison with Chrome (14). Stop and report numbers before making Obscura the default in a release.
6. Update `docs/PROJECT_STATE.md`, `README.md`, a session file, `.env.example` (`OBSCURA_BIN`).

## Open risks / left undecided

- Obscura is never covered by a dry run. Its only safe proof is the local run (12). A bug on a real site shows up as a real application.
- DOM text 3 to 14 times bigger may raise cost per step by a similar factor; only measured on page size, not money.
- Cross-site iframe forms (Greenhouse embedded in a company site) not tried on Obscura: `FrameSessions` may see nothing.
- `--allow-file-access` safety depends on the `file://` block catching every path (navigation, new tab, iframe).
- `--headless` keeps meaning only for Chrome; Obscura has no visible mode, so WWR "visible" debugging needs `--browser chrome`.
- The dry-run guard could be rebuilt on Obscura page sessions later; not in scope.

## Revised after build (same day)

Decisions 1 to 14 were built (ObscuraServer, `--browser`, dry-run refusal) and then
removed with `git revert`, on the user's choice, after the local end-to-end run (12).
Evidence is in [../sessions/2026-11-15.md](../sessions/2026-11-15.md), section 3.

| # | Revised | Why |
|---|---|---|
| 1, 2, 3 | Obscura removed; Chrome stays the only browser; no `--browser` flag | Obscura 0.2.7 never sends a file: a classic form with `enctype=multipart/form-data` goes out urlencoded without the file, and `fetch` with `FormData` goes out without the file part. Every real application would go out without the resume and then be skipped forever |
| 4, 5 | Not needed | `--dry-run` runs on Chrome as before |
| 6 to 11, 13 | Not needed | went with Obscura |
| 12 | Done with Obscura and Chrome | it is what found the upload gap; the earlier probe `files.length 1` only proved the file attached, not that it was sent |
| 14 | Not measured | the OpenRouter key `usage` lags by minutes and the key is shared: the Chrome run showed no change right after it finished |
| 19 | Changed: records with reason `submitted_not_marked` do not stop page actions | that reason is a guess from any same-host POST and can name a listing (Ashby loads its board through same-host GraphQL POSTs) |
| 19 | Added: when every action is dropped, a `go_back` takes their place | browser_use does not count a step with no actions, so the step number repeated and its screenshots were overwritten |

Speed, one run each on the local portal: Obscura 112.6 s for 12 steps (9.4 s a step),
Chrome 128.9 s for 23 steps (5.6 s a step). Start time alone favored Obscura
(0.3 s against 1.4 to 11.8 s). Reopen Obscura only if a release sends multipart
form bodies with the file and pauses requests on the browser target.
