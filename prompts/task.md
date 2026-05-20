{login_line}**Step 1 — Navigate ONCE** to this exact URL using the `navigate` action:
{url}

This URL is pre-filtered (when applicable) for Python/backend roles. Do NOT visit the
homepage, profile pages, settings, blog, or category browser — work from the page above.

{search_url_line}{best_for_line}{account_line}

## Hard rules — apply to every step

- **Goal**: open job listings → match → click Apply → submit forms. Reading is not the goal.
- **Remote only**: never type a city/state/country in any search field. If a location field
  is required, leave blank or type "Remote". Skip any role that is not fully remote.
- **No drifting**: if you've scrolled 3+ times without progress, the listings ARE what you see —
  start opening them. Don't use on-page search if you arrived via a pre-filtered URL.

## How to search on this portal
{search_instructions}

## Filtering guidance
{filter_notes_line}

---

## Per-listing flow

For each listing card visible on the page:

**1. Skip by title** (without opening) only if the title matches one of:
   Product Manager, Product Owner, Designer (UX/UI/Graphic), Marketing Manager, Sales Manager,
   Account Executive, Recruiter, HR, Talent Acquisition, Finance, Accountant, Legal Counsel,
   Customer Success, Customer Support, Executive Assistant, Office Manager.
   Everything else gets opened — when in doubt, OPEN.

**2. Open the listing**, read the title and description.

**3. Match generously** — counts as a MATCH if ANY of:
   - Title contains: engineer, developer, programmer, architect, SRE, DevOps, platform,
     infrastructure, backend, full-stack, data, AI, ML, software, cloud, systems, automation,
     lead, staff, senior, principal.
   - Description mentions: Python, Django, FastAPI, Flask, AWS, GCP, Azure, PostgreSQL,
     Kubernetes, Docker, microservices, REST, GraphQL, distributed systems, ML.
   - Default to MATCH when uncertain. If no match → skip to next listing.

**4. If MATCHED — apply.** This is the core of your job.

   a. `check_applied(job_url)`. If returns `already_applied` or `already_attempted` → skip.
      If `not_applied` → continue.
   b. Click "Apply" (also: "Apply Now", "Apply for this job", "Submit application",
      "Quick apply", "I'm interested"). If it opens a modal, work inside the modal.
      If it redirects to an external ATS (Greenhouse, Lever, Ashby, Workday) — continue there.
   c. Complete the apply flow (see "Apply flow" below).

**Anti-loop — strict. Read this every time you bail on a listing.**

After ANY bail (`already_applied`, `already_attempted`, `mark_attempted`, "no match", or
2 failed apply attempts), your VERY NEXT action must move to a **different** listing —
not the same one. Specifically:

- **Pick the NEXT listing in the list, not the same one.** Look at the search-results page
  and identify a job card whose title/URL is DIFFERENT from any you've already opened in
  this session. If the next visible listing is one you've already opened, scroll DOWN
  (not up) to find a fresh one.
- **Maximum 2 scrolls between listing-opens.** If after 2 scrolls you can't find an unopened
  listing, you're at the bottom of the page — paginate to the next page or call `done`.
- **Maximum 1 `go_back` per listing.** If you've gone back once and still can't find the
  next listing, you're confused — call `navigate` to the search URL directly instead of
  scrolling/going-back further.
- **NEVER call `go_back` two steps in a row.** If your previous action was `go_back` and you
  still need to navigate, use the `navigate` action with the search URL — `go_back` does
  nothing when there's nothing left in the back stack.
- **After `mark_attempted` your VERY NEXT action must be `navigate` to the search URL,
  NOT `go_back`.** This guarantees you return to a fresh listings page. Example:
  `mark_attempted(...)` → next step → `navigate(url="https://...search-url")`.
- **NEVER click a listing whose URL matches one already opened this session.** If you see
  yourself about to click the same job card twice, scroll past it.
- If you've opened 5 different listings on this page without applying to ANY of them
  (all skipped/non-match/already-attempted), the page is exhausted — paginate or `done`.

Other anti-loop rules:
- Max 2 attempts per listing → `mark_attempted` and move on. Never a third attempt.
- External redirect to a company site you can't navigate in 3 clicks →
  `mark_attempted(reason="external_redirect")`.
- Apply requires a fresh account on a brand-new ATS with no SSO →
  `mark_attempted(reason="login_required")`.

---

## Apply flow

### Account / sign-in screens

**ALWAYS prefer SSO over self-registration**, in priority order:

1. **"Continue with LinkedIn"** → click → on the LinkedIn login page fill:
   `Email = <linkedin_sso_email>`, `Password = <linkedin_sso_password>` → Sign in →
   approve any OAuth consent. Do NOT call `save_credentials` for SSO (cookies persist).
2. **"Continue with Google"** → skip if no Google credentials available.
3. **"Apply with Indeed"** → use only if portal explicitly accepts it.

**Self-register only if NO SSO is offered.** Fill: First name = `<first_name>`,
Last name = `<last_name>`, Email = `<primary_email>`, Password = `<portal_password>`.
Click create. Immediately call
`save_credentials(email, password)`. If "verify email" appears, also call `save_note` and
mark the application partial.

### Filling the form (single rule set — applies everywhere)

- **Structured fields** (text, dropdowns, checkboxes, radio): copy verbatim from
  "Form Quick-Reference" in my profile. Do not paraphrase.
- **Long-form text** (cover letter, bio, "about yourself"): one paragraph, 3–5 sentences,
  plain B2 English, varied sentence length. Pick the most relevant cover letter variant
  from "Cover Letters" in my profile and trim to fit.
- **Unknown/unusual fields**: answer honestly with the closest profile data. Never invent
  facts, never leave a required field blank.
- **File uploads**: upload the resume PDF when prompted: {resume_line_inner}
- **Multi-page forms**: fill all visible fields on each page, then click Next / Continue.
  Don't skip optional fields if you have a relevant answer.

### Submit and record

1. Before clicking the final Submit/Send button → call `save_screenshot`.
2. Click Submit.
3. Wait for the thank-you / confirmation page.
4. Call `mark_applied(job_url, job_title, ...)` with as much context as the listing gave you:
   `salary`, `location`, `seniority`, `employment_type`, `key_requirements`,
   `cover_letter_used`, `notes`. All optional except url + title, but greatly improves the audit log.

**The application is complete ONLY when a confirmation page is visible** or the site explicitly
states the application was received. Clicking Continue / Next / Create account / Sign in alone
is NOT completion.

## Form-filling notes for this portal
{form_hints}

## Application notes
{application_notes_line}

{resume_line}{filter_line}{note_line}

---

## Finishing the portal

You're DONE — and that's correct, NOT a failure — when ONE of these is true:

1. **You attempted apply on every matching role on every page.** (Best outcome — apply happened.)
2. **You opened at least one listing, and confirmed every visible role is filtered out by
   the REMOTE ONLY rule** (e.g., every job is "On-site" only). Call `done` with a note like
   "All N roles are on-site only — REMOTE ONLY rule excludes the entire portal."
3. **You scrolled through the entire listings page (saw the footer), and zero roles match
   the title-skip / engineering criteria.** Call `done` with the role count.
4. **You triggered the loop-detector or hit max_attempts on every visible matching role.**
   Call `done` with a brief summary of what blocked each.

Concrete failure modes (DO NOT do these):
- Calling `done` after only viewing the careers page WITHOUT scrolling to see all roles.
- Calling `done` while there are still UNCHECKED listings on the page.
- Re-navigating to the same listings page over and over without making progress
  (use `done` with a "no remote matches" reason instead).
- Pretending you applied when you didn't.

Then:
1. Call `save_note` with portal-specific quirks (field limits, ATS redirects, login flow
   issues, anything non-obvious). Skip if nothing unusual.
2. Report a summary table — "Applied?" should be "yes" for every match unless you can name
   a concrete blocker (already_applied, captcha, missing credentials, etc.):

| Role | Match? | Applied? | Notes |
|------|--------|----------|-------|
...

## My professional profile

{knowledge}
