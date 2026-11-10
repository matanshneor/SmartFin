# SmartFin — Family Budget Management

A mobile-first PWA for managing a shared household budget. The interface is
Hebrew and right-to-left, and installs to the home screen. Visual language is
**"Gold Fintech"**: a dark hero, floating white cards, a gold accent, and
restrained charts and motion.

🔗 **Live at:** https://smartfin.up.railway.app

## Stack

- **Backend:** Python 3.11 + Flask (Jinja2 templates, served by gunicorn)
- **Database & auth:** Supabase (PostgreSQL + GoTrue), RLS enforced on every table
- **Frontend:** HTML + hand-written CSS + vanilla JS + Chart.js (no build step)
- **Receipt scanning:** OpenAI (`gpt-4o-mini`, vision)
- **Credit-card import:** `openpyxl` / `xlrd` / `pdfplumber` for the statement file, OpenAI only for unknown layouts and new merchants
- **Rate limiting:** Flask-Limiter — shared storage via `RATELIMIT_STORAGE_URI`
  (Redis in production), falling back to in-memory counters if it is unreachable
- **PWA:** service worker — stale-while-revalidate for static assets, network-only for API calls
- **Deployment:** Railway (live, region `europe-west4`)

## Project layout

```
SmartFin/
├── backend/
│   ├── app.py                  # all 86 routes: pages, API, auth, hardening
│   ├── clock.py                # Asia/Jerusalem — every "today" in the app comes from here
│   ├── money.py                # the one rule for showing an amount (agorot only when present)
│   ├── wording.py              # Hebrew counts, percentages that sum to 100, day labels, month names
│   ├── logs.py                 # logging setup
│   ├── supabase_config.py      # data layer: queries, auth, analytics, recurring engine
│   └── supabase/migrations/    # source of truth for the database schema (70 migrations)
├── frontend/
│   ├── templates/              # base, index, month, months, settings, projects,
│   │                           # project_detail, project_edit, login, onboarding,
│   │                           # reset_password, landing, privacy, terms, error,
│   │                           # and the partials _week_card, _notice
│   └── static/
│       ├── css/style.css       # the entire stylesheet
│       ├── js/                 # one script per page/feature; vendor/ holds Chart.js
│       ├── fonts/              # Rubik, self-hosted
│       ├── sw.js, manifest.json, icons/
├── tests/                      # 2117 tests: 2076 unit (no network) + 41 against the real DB
│   └── browser/                # manual Playwright checks (screenshots, not run in CI)
├── docs/SPEC.md                # product specification
├── Procfile / runtime.txt      # Railway configuration (gunicorn, Python 3.11.9)
└── requirements.txt / requirements-dev.txt / requirements.lock
```

## Features

### Main screens

- **Home** — the month's remaining balance (income − expenses − savings), quick
  transaction entry, and recent transactions ordered by insertion. Tapping a
  transaction expands an inline editor in place (amount, category, owner,
  description, date, plus save and delete); project and recurring settings open
  the full modal via "more options". The month's alerts appear here — and only
  here — when there are any; tapping one opens its category on the month page.
- **Month** — doughnut charts (where the money went: expenses vs. savings ·
  expenses, income and savings by category · split by family member) and every
  transaction of the month. A horizontally scroll-snapped
  strip at the top jumps between every month that has data. Categories expand
  inline to reveal the transactions behind them.
- **Comparison** — the monthly balance over time, with a drill-down into any month.
- **Settings** — profile, categories, projects and account, in an accordion layout.

### Transactions

- Expense / income / savings · edit, delete (with undo) and duplicate from
  anywhere · swipe gestures on mobile · "today / yesterday" quick chips
- Attribution to a family member with a stable per-member color tag (expenses and
  income; savings are always shared)
- **Recurring transactions** — an engine that materializes occurrences
  automatically, both retroactively and forward. Frequencies: monthly on the same
  date, on the 1st and 15th, weekly, and biweekly.
- **Receipt scanning** — photograph a receipt and the amount, merchant, date and
  category are extracted automatically (OpenAI vision). Two ceilings: 100 scans per
  family per month, and `RECEIPT_GLOBAL_MONTHLY_LIMIT` (default 2000) across all
  families, which is what protects the OpenAI bill from an open sign-up form.
  Manual entry is always available.
- **Credit-card import** (`/import`) — once a month, upload the transactions file
  downloaded from the card company (Max, Cal, Isracard and others: `.xlsx`, old
  `.xls`, `.csv`, the HTML-as-`.xls` some of them produce, or the monthly
  statement PDF, whose table is rebuilt from word positions). Columns are found
  by their Hebrew headers; an unknown layout goes to OpenAI (headers and three
  sample rows only), and failing that the user picks the columns once. Each
  transaction gets a category from the family's own history for that merchant,
  then keyword rules, then the card company's own sector column, then OpenAI. Anything already entered (same amount and
  merchant within 3 days) is flagged and left out; refunds are not imported;
  installment payments land in the month they are charged. Standing orders ("הוראת קבע") are offered — pre-ticked — as monthly recurring transactions, once per merchant and never when the family already has a series for it. Nothing is written
  until the user approves, and the file itself is never stored
  (`backend/card_import.py`).
- **Phone notifications** (Web Push, opt-in per device in Settings → Notifications):
  month closed (on the 1st), a family member joined, the month's salary was
  recorded, the last payment of an installment series, and a week with no new
  transactions. Encryption and VAPID signing are in `backend/push.py` (RFC 8291
  / 8292, on `cryptography`). The daily ones are sent by `pg_cron` calling
  `/internal/push/daily` with a shared secret held in Supabase Vault and in
  `PUSH_CRON_SECRET`; nothing is sent without `VAPID_PRIVATE_KEY`
- Saving a transaction fires a short haptic tap (`navigator.vibrate`, with a hidden
  `switch` input as the iOS fallback). There is no sound, so there is nothing to
  switch off.

### Projects (a trip, a renovation, an event…)

- Shared with the whole family or private to their owner, convertible in both directions
- Each carries an icon, description, budget target and its own dedicated categories
- A clean overview page (summary, doughnut chart, all transactions) with a
  separate edit screen for every setting
- **Project transactions are excluded from the monthly balance** — a one-off or
  capital expense does not distort the regular month. They are shown separately
  in the "projects this month" section of the month page, and are kept out of the
  home feed and the monthly category drill-downs.

### Analytics

- Default categories can be brought back from Settings → Categories → manage
  (`/api/categories/defaults`): only the missing ones are added, nothing changes
  or moves
- Per-category monthly budgets, with a progress bar and an overrun warning. An
  overrun is also an alert on the home page — the only kind of category alert
- CSV export of any month (`/month.csv`), and a full account export
  (`/account.json`) covering profile, categories, projects and every
  transaction — the data-portability right, which one month at a time
  did not actually serve

### Families and accounts

- Invite code plus an onboarding flow for picking a new family's categories. Codes
  expire after 7 days — a valid one is full read and write over the family's whole
  financial history — and the manager can mint a new one from settings. An expired
  code is renewed automatically the next time a member opens settings
- Sign in by email **or** phone · password reset · password change · profile editing
- A family manager role. Destructive actions — deleting a category, rotating the
  invite code, wiping the family's transactions — are the manager's alone; budgets,
  settings and adding categories stay shared. The rule is enforced in the database,
  not only in the routes.
- Member management: the manager can remove a member, and leaving a family hands
  the role on rather than orphaning it
- Transaction reset and permanent account deletion, backed by an internal
  owner-only archive (nothing is truly lost)
- "Remember me": a 90-day session with automatic token refresh. The window slides
  with use, so it only ever expires an abandoned session. Changing the password
  revokes every other device

### Hardening

- `SECRET_KEY` is mandatory — the app refuses to start without it
- Session cookies are `HttpOnly` + `SameSite=Lax`, and `Secure` outside development
- Request bodies capped at 8 MB; uploads restricted to JPG / PNG / WebP
- Rate limits per IP on every write route, not only the auth ones: 10/min on
  login, 5/min on signup, 3/min on password reset, 10/min on receipt scanning,
  60/min on transactions, 20–30/min on categories, projects, profile and family
  settings, and 3/hour on the two irreversible ones — resetting transactions and
  deleting an account. They are only as good as `RATELIMIT_STORAGE_URI`: without
  shared storage the counters are per-worker, so `/health` reports which it is
- Amounts, dates and categories are validated on every write path, and a write
  that matched no row reports that instead of "saved"
- `/health` (always 200, for the platform) and `/health/db` (503 when the database
  is unreachable, for an uptime monitor)

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in the keys
flask --app backend.app run --port 8080
```

> Note: the local server connects to the same Supabase project as production.
> Templates and Python are reloaded only on server restart (no auto-reload by default).

### Environment variables

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_KEY` | anon public key |
| `SECRET_KEY` | random string used to sign the session — **required; the app will not start without it** |
| `OPENAI_API_KEY` | OpenAI key — needed only for receipt scanning (without it the button is disabled) |
| `FLASK_ENV` | `development` locally only — **never set in production** (controls cookie hardening and HSTS) |
| `RATELIMIT_STORAGE_URI` | shared rate-limit storage (e.g. a Redis URL). Without it the counters are per-process, which means they barely limit anything across 4 workers — the app logs a warning when this happens in production |
| `RECEIPT_GLOBAL_MONTHLY_LIMIT` | scans per month across **all** families (default 2000) |
| `SENTRY_DSN` | error reporting. Request bodies, cookies and auth headers are stripped before sending |
| `RESEND_API_KEY`, `OWNER_NOTIFY_EMAIL` | an email to the site owner when a family finishes onboarding or someone joins one (family name, member count, and the name, email and phone of whoever registered or joined). Without both, nothing is sent |
| `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | phone notifications. The key is generated once (`backend.push.generate_vapid_keys`); the public half is derived from it. Without it the Settings toggle is hidden and nothing is sent |
| `PUSH_CRON_SECRET` | must match the `push_cron_secret` Vault secret; guards `/internal/push/daily` and the `push_daily` RPC |
| `LOGIN_LOOKUP_SECRET` | must match the `login_lookup_secret` Vault secret. Signing in with a phone number needs it: the `email_for_phone` RPC returns nothing without it, so the anon key alone can't be used to collect users' emails. Without it, phone sign-in fails (email sign-in still works) |
| `LOG_LEVEL` | `INFO` by default |
| `CSP_REPORT_ONLY` | set while tightening the Content-Security-Policy, so violations are reported instead of blocking |

## Tests

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -m unit -q      # 1723 tests, no network, ~15s
python3 -m pytest tests/ -q              # + 41 against the real Supabase project
```

CI runs the unit suite on every push, and Railway is configured to wait for it
before building — so a red test stops the deploy rather than reporting on one
that already shipped. Coverage is measured with a floor (55%; measured coverage is ~57%); it is
a floor, not a target, and exists so that coverage cannot disappear quietly the
way it once did over the transaction routes.

The 41 integration tests have no `unit` marker and are excluded from CI on purpose:
running them needs production keys, and they would also write to the real
database. Each run creates two throwaway accounts (each with its own family) and
deletes them at the end together with everything they left behind, archive rows
included — see `tests/_test_accounts.py`. Nothing test-made stays in the database;
an interrupted run is swept up by the next one. Creating them needs the linked
Supabase CLI (`supabase link`). Caution: more than 10 logins per minute from one
IP get a 429.

## Database

The schema lives entirely in `backend/supabase/migrations/`. For a fresh
Supabase project:

```bash
cd backend && supabase link --project-ref <REF> && supabase db push
```

- Every table has RLS enabled. Note that some tables were created with partial
  CRUD coverage — when adding a new write path, check the table's policies first.
- Owner-only internal tables (RLS enabled with **no** policies, so clients
  receive `[]`): `owner_archive`, an archive of everything deleted, and
  `login_events`, a sign-in log. Both are purged by `pg_cron` — 30 days and 90
  days — because an archive with no retention window is not an archive, it is a
  copy of the data the privacy policy promises to delete.

## Deploying to Railway

The app runs live on Railway (service `smartfin`).

**Critical for performance — region:** the Railway service must run in the same
geographic region as Supabase (here: Railway `europe-west4` against Supabase
`eu-central-1`). Otherwise every query crosses continents (~250 ms instead of
~30 ms) and pages become very slow. Change it under Railway → Service →
Settings → Regions.

**Shipping a new version:** `git push` to `main`. Railway deploys automatically,
and **Settings → Source → "Wait for CI"** is on, so the build waits for GitHub
Actions to go green first. `railway up` still works for a manual push but skips
that gate, so prefer the git path.

**Production environment variables:** `SUPABASE_URL`, `SUPABASE_KEY`,
`SECRET_KEY` (a strong random value, different from the local one),
`RATELIMIT_STORAGE_URI` (without it the limits are per-worker and nearly
meaningless), plus the optional `OPENAI_API_KEY` and `SENTRY_DSN`. Do **not** set `FLASK_ENV` — omitting it enables
secure cookies and HSTS. `PORT` is injected automatically (gunicorn binds to it;
defaults to 8080).

**Supabase Auth:** whenever the domain changes, update `SITE_URL` and
`URI_ALLOW_LIST` to the new domain, or password-reset links will break.

## Before you change anything

Three things in this repo are load-bearing in ways that are not obvious from the
code, and each one has already caused a real incident:

- **Do not add `--threads` to the Procfile, and do not switch to gevent or any
  async worker.** The Supabase client is a process-level singleton whose auth
  header is mutated per request. Sync workers make that safe because each one
  handles a single request at a time. With threads, one user's request runs under
  another user's token — one family reading another's money. The Procfile carries
  the full explanation.
- **Every "today" comes from `backend/clock.py`**, which is `Asia/Jerusalem`. The
  server runs on UTC, so `date.today()` rolls over three hours early and puts
  evening transactions in tomorrow. Tests that patch `datetime.date.today` instead
  of `clock.today()` silently test nothing.
- **`DataUnavailable` is raised, never swallowed.** A query that fails must not
  return zero: a false zero looks exactly like a real one, and the whole app is
  numbers. Returning `[]` or `0` on failure is how a family ends up seeing an
  empty month and believing it.

## Documentation

- `docs/SPEC.md` — the product specification, in Hebrew
- Comments in this codebase explain **why**, not what. Where one is long, it is
  usually because the obvious change there has already been tried and broke
  something.
