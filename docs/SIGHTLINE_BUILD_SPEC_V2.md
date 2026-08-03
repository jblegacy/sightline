# Sightline — Build Spec v2 (Railway + Supabase)

Supersedes v1. Same thesis, concrete stack.

**Thesis:** Automate discovery, screening, and assembly. Keep a human on submission, free-text answers, and outreach — by design. See §9.

---

## 1. Architecture

```
                    ┌──────────────────────────── RAILWAY ────────────────────────────┐
                    │                                                                  │
  TheirStack ──────▶│  worker (Python)                                                 │
  Adzuna (free)     │   ├─ 02:00 UTC  ingest      → upsert → postings                  │
                    │   ├─ 02:30 UTC  filter      → deterministic pass                 │
                    │   ├─ 03:00 UTC  score       → Haiku → scores + contact extract   │
                    │   ├─ 03:30 UTC  assemble    → .docx + outreach drafts            │
                    │   └─ 12:00 UTC  digest      → Resend → your inbox                │
                    │                                                                  │
                    │  web (FastAPI + HTMX)                                            │
                    │   └─ review queue · tracker · resume downloads                   │
                    └──────────────────────────────┬───────────────────────────────────┘
                                                   │
                                        ┌──────────▼──────────┐
                                        │      SUPABASE       │
                                        │  Postgres + Storage │
                                        └─────────────────────┘
```

**Why FastAPI + HTMX for the dashboard:** you need a table, a few buttons, and file links. No build step, no React toolchain, server-rendered HTML with HTMX for the interactions. One Python service, one language across the whole system.

**Services:** two Railway services off one repo (`worker` and `web`), or one service with the scheduler in-process. Two is cleaner — a crashed scraper shouldn't take the dashboard down.

**Verify before you build:** current Railway pricing/limits and Supabase free-tier limits both change; check them rather than trusting this document.

---

## 2. Environment

```
SUPABASE_URL=
SUPABASE_SERVICE_KEY=          # server-side only, never in the browser
THEIRSTACK_API_KEY=
ADZUNA_APP_ID=                 # free tier, second source
ADZUNA_APP_KEY=
ANTHROPIC_API_KEY=
RESEND_API_KEY=
DIGEST_TO=james@beamlegacy.com
DASHBOARD_PASSWORD=            # single-user basic auth is sufficient
SCORE_THRESHOLD=70
```

---

## 3. Schema (Postgres)

```sql
create table companies (
  id            bigserial primary key,
  name          text not null,
  domain        text,
  ats_type      text,                      -- greenhouse|lever|ashby|workday|scrape
  ats_token     text,
  careers_url   text,
  tier          smallint default 2,        -- your target-list tier
  active        boolean default true,
  notes         text
);

create table postings (
  id             bigserial primary key,
  company_id     bigint references companies(id),
  external_id    text,
  title          text not null,
  url            text not null,
  location_raw   text,
  remote_flag    text,                     -- true|false|unclear
  comp_min       int,
  comp_max       int,
  comp_source    text,                     -- posted|inferred|absent
  posted_at      timestamptz,
  first_seen_at  timestamptz default now(),
  last_seen_at   timestamptz default now(),
  content_hash   text,
  jd_text        text,
  raw            jsonb,
  status         text default 'new',       -- new|filtered|scored|queued|applied|archived|expired
  filter_reason  text,
  unique (company_id, external_id)
);
create index on postings (status, first_seen_at desc);

create table scores (
  id                  bigserial primary key,
  posting_id          bigint references postings(id) on delete cascade,
  rubric_version      text not null,
  model               text not null,
  total               int not null,
  dimensions          jsonb not null,
  rationale           text not null,
  keywords            jsonb,               -- exact phrases to mirror
  matched_bullet_ids  jsonb,
  unmet_requirements  jsonb,
  suggested_variant   text,                -- engineer|leadership
  reports_to          text,                -- extracted: "Chief Product & Technology Officer"
  named_contacts      jsonb,               -- [{name, title, context}] if the JD names anyone
  target_titles       jsonb,               -- ["VP Engineering","Head of Ops"] — who to look for
  company_signals     jsonb,               -- funding, launches, growth cues found in the JD
  cost_usd            numeric(10,5),
  created_at          timestamptz default now()
);
create index on scores (posting_id);

create table bullets (
  id                bigserial primary key,
  ref               text unique not null,  -- BL-014
  text              text not null,
  source_org        text,
  source_period     text,
  tags              text[] not null,
  variants          text[] not null,       -- which resume variants may use it
  provenance        text not null,         -- measured|stated|modeled|derived
  evidence_url      text,
  evidence_note     text,
  status            text default 'verified',
  last_verified_at  date
);

create table variants (
  id              bigserial primary key,
  posting_id      bigint references postings(id) on delete cascade,
  kind            text not null,           -- engineer|leadership
  bullet_refs     text[] not null,
  summary_key     text not null,
  storage_path    text,                    -- Supabase Storage object path
  created_at      timestamptz default now(),
  approved_at     timestamptz,
  approved_notes  text
);

create table applications (
  id                    bigserial primary key,
  posting_id            bigint references postings(id),
  variant_id            bigint references variants(id),
  submitted_at          timestamptz,
  channel               text,              -- ats|email|referral
  freetext_answers      text,              -- keep for reuse as reference
  status                text default 'draft',
                        -- draft|submitted|rejected|screen|interview|offer|ghosted
  follow_up_due         date,
  outreach_contact      text,
  outreach_sent_at      timestamptz,
  notes                 text,
  updated_at            timestamptz default now()
);
create index on applications (status, follow_up_due);

create table outreach (
  id                      bigserial primary key,
  posting_id              bigint references postings(id) on delete cascade,
  target_name             text,            -- you fill this from LinkedIn
  target_title            text,
  target_linkedin_url     text,
  draft_linkedin_note     text,            -- short-form, connection-request length
  draft_linkedin_message  text,            -- longer-form DM / InMail
  draft_email_subject     text,
  draft_email_body        text,
  sent_at                 timestamptz,
  sent_channel            text,            -- linkedin_note|linkedin_message|email
  replied_at              timestamptz,
  follow_up_due           date,
  notes                   text,
  created_at              timestamptz default now()
);
create index on outreach (sent_at, follow_up_due);

create table settings (
  id                  int primary key default 1,     -- single row
  title_include       text[] not null,
  title_exclude       text[] not null,
  remote_only         boolean default true,
  open_only           boolean default true,
  direct_employer     boolean default true,
  countries           text[] default '{US}',
  min_employee_count  int default 50,
  employment_types    text[] default '{full_time,contract}',
  monthly_credits     int default 1500,
  per_run_cap         int default 120,               -- hard stop; a bad query can't drain the month
  score_threshold     int default 70,
  comp_target         int default 150000,
  comp_low_line       int default 90000,
  hard_knockouts_only boolean default true,
  fetch_salary_filter boolean default false,     -- off by default; see Criteria view
  fetch_salary_min    int,
  fetch_salary_max    int,
  queue_salary_min    int default 0,
  queue_salary_max    int default 500000,
  queue_min_score     int default 55,
  queue_max_age_days  int default 21,
  queue_ko_tolerance  int default 9,
  queue_include_no_salary boolean default true,
  rubric_version      text default 'v1',
  updated_at          timestamptz default now()
);

create table events (
  id           bigserial primary key,
  entity_type  text not null,
  entity_id    bigint,
  event        text not null,
  payload      jsonb,
  created_at   timestamptz default now()
);
```

`events` is the audit log — every stage writes to it. It's what lets you answer "why did this surface?" three weeks later, and it's what makes the system defensible rather than a black box.

**Storage bucket:** `resumes`, private. Objects at `resumes/{posting_id}/{kind}-{timestamp}.docx`. Serve through signed URLs from the dashboard; never make the bucket public.

---

## 4. Stage 1 — Ingest (webhook-driven)

All of R1/R2/R3/R4/R5/R6/R9 below are now empirically verified against a live account, not just documentation — see `docs/THEIRSTACK_API_REFERENCE.md` for the full reference. This section supersedes the original polling design: **ingest is webhook-driven**, per TheirStack's own guidance (their docs explicitly recommend webhooks over periodic polling for this exact use case, and it removes the duplicate-charge risk polling carries).

**Buy this layer, don't build it.** TheirStack aggregates from 315k+ sources including LinkedIn, Indeed, Glassdoor and 16k+ ATS platforms (Greenhouse, Lever, Workable), deduplicates automatically, normalizes salary and location into structured fields, and delivers job descriptions as Markdown. That removes the ATS adapters, the dedupe logic, the HTML parsing, the robots.txt/rate-limit handling, and the salary-extraction problem.

```
sources/
  theirstack.py   # saved-search + webhook management client
  adzuna.py       # free second source, register at developer.adzuna.com
web/
  routes/theirstack_webhook.py   # POST endpoint receiving job.new / job.closed
```

**How settings stay data, not code:** `/v0/saved_searches` and `/v0/webhooks` both have full CRUD (`POST`/`GET`/`PATCH`). When the `settings` row changes (title include/exclude, scope filters, credit budget), the app pushes the update via `PATCH /v0/saved_searches/{id}`, so nothing is hand-configured in TheirStack's app UI — the dashboard remains the single source of truth.

**Credit model.** 1 credit per job delivered via `job.new`/`job.closed`, same rate as polling, no caching — repeated delivery of the same job charges again. `job.closed` fires when a tracked posting closes, useful for keeping `postings.status` current without a second poll.

**Tune with free modes before subscribing.** `blur_company_data: true` returns blurred results at 0 credits — verified empirically (balance unchanged across repeated free calls). Free count is `limit: 1` **plus** `include_total_results: true`; `include_total_results` reads the whole matching dataset and slows the response, so enable it only on the first page, not every page. `hiring_team` is **not** blurred in preview mode (verified empirically — masked `company`/`description` next to a real `hiring_team` array on the same record), so preview mode is enough to validate outreach coverage for free.

**One query, not many.** Separate queries with overlapping title lists double-charge for jobs matching both ("ai engineer" + "automation engineer" both match "AI Automation Engineer"). Use one query with an OR'd title array. Split only when filters genuinely differ, and keep title lists disjoint.

**Request constraint:** TheirStack requires at least one of `posted_at_max_age_days`, `posted_at_gte`, `posted_at_lte`, `company_domain_or`, `company_linkedin_url_or`, or `company_name_or` on every Job Search / saved-search call; `discovered_at_gte` alone does not satisfy it and the request fails validation.

**Baseline saved-search filters** (same shape used for both the saved search and any manual/backfill query):

```json
{
  "posted_at_max_age_days": 30,
  "remote": true,
  "job_country_code_or": ["US"],
  "is_closed": false,
  "company_type": "direct_employer",
  "employment_statuses_or": ["full_time", "contract"],
  "job_title_or": ["ai engineer","automation engineer","ai enablement","workflow automation",
                   "internal tools engineer","integration engineer","business systems analyst",
                   "ai operations","business automation","ai implementation"],
  "job_title_not": ["sales engineer","recruiter","intern"],
  "min_employee_count_or_null": 50,
  "limit": 100
}
```

No salary floor in the query — low-comp roles surface flagged, not filtered, and a salary filter would drop every posting without a published band.

**Do not use Company Search or Technographics** — 3 credits each vs 1 for a job.

**`hiring_team` arrives free but fills rarely.** Measured fill rate on a 100-record live sample: **11%** — below the 15% threshold for leading outreach with a named contact. The Outreach panel's primary path is the generated LinkedIn search link; a populated `hiring_team`/`manager_roles` is a bonus, not the default UX.

**Budget — verified against a live account:**
- Steady-state volume (7-day average, full production filters): **42.4/day ≈ 1,273/month** — matches the original 1,500/month budget assumption well. (A single-day count showed 9; that was daily noise, not signal — use a multi-day window for any volume check.)
- **Backlog is much larger than assumed.** Full-filter, no-`discovered_at`-bound count of currently-open matching postings: **28,624**. At 1,500 credits/month that's ~19 months of budget in one sweep — do not run an unbounded backlog pull. If a historical sweep is wanted at all, bound it tightly (e.g. last 30–60 days via `posted_at_max_age_days`) and size it with free count first.
- Unused *paid* credits roll over 12 months. **Free credits do not** — this account's free grant shows an explicit `earliest_expiration` about a month out from grant, contradicting the earlier assumption of "no time limit."
- Reposts: a repost is deduped server-side (no recharge) if the *original* posting is within the last 30 days; if the original is 30+ days old, a repost resurfaces as "new" and recharges — documented, expected behavior.
- Rate limit, verified from live response headers: **4 req/sec, 10/min, 50/hour, 400/day** on the free tier (a since-superseded blog post claims 2/sec — don't trust it; the dedicated rate-limit reference page and live headers agree on 4/sec).

**Instrument burn from day one.** Log credits consumed per webhook event into `events`, and surface a burn-rate figure on the metrics view. Poll `/v0/teams/credits_consumption` and `/v0/billing/credit-balance` (hyphen, not underscore).

## 5. Stage 2 — Filter (02:30), no model calls

Cheap, deterministic, can't hallucinate. Corrected against CLAUDE.md, which is authoritative where the two disagree:

- Not remote → archive (`postings.remote_flag == 'false'`; TheirStack's own `remote: true` fetch filter already does most of this work, this is a safety net for ambiguous cases)
- Red-flag phrase hit → archive, **only if `settings.red_flag_phrases` is non-empty**. No phrases are defined anywhere yet — not invented here, since that's editorial judgment about what disqualifies a posting, not something to guess at. Starts as a no-op until filled in from the dashboard.

**Removed from this stage, moved elsewhere:**
- ~~Comp posted and below floor → archive~~ — CLAUDE.md is explicit and non-negotiable: comp is a **flag, not a filter**. Never archive on comp. (The original spec draft got this wrong; CLAUDE.md wins.)
- ~~Location restricted outside OR/US-remote → archive~~ — detecting this reliably needs judgment (reading what the JD actually says about location), not a keyword list nobody's written. Handled instead as a knockout flag during scoring (Stage 3), consistent with CLAUDE.md's "hard requirements get flagged, not filtered."
- ~~Posted >21 days ago → deprioritize~~ — this is a **queue-layer** concern (`settings.queue_max_age_days`, already in the schema), not a Stage 2 archive. Two filter layers, never merged — see CLAUDE.md.

Always write `filter_reason`. When you later find the filter was too aggressive, that column is how you prove it.

---

## 6. Stage 3 — Score (03:00)

**Model:** Haiku for scoring. It's a classification-and-extraction task against a rubric, not a reasoning problem, and cost matters at nightly volume. Escalate to Sonnet only for the briefs in §7.

**Use structured output.** Define the score object as a JSON schema and require conformance — don't parse prose. Rubric v1:

| Dimension | Max |
|---|---|
| Role fit | 25 |
| Evidence overlap (verified bullets ↔ requirements) | 20 |
| Seniority & scope | 15 |
| Remote authenticity | 15 |
| Comp signal | 15 |
| Company & stage fit | 10 |
| Red flags | −30 |

**Also extract, in the same call (free — it's already reading the JD):**

- `reports_to` — verbatim if stated ("reports to the Chief Product & Technology Officer")
- `named_contacts` — any person the JD names, with their title and the surrounding context
- `target_titles` — who you'd plausibly want to reach if nobody is named, inferred from the role's level and function
- `company_signals` — funding, launches, growth, or team-building cues mentioned in the posting. These become the opening line of your outreach, so pull them verbatim rather than paraphrasing.

**Also return:** written rationale (required — a score you can't read is a random number with good manners), exact keyword phrases, matched bullet refs, unmet requirements, and `suggested_variant` (engineer vs leadership, keyed off JD vocabulary — build/ship/implement language → engineer; roadmap/standards/adoption/stakeholder language → leadership).

**Validate before trusting it.** Hand-score 20 postings, run the rubric against them, check agreement. Store `rubric_version` so you can compare behavior after you revise it.

**Thresholds:** ≥70 → queue. 55–69 → watchlist. <55 → archive with rationale retained.

---

## 7. Stage 4 — Assemble (03:30)

1. Select bullets by tag overlap with extracted keywords, filtered to the suggested variant, capped per employer section. **Selection only — never generate new bullet text.**
2. Pick a summary from the pre-approved set (one per variant), not a generated one.
3. Run the provenance validator (§9) — refuse to build on violation.
4. Render `.docx` with the existing build script, upload to Storage.
5. Generate a **one-page brief** (Sonnet is worth it here): score rationale, keywords mirrored, gaps to prepare for, three talking points for this JD.

The brief is what you'll actually value. The tailored resume saves 40 minutes; the brief is what makes you sound prepared in the screen.

---

## 8. Dashboard and digest

### Dashboard (`web`)

Single-user basic auth. Four views:

**Queue** — scored ≥70, not yet actioned. Columns: company, title, score, one-line rationale, posted date, age. Row expands to full rationale, keywords, gaps, and the brief. Actions: **Approve** (generates/reveals the tailored resume) · **Reject** (with a reason — this is rubric training data) · **Defer**.

**Applications** — the tracker you asked for. Columns: company, title, variant used, **resume file** (signed download link), **submitted at**, status, follow-up due, notes. Inline editable status. A **Mark submitted** button that stamps `submitted_at` and sets `follow_up_due = today + 7`. This is the record of what you sent and when, per submission.

**Watchlist** — the 55–69 band, resurfaced when the queue is thin.

**Metrics** — six counters, one query each: median hours from first-seen to submitted (the headline number), ingested, surfaced, submitted, replies, credit spend.

**Criteria** — two clearly separated layers, because they have different costs and different consequences.

*Fetch criteria (costs credits)* — title include/exclude, scope filters, optional salary filter (off by default), seniority, source exclusions, credit budget with per-run hard cap. Every change here changes what you pay for and what you never see.

*Queue filters (free)* — salary range shown, minimum score, max age, knockout tolerance, include/exclude unposted-salary postings. These filter the display only. Nothing is deleted, every change is reversible, and the view shows "N of M fetched postings shown" so it's always obvious what's being hidden.

The distinction matters: a salary filter at fetch time silently discards every posting without a published band, permanently. The same filter at queue time is free and reversible. Default salary filtering to the queue layer.

**Settings** — edits the `settings` row. Title include/exclude as tag inputs, server-side filters, credit budget with a per-run hard cap, and score/comp thresholds.

The critical control is **Preview**, which calls the search endpoint with `blur_company_data: true` (free) and reports new-per-day, last-7-days, and full-backlog counts, then compares daily volume against the monthly budget and says plainly whether the query is too broad, too narrow, or well sized. Query tuning without it is guessing with real money. Enforce `per_run_cap` in the worker, not just the UI.

**Outreach** — per queued posting, everything you need for LinkedIn research and a send:

| Field | Source |
|---|---|
| Reports to | extracted from the JD |
| Named contacts | extracted from the JD, if any |
| Who to look for | inferred target titles |
| LinkedIn people search | generated deep link (see below) |
| Company signals | extracted verbatim, for your opening line |
| Target name / title / profile URL | **you fill these in** after your LinkedIn research |
| Three drafts | generated on demand once you've named the target |
| Sent · channel · replied · follow-up | you mark; drives the digest |

**LinkedIn deep link.** Generate a pre-filled people-search URL scoped to the company from the row so research is one click, not five. Build it from the company name and the inferred target titles. This is a link a human clicks — no scraping, no automation against LinkedIn, which their terms prohibit.

### Outreach drafts

Generated only after you've entered a target name, so the drafts can actually address a person. Three variants, each with a copy button:

| Variant | Length target | Notes |
|---|---|---|
| LinkedIn connection note | short — cap is a few hundred characters and LinkedIn changes it | One signal, one proof point, one ask. No links. |
| LinkedIn message / InMail | medium | Room for a second sentence of proof. |
| Email | ~80 words | Subject under 10 words. Plain text. No tracking, no attachment. |

Make the length caps config values, not hard-coded — LinkedIn's limits move.

**Composition rules for the generator** (these are the prompt, and they're why it works):

1. Open with one specific company signal from `company_signals`, verbatim. Never "I noticed you work at X."
2. Exactly one metric from the bullet library, chosen for relevance to this JD. One. Stacking wins muddies the message.
3. State what you bring, not what you want.
4. Close with a permission-based ask — "worth a 15-minute conversation?" Never a calendar link, never "apply here."
5. Subject line: specific, under 10 words, never "Seeking Opportunities" or anything resembling a bulk send.
6. Plain text only. No HTML, no tracking pixel, no attachment.
7. Draft only — the dashboard never sends. You copy into LinkedIn or your own Gmail and send by hand.

Rule 7 is not a limitation to work around later. Sending plain text from your own authenticated Gmail inherits your SPF/DKIM alignment and personal sender history; any bulk-sending layer degrades deliverability and buys nothing at this volume.

**Follow-up discipline:** one follow-up after 7 days, then stop. `follow_up_due` drives the digest reminder.

### Daily digest (12:00 UTC, Resend)

Plain HTML, scannable in 30 seconds:
- New in queue: company · title · score · one-line rationale · link
- Follow-ups due today
- Applications with no response past 14 days
- One-line stats: ingested / queued / submitted this week

Keep it short. A digest you skim is worth more than a report you don't open.

---

## 9. Guardrails (non-negotiable, and the interview story)

| # | Rule | Why |
|---|---|---|
| 1 | No new bullet text generated at assembly time. Selection only. | A model writing fresh resume prose will promote a plan to an accomplishment, in your voice. |
| 2 | No automated submission. Ever. | ATS terms prohibit it, major platforms detect it, and a flag in a shared instance can follow you to every company on it. |
| 3 | No automated outreach send. Identify a contact; write the message yourself. | Templated outreach at volume is the most detectable thing in this market and burns the contact permanently. |
| 4 | Provenance validator gates every build. | See §9.1. |
| 5 | `robots.txt`, rate limits, identifying User-Agent. | Don't get blocked mid-search. |
| 6 | No third-party PII beyond name, public title, public profile URL. | Hiring managers are people. Don't build a dossier. |
| 7 | Service key server-side only; Storage private with signed URLs. | Your resume history isn't public. |
| 8 | Never automate anything against LinkedIn — generate links a human clicks. | Their terms prohibit scraping and automation, and account restriction is a real cost. |
| 9 | The dashboard drafts; you send from your own inbox, by hand, plain text. | Deliverability, and a note you didn't read is a note that reads like it. |

### 9.1 The provenance validator

```python
QUALIFIERS = ("estimated", "modeled", "projected", "approximately")

def assert_shippable(bullets):
    for b in bullets:
        if b.provenance == "derived":
            raise ProvenanceError(f"{b.ref}: derived claims never ship")
        if b.provenance == "modeled" and not any(q in b.text.lower() for q in QUALIFIERS):
            raise ProvenanceError(f"{b.ref}: modeled claim needs an explicit qualifier")
        if b.status != "verified":
            raise ProvenanceError(f"{b.ref}: status={b.status}")
```

Ten lines. The most valuable code in the repo.

---

## 10. Cost

| Item | Estimate |
|---|---|
| Railway (2 services, low traffic) | Hobby-tier usage — verify current pricing |
| Supabase | Free tier is sufficient at this scale — verify current limits |
| TheirStack | Tier-dependent; ~1.5–2k postings/month lands roughly **$10–25/month** — verify tiers |
| Adzuna | Free |
| Anthropic API — scoring | ~100 postings/night on Haiku with short JD text: roughly **$1–3/month** |
| Anthropic API — briefs + outreach drafts (Sonnet) | Only on approved items, ~12/week: **under $2/month** |
| Contact discovery tooling | **$0** — manual LinkedIn research |
| Resend | Free tier covers one daily email |

Log `cost_usd` per scoring run into `scores`. You'll be able to state your actual monthly cost in an interview, which is a better answer than an estimate.

---

## 11. Build order

| Phase | Scope | Effort |
|---|---|---|
| 0 | Supabase project, schema, `bullets` seeded from both resume variants (~45 rows), provenance validator | 1 day |
| 1 | Railway repo, TheirStack + Adzuna clients, nightly ingest, `postings` filling up | 1 evening |
| 2 | Deterministic filter + Haiku scoring with structured output + hand-validation against 20 postings | 1 weekend |
| 3 | Dashboard: queue + applications tracker + signed downloads | 1 weekend |
| 4 | Assembly (docx + brief), outreach drafts, digest email | 3–4 evenings |
| 5 | Metrics view, response-rate-by-variant | 2 evenings |

**Ship 1 and 2 before 3 and 4.** Discovery and screening carry nearly all the value. If you never build assembly, the pipeline has already paid for itself — and Phase 3 gives you the tracker you asked for, which is useful even with manual tailoring.

Keep a dated decisions log in the repo as you go. It's the raw material for the case study, and handing an interviewer a decisions log is disarmingly strong.

---

## Appendix A — the $0 hybrid

If you'd rather avoid API billing entirely: Railway keeps ingest, filter, database, dashboard, and digest (no model calls anywhere). Scoring runs on your Mac as a launchd job — `claude -p --bare --output-format json --json-schema ...` authenticated with `claude setup-token` — pulling unscored rows from Supabase and writing scores back.

Tradeoffs: your Mac has to be awake nightly, subscription usage limits apply, and you should confirm that unattended batch use sits within current subscription terms. Always pair `--print` with an explicit permission mode or the script hangs silently waiting for approval, and use `--bare` so an automated run doesn't pull in ambient config.

Verify current terms at docs.claude.com before relying on this path.
