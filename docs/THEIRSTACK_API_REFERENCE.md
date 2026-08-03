# TheirStack API — Compiled Reference

Originally assembled from official documentation on 2026-07-31 because the rendered doc pages were returning 503s in that environment. **Updated 2026-08-03 with a live account** — every item that was previously ⚠️/❓ below has now been empirically verified (real API calls, balance checked before/after) or confirmed against the live OpenAPI spec and current doc pages, which were reachable this session. See §10 for the full verification log.

**Marker key:** ✅ verified live (docs + real API behavior agree) · ⚠️ documented but not independently tested · ❌ a prior assumption that turned out wrong, corrected here.

---

## 1. Auth and base

✅ Base URL: `https://api.theirstack.com`
✅ Auth: `Authorization: Bearer <api_key>`
✅ Keys created at Settings → API Keys. Shown once. Expiry is a date or Never. Revocable, immediate, irreversible.

```
POST  /v1/jobs/search                    1 credit per job returned
POST  /v1/companies/search               3 credits per company
POST  /v1/companies/technographics       3 credits per company (all techs for that company)
GET   /v0/billing/credit-balance         free   (hyphen, not underscore — ❌ corrected)
GET   /v0/teams/credits_consumption      free
GET   /v0/catalog/keywords | /technologies | /industries | /locations

POST/GET   /v0/saved_searches            manage saved searches (source for webhooks)
GET/PATCH  /v0/saved_searches/{id}
PATCH      /v0/saved_searches/{id}/archive
POST/GET   /v0/webhooks                  manage webhooks
GET/PATCH  /v0/webhooks/{id}
PATCH      /v0/webhooks/{id}/status
PATCH      /v0/webhooks/{id}/archive
GET        /v0/webhooks/{id}/events
GET        /v0/webhooks/{id}/events/count
POST       /v0/webhooks/events/retry
POST       /v0/webhooks/test
GET        /v0/webhooks/event-types
```

✅ Confirmed from the live OpenAPI spec (`https://api.theirstack.com/openapi.json`) — pull it directly rather than trusting this file for anything schema-level; it's the definitive source.

**Use Job Search + webhooks only.** Company Search and Technographics cost 3× and we don't need them.

---

## 2. Credits — the constraint that shapes everything

✅ 1 credit per **job delivered** — same whether it comes back from `POST /v1/jobs/search` or a `job.new`/`job.closed` webhook event.
✅ Credits are consumed only when data is returned, or when a webhook event dispatches.
✅ **No caching.** Repeated calls without dedup filters charge for the same jobs multiple times.
✅ Unused **paid** credits roll over up to 12 months.
❌ **Free credits do not have "no time limit."** This account's real balance shows `earliest_expiration: 2026-09-03` — about a month after grant. Free credits expire; budget accordingly.

### Dedup mechanics (✅ verified against live docs, not just inferred)

- **`discovered_at_gte`** — pass a timestamp higher than your last call, format `YYYY-MM-DDTHH:MM:SSZ` UTC. Per TheirStack's own guide: *"it should be the date and time of the last job you fetched"* — i.e. `MAX(discovered_at)` among jobs actually processed, not your run's wall-clock start. A failed/partial run resumes safely from this value with no gap and no double charge.
- **`job_id_not`** — array of job IDs to exclude, useful when the same job could appear across multiple searches. No documented max array size (checked the OpenAPI schema directly — none stated).
- **Reposts**: if a job's *original* posting date is within the last 30 days, a repost is deduped server-side and won't resurface or recharge. If the original posting is 30+ days old, a repost **will** resurface as a "new" discovery and recharge — this is documented, intentional behavior, not a bug to guard against.
- **Pagination double-charge**: not empirically tested (would require spending real paid credits with no real design payoff). Reasoned conclusion: "no caching" already covers the general case, and offset-based pagination only risks returning a boundary record twice if new data shifts the sort order between page fetches — a low-probability edge case, and one that matters even less given real steady-state volume (see §9) rarely exceeds a single page.

### Rule — never return a job you'd discard

Every filter you apply in Python is a credit already spent. Push everything server-side.

### Corollary — one query, not several

✅ Overlapping searches return the same job twice and charge twice. Prefer a single query with an OR'd title array. If you must split, keep title lists disjoint or feed `job_id_not` with everything already stored.

---

## 3. Free modes — both verified live, balance genuinely unchanged

### Preview mode ✅ (verified — balance stayed at 0 used across every call)

Set `blur_company_data: true`. Returns records without consuming credits, with identifying fields blurred.

**Job search — blurred fields (confirmed from the live doc page, exact list):** `description`, `url`, `final_url`, `source_url`, `company`, `company_domain`, `company_object.name`, `company_object.domain`, `company_object.linkedin_url`, `company_object.linkedin_id`, `company_object.url`, `company_object.long_description`, `company_object.seo_description`, `company_object.possible_domains`.

✅ **`hiring_team` survives preview mode — confirmed empirically**, not just from the absence of it in the blur list. Pulled a real blurred record: `company` came back as `"XxxXxxx XX"`, `description` as masked characters, while `hiring_team` came back as a genuine (in that case empty) array. Preview mode is sufficient to validate outreach coverage for free.

⚠️ Not available when filtering by company identifiers (`company_name`, `company_domain`, `company_linkedin_url`, `company_id`) — and as of Oct 13, 2025, `blur_company_data` has no cost-reduction effect at all when filtering by a single company identifier (bills normally). Irrelevant to our OR'd multi-title query.

### Free count ✅ (verified — this is the exact mechanism, confirmed live)

1. `include_total_results: true` — returns `metadata.total_results`.
2. `blur_company_data: true` — makes the request free.
3. `limit: 1` — minimizes returned data.

All three together, not `limit: 1` alone. Verified: ran this exact query repeatedly, checked `/v0/billing/credit-balance` before and after each time — `used_api_credits` never moved from 0.

⚠️ `include_total_results` reads the whole matching dataset and slows the response — enable it only on the first page of any paginated call, not every page.

---

## 4. Request constraints

✅ **At least one of these is required or the request fails** (confirmed from the live OpenAPI spec description, verbatim):
`posted_at_max_age_days`, `posted_at_gte`, `posted_at_lte`, `company_domain_or`, `company_linkedin_url_or`, `company_name_or`

`discovered_at_gte` is **not** on that list — pair it with `posted_at_max_age_days`.

✅ Pagination: `offset` + `limit`, or `page`, or `cursor`. Official example uses `offset: 0, limit: 500`.

✅ **Rate limits — verified from live response headers on a real call, resolving a conflict between two of TheirStack's own doc pages:**

| Tier | Per-second | Per-minute | Per-hour | Per-day |
|---|---|---|---|---|
| Free | **4** | 10 | 50 | 400 |
| Paid | 4 | — | — | — |

An October 2025 product-update blog post claims free tier is 2 req/sec — that's stale. The dedicated Rate Limit reference page *and* the actual `ratelimit-policy` response header on a live call both say 4/sec. Trust the header over the blog post.

Free tier page size: max 25 results/page, 5 pages max per query (confirmed in docs and matches the account's plan comparison table). Paid: up to 500/page, unlimited pages.

Rate limit headers follow the IETF `RateLimit`/`RateLimit-Policy` draft — read `RateLimit-Remaining` and back off on 429 with exponential backoff.

---

## 5. Filters we use

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
  "job_seniority_or": ["mid_level","senior","staff"],
  "limit": 100,
  "offset": 0
}
```

This is now also the saved-search body used to drive the webhook (see §8) — `discovered_at_gte` is dropped from it since webhooks push new matches as they're discovered, no incremental timestamp bookkeeping needed on our side.

**Why each choice:**
- `company_type: "direct_employer"` — excludes recruiting agencies. Largest single noise reduction. Other values: `recruiting_agency`, `all`.
- `min_employee_count_or_null` rather than `min_employee_count` — keeps companies whose size is unknown instead of silently dropping them.
- `job_title_or` matching is keyword-based and word-order independent: `"automation engineer"` matches "Engineer, Automation" and "Senior Automation Engineer".
- **No salary filter.** `min_salary_usd`/`max_salary_usd` exist, but most postings have no published band and would be excluded. Salary filtering happens in the queue layer. Do not move it here.

---

## 6. Full parameter reference

**Dates**
`posted_at_gte` · `posted_at_lte` · `posted_at_max_age_days` (0 = today) · `discovered_at_gte` · `discovered_at_lte` · `discovered_at_max_age_days` · `discovered_at_min_age_days` · `closed_at_gte` · `closed_at_lte`

**Title** — `job_title_or` · `job_title_not` (keyword, word-order independent) · `job_title_pattern_or` / `_and` / `_not` (regex, case-insensitive)

**Description** — `job_description_contains_or` / `_not` (whole words, word boundaries) · `job_description_pattern_or` / `_and` / `_not` (regex; `(?i)` prefix for case-insensitive)

**Location** — `job_country_code_or` / `_not` (ISO2) · `job_location_or` / `_not` (structured IDs from the locations catalog — preferred) · `job_location_pattern_or` / `_not` (deprecated) · `remote` (true/false/null)

**Company** — `company_name_or` · `company_name_case_insensitive_or` · `company_name_partial_match_or` / `_not` · `company_domain_or` / `_not` · `company_linkedin_url_or` · `company_id_or` / `_not` · `company_country_code_or` / `_not` / `_not_or_null` · `company_location_pattern_or` · `company_description_pattern_or` / `_not` · `company_tags_or` · `company_list_id_or` / `_not` · `company_type`

**Firmographics** — `min_employee_count` / `max_employee_count` (+ `_or_null` variants) · `min_revenue_usd` / `max_revenue_usd` · `min_funding_usd` / `max_funding_usd` · `funding_stage_or` (seed, series_a…series_j, pre_seed, private_equity, post_ipo_*, growth_equity_vc, grant, …) · `last_funding_round_date_gte` / `_lte` · `industry_id_or` / `_not` / `_not_or_null` (LinkedIn Industry Codes V2) · `company_investors_or` · `company_investors_partial_match_or` · `only_yc_companies`

**Job attributes** — `job_seniority_or` (`c_level`, `staff`, `senior`, `junior`, `mid_level`) · `employment_statuses_or` (`full_time`, `part_time`, `temporary`, `internship`, `contract`, …) · `is_closed` · `easy_apply` · `job_id_or` / `_not`

**Tech & keywords** — `job_technology_slug_or` / `_and` / `_not` · `company_technology_slug_or` / `_and` / `_not` · `job_keyword_slug_or` / `_and` / `_not` · `company_keyword_slug_or` / `_and` / `_not` (slugs from the catalog endpoints)

**Salary** — `min_salary_usd` · `max_salary_usd` (annual USD; we don't use these — see §5)

**Source** — `url_domain_or` / `_not` (e.g. `["linkedin.com"]`, `["greenhouse.io"]`)

**Presence** — `property_exists_or` / `_and`: `company_object.domain`, `company_object.linkedin_url`, `final_url`, `hiring_team`, `employment_statuses`.
Deprecated equivalents you may see and should not use: `only_jobs_with_hiring_managers`, `reports_to_exists`, `hiring_managers_exists`, `final_url_exists`, `company_linkedin_url_exists`.

**Control** — `limit` (default 25) · `offset` · `page` · `cursor` · `include_total_results` · `blur_company_data` · `order_by` (deprecated; defaults to `date_posted` desc, then `discovered_at` desc)

---

## 7. Response shape

```jsonc
{
  "data": [{
    "id": 1234,
    "job_title": "Senior Data Engineer",
    "description": "…markdown…",
    "url": "https://example.com/job/1234",
    "final_url": "https://company.com/careers/…",   // ATS-original when known
    "source_url": "https://www.linkedin.com/jobs/view/…",
    "date_posted": "2021-01-01",
    "date_reposted": "2024-01-01",
    "reposted": true,
    "discovered_at": "2024-01-01T00:00:00",          // ← dedup anchor
    "closed_at": "2024-06-15T12:30:00Z",
    "remote": true, "hybrid": true,
    "location": "New York",
    "long_location": "Methuen, MA 01844",
    "short_location": "Tulsa, OK",
    "locations": [{ "id": 5367315, "display_name": "…", "city…state…country_code" }],
    "country": "United States", "country_code": "US",
    "seniority": "c_level",
    "employment_statuses": ["full_time"],
    "easy_apply": true,
    "min_annual_salary_usd": 100000,
    "max_annual_salary_usd": 100000,
    "avg_annual_salary_usd": 100000,
    "salary_string": "$100,000 - $120,000",
    "salary_currency": "USD",
    "hiring_team": [{                                 // ← outreach targets, 11% fill rate (n=100)
      "full_name": "…", "first_name": "…", "role": "CEO",
      "linkedin_url": "https://www.linkedin.com/in/…",
      "image_url": "…", "thumbnail_url": "…"
    }],
    "manager_roles": ["…"],                           // ← reports-to signal
    "technology_slugs": ["postgresql","jira"],
    "keyword_slugs": ["…"],
    "matching_phrases": [], "matching_words": [],
    "normalized_title": "…",
    "company": "Google",
    "company_domain": "acme.com",
    "company_object": {
      "name","domain","logo","url","linkedin_url","industry","industry_id",
      "employee_count", "employee_count_range", "founded_year",
      "funding_stage","total_funding_usd","last_funding_round_date","investors",
      "annual_revenue_usd","country","city","yc_batch","is_recruiting_agency",
      "num_jobs","num_jobs_last_30_days","technology_slugs","keyword_slugs",
      "has_blurred_data"
    },
    "has_blurred_data": false
  }],
  "metadata": {
    "total_results": 2034, "total_companies": 1045,
    "truncated_results": 0, "truncated_companies": 0
  }
}
```

**Fields that matter most to us:**
- `discovered_at` — max of these across a run is the next run's `discovered_at_gte` (moot once fully on webhooks, still relevant for any one-off manual/backfill pull)
- `hiring_team[]` — name, role, LinkedIn URL. Real fill rate: **11%** (n=100, live sample). Bonus signal, not the primary outreach path.
- `manager_roles[]` — populates the "reports to" field on the outreach panel
- `final_url` — prefer over `url` for the actual application link
- `min/max_annual_salary_usd` + `salary_string` — comp badges. Absent → `comp_source = 'absent'`.
- `is_recruiting_agency` — sanity check that `company_type` filtering worked
- `reposted` / `date_reposted` — see §2 dedup mechanics; the 30-day rule governs recharge behavior

**Errors:** 400, 402 (credits exhausted), 422 (validation), 500. Body is `{ "error": { "code", "title", "description" }, "request_id" }`. Handle 402 explicitly — stop the run, alert, don't retry.

---

## 8. Webhooks — this is the ingest design, not an alternative

✅ **Decision made:** ingest is webhook-driven. TheirStack's own guide on periodic fetching explicitly recommends this over polling: *"we'd strongly recommend using our webhooks instead... if you're seeing duplicate job issues, it's a strong signal that your current approach is flawed."*

- `job.new` fires when a job matching a saved search is discovered; `job.closed` when a posting closes. Same credit cost as API results — 1 per job (job.new/closed), 3 per company (company.new/tech.new).
- Retries: failed webhook deliveries retry hourly for 48 hours.
- Credit depletion doesn't lose events: if credits run out and a webhook's search window (e.g. `posted_at_max_age_days`) still covers a job discovered during the gap, it fires once credits are restored.
- **Settings stay data, not code.** `/v0/saved_searches` and `/v0/webhooks` both support full CRUD (`POST`/`GET`/`PATCH`/archive). The app pushes `settings` table changes to TheirStack via API — nothing is hand-configured in their app UI, preserving CLAUDE.md's "settings are data, not code" rule.
- **Don't run both webhooks and polling for the same search** — you'd pay twice.

---

## 9. Plans — verified against a live account

- **Free: 200 API credits/month + 50 company credits/month, forever free**, but *"only for users who have never paid for any credits. Once you make your first payment, you become a paid plan member from that moment forward"* — confirmed verbatim from the current pricing/plans doc. No free→paid→free cycling.
- Free credits expire (~1 month from grant on this account) — see §2. This contradicts an earlier "no time limit" assumption from search-snippet research; don't rely on that claim.
- Paid plan pricing itself: still not independently verified against a primary source with confidence (search results for exact dollar figures conflicted) — check the live billing page before subscribing.
- Free tier limits: 5 pages max, 25 results/page, 4 req/sec (see §4 — corrects an earlier 2 req/sec assumption).

**Sequencing:** build and validate the entire ingest + scoring path on free credits before subscribing to anything — confirmed as the right call given the no-cycling rule above.

---

## 10. Verification log — all originally-open questions, now resolved

| # | Question | Status | Finding |
|---|---|---|---|
| 1 | Is free count genuinely free? | ✅ Verified live | Balance unchanged (`used_api_credits: 0`) across every free-count call, repeated. |
| 2 | Does pagination re-charge earlier pages? | ⚠️ Not tested | Reasoned low-risk (no-caching already covers it; real volume rarely exceeds one page) — not worth spending real credits to confirm further. |
| 3 | Is `discovered_at_gte` inclusive or exclusive, timezone? | ✅ Documented | UTC; use `MAX(discovered_at)` of the last job actually processed, not run-start time. |
| 4 | Does a repost re-charge? | ✅ Documented | Reposts within 30 days of original posting are deduped (no recharge); 30+ days, they resurface and recharge. Intentional, documented behavior. |
| 5 | Does `hiring_team` survive preview mode? | ✅ Verified live | Yes — confirmed on a real blurred record (masked company/description next to a populated hiring_team array). |
| 6 | `hiring_team` fill rate? | ✅ Verified live | **11%** on a 100-record live sample — below the 15% threshold, so outreach leads with the LinkedIn search link, not a named contact. |
| 7 | Real rate limits and max page size on this tier? | ✅ Verified live | 4 req/sec (from live response headers, resolving a conflict with a stale blog post claiming 2/sec), 25/page, 5 pages max. |
| 8 | Backlog size for our query? | ✅ Verified live | **28,624** open postings match the full production filter set, all-time. At 1,500 credits/month that's ~19 months of budget — do not sweep it unbounded. |

Also verified live: steady-state volume via a 7-day window average = **42.4/day ≈ 1,273/month**, matching the original budget assumption (a 1-day check showed 9 — daily noise, not signal, don't trust single-day counts).
