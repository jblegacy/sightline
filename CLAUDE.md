# CLAUDE.md — Sightline

Job-search pipeline. Ingests postings, screens them, assembles tailored resumes, drafts outreach. A human submits.

## Non-negotiable rules

Violating any of these is a bug, not a tradeoff. If a task seems to require breaking one, stop and ask.

1. **Never generate new resume bullet text.** Assembly selects and orders from the approved library in `bullets`. It does not write, rephrase, or "improve" a claim. If a bullet doesn't fit a posting, drop it — don't rewrite it.

2. **Never implement automated application submission.** No ATS form filling, no headless-browser submits, no API submission. ATS terms prohibit it, platforms detect it, and a flag in a shared instance follows the user to every company on it. The dashboard's job ends at "ready to submit."

3. **Never implement automated outreach sending.** Generate drafts. The user copies them into LinkedIn or their own Gmail and sends by hand. No SMTP integration, no LinkedIn API, no scheduling.

4. **Never automate anything against LinkedIn.** No scraping, no API, no browser automation. Generate search URLs a human clicks. Their terms prohibit the rest and account restriction is a real cost.

5. **The provenance validator gates every document build.** See below. Do not add a bypass flag, a "force" option, or a try/except that swallows it.

6. **Respect robots.txt, rate-limit every source, send an identifying User-Agent.**

7. **Store no third-party PII beyond name, public title, and public profile URL.** No personal emails, no phone numbers, no enrichment beyond what a person could read on a public profile.

8. **Service key server-side only. Storage bucket private, signed URLs only.**

## The provenance validator

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

Provenance values: `measured` (instrumented, with a number) · `stated` (asserted by the user, defensible from a document) · `modeled` (an estimate — ships only with a qualifier word in the text) · `derived` (inferred by a tool, including by a model — never ships).

This exists because a resume bullet once described a system as built when it had only been specified. Notes don't distinguish those states after a few weeks. The validator does.

## Architecture

- **Railway** — `worker` (scheduled Python, scoring/assembly/digest) and `web` (FastAPI + HTMX dashboard, also receives TheirStack webhooks)
- **Supabase** — Postgres + private Storage bucket for generated documents
- **TheirStack** — primary posting source; do not build ATS scrapers, this layer is bought.
  **Ingest is webhook-driven, not polled.** TheirStack's own docs recommend webhooks over periodic polling for exactly this use case, and it eliminates the duplicate-charge risk polling carries. The `settings` table still owns the query — the worker/web pushes it to TheirStack via `POST/PATCH /v0/saved_searches` and `/v0/webhooks`, so nothing is hand-configured in their app UI. `job.new`/`job.closed` events land on a `web` endpoint at 1 credit/job, same cost as polling. **Credits burn per job delivered, including repeats — there is no caching.** Never call Company Search or Technographics (3 credits each). Log credits consumed per event to `events`. A one-time backlog sweep (if ever done) must be tranched and bounded — verified backlog for the baseline query was 28,624 open postings, enough to burn ~19 months of a 1,500/month budget in one run if pulled naively.
- **Anthropic API** — Haiku for scoring, Sonnet for briefs and outreach drafts
- **Resend** — one daily digest email

## Conventions

- Python 3.12, `ruff` clean, type hints on public functions.
- No vector DB, no agent framework, no orchestration beyond the scheduler. The corpus is a few hundred rows. Keyword matching plus one model call is correct. Reaching for the sophisticated option here is a bug.
- Every pipeline stage writes to `events`. That table is why we can answer "why did this surface?" three weeks later.
- Model calls use structured output against a JSON schema. Never parse prose.
- Store `rubric_version` on every score so scoring behavior stays comparable across revisions.
- Log `cost_usd` per model call.

## The dashboard prototype is the API contract

`prototype/sightline-dashboard.html` defines the exact shape the API must return. The `P` array is the posting contract; `LIB` is the bullet-library contract. When wiring real endpoints, match those shapes rather than inventing new ones — the frontend is already built against them.

## Filtering vs flagging — important

The deterministic filter **archives** only: not remote, location-restricted outside the user's region, expired postings.

Comp below target and knockout requirements are **flags, not filters.** They surface in the queue with badges. The user decides. Do not add comp-based exclusion.

"Preferred" requirements are not knockouts. Only hard requirements — required years, required domain, required credential, mandatory onsite days — get flagged. Flagging soft preferences destroys the signal.

## Two filter layers — never merge them

**Fetch filters** go to TheirStack and cost credits. What they exclude, you never see and can't recover without paying again.

**Queue filters** run against already-stored rows and are free. They hide, never delete, and are always reversible.

Salary belongs in the queue layer by default. A fetch-time salary filter drops every posting with no published band — which is most of them, including well-paying roles. `fetch_salary_filter` defaults to false; do not change that default.

## Settings are data, not code

Query terms, filters, thresholds, and credit budget live in the `settings` table and are edited from the dashboard. Do not hard-code title lists or thresholds.

The Preview control must use `blur_company_data: true` so tuning costs nothing. Enforce `per_run_cap` in the worker as a hard stop — the UI value is a display of it, not the enforcement.

## Calibration

The rubric is unvalidated until it's been compared against the user's 20 hand-scored postings. Do not present scores as authoritative before that check runs. Score precision is coarse: treat anything within ~8 points as a tie.

## Build order

Ingest → filter → score → queue → answer library → assembly → outreach drafts. Metrics last.

Ship ingest and scoring before assembly. Discovery and screening carry most of the value; assembly is a convenience.
