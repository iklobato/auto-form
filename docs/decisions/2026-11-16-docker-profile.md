# Docker image: structured profile + resume PDF in, job forms out

Date: 2026-11-16. Grill session with the repo owner. Builds on the fill/send verification of
the same day (password guard, resume reminder, POST required for `mark_applied`, PR #10).

## Decisions

| # | Decision | Choice | Why | Depends on |
|---|---|---|---|---|
| 1 | Who runs it and how | Local CLI: `docker run` on each person's own machine, one mounted folder | No third party data leaves the person's machine; no login, queue or hosting | - |
| 2 | Profile input format | One `profile.yaml` with a fixed schema, validated at start | Clear error when a field is missing; enables per field checks (12) | 1 |
| 3 | Dry run or real send | Dry run by default; real send only with an explicit `--send` flag | A forgotten test must never send applications under someone's name | 1 |
| 4 | Browser mode | Headless Chromium only, portals with no login | Covers the 13 portals that filled forms without login in the 2026-11-15/16 runs; no screen needed | 1 |
| 5 | Schema scope | Fixed validated fields (identity, contact, work authorization, compensation, EEO, education, years of experience) plus free text lists (work history, skills, projects, freelance, cover letters) | Covers everything in today's `knowledge/*.md` | 2 |
| 6 | Default portal list | Built-in list of the 13 no-login portals; `--systems` overrides | Works out of the box; the other 57 of `systems-all.yaml` block, need login or are dead | 4 |
| 7 | LLM key and model | `OPENROUTER_TOKEN` from the environment (`-e` or `--env-file`); default `google/gemini-2.5-flash-lite`; `--model` overrides | Each person pays for their own runs; cheapest model that fills forms | 1 |
| 8 | Code base | Merge PR #10 first, then branch from `master` | The image must carry the password guard and the POST requirement | - |
| 9 | Profile to prompt | Validate the YAML with pydantic (already installed with browser_use) and render the same Markdown sections the prompt uses today | `prompts/task.md` keeps working; the `.md` path stays for non Docker use | 2, 5 |
| 10 | Folder layout | One mounted `/data`: `/data/profile.yaml`, `/data/resume.pdf`, outputs under `/data/out` (trackers, screenshots, logs, applications, browser profiles); `fill.py` gets a data dir setting instead of fixed `Paths.ROOT` | One `-v` flag; outputs survive the container | 1 |
| 11 | Base image | `python:3.13-slim` + Debian `chromium`; deps from `uv.lock` via uv | Smaller image, project needs Python 3.13 (Playwright image ships 3.12). Size not measured yet | 4 |
| 12 | Per field value check | Yes, for fixed fields only | Structured profile makes the check possible; stops the made-up gender and salary seen on 2026-11-16 | 2, 5 |
| 13 | On a mismatch | Drop the input and tell the agent the profile value | Same pattern as the password and email guards | 12 |
| 14 | Chromium user and sandbox | Non-root user, Chrome sandbox off | Common container setup; isolation comes from Docker; no extra `docker run` flags | 11 |
| 15 | Distribution | ~~`docker build` from the repo, no registry~~ Changed the same day: GHCR `ghcr.io/iklobato/auto-form` now, Docker Hub `iklobato/auto-form` once the owner creates the account and adds the secrets | Users should not need a local build; GHCR needs no new account | 1 |
| 16 | Version | `v0.1.0`, image tags `0.1.0`, `0.1` and `latest` | First public version; the profile.yaml format may still change | 15 |
| 17 | Platforms | `linux/amd64` and `linux/arm64` | Runs natively on Intel/AMD and Apple Silicon | 15 |
| 18 | Release steps | PR and merge to `master`, git tag `v0.1.0`, GitHub Release, and a workflow that builds and pushes on every `v*.*.*` tag | Repeatable releases; GHCR uses the workflow token; Docker Hub is skipped until the repo secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` exist | 15 |

## Not decided / defaults taken without a question

- An example `profile.example.yaml` with made-up data ships in the repo (the repo is public).
- `save_note` writes to `/data/out` instead of the profile.
- Verification: build the image and run it against the local fake portal and a dry run on the
  built-in list, same checks as on 2026-11-16 (field compare, resume size, md5 of inputs).

## Open risks

- Field mapping for the value check (12) relies on the field label, name or placeholder that
  browser_use exposes. A misleading label can drop a correct value (accepted in 13).
- Headless only: the anti-bot portals stay out of reach (4).
- `--send` with the POST requirement can refuse a real application sent from a cross-site
  iframe (known cost from PR #10).
- Image size measured: 1.55 GB (amd64), more than the first guess of 600-800 MB.
- The image is public; it holds no personal data (`.dockerignore` is an allowlist; checked: no `.env`, no `knowledge/` inside).
- Running the 13 portals costs LLM money per run on the person's key; about US$ 0.96 for the
  13 portal dry run plus 2 local runs on 2026-11-16 (lagging usage counter).
