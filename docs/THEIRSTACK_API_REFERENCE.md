# TheirStack API — Compiled Reference

Assembled from official documentation on 2026-07-31 because the rendered doc pages were returning 503s.

**Get the docs directly if you can — three routes that avoid HTML scraping:**

1. **MCP server** (best): `https://api.theirstack.com/mcp`, Bearer auth with a TheirStack API key.
   ```json
   { "mcpServers": { "theirstack": { "url": "https://api.theirstack.com/mcp",
     "headers": { "Authorization": "Bearer <YOUR_API_KEY>" } } } }
   ```
   Keys don't expire until revoked. OAuth also works but requires re-auth frequently.
2. **OpenAPI spec**: `https://api.theirstack.com/openapi.json` (or `.yaml`) — definitive.
3. **Markdown docs**: `https://theirstack.com/llms.txt` indexes every page; each has a `.md` variant (e.g. `.../api-reference/authentication.md`).

**Marker key:** ✅ from official docs · ⚠️ third-party or inferred, verify before relying on it.

---

## 1. Auth and base

✅ Base URL: `https://api.theirstack.com`
✅ Auth: `Authorization: Bearer <api_key>`
✅ Keys created at Settings → API Keys. Shown once. Expiry is a date or Never. Revocable, immediate, irreversible.

```
POST /v1/jobs/search              1 credit per job returned
POST /v1/companies/search         3 credits per company
POST /v1/companies/technographics 3 credits per company
GET  /v0/billing/credit_balance   free
GET  /v0/teams/credits_consumption free
GET  /v0/catalog/keywords | /technologies | /industries | /locations
```

⚠️ Exact paths for the account and catalog endpoints — confirm against the OpenAPI spec.

**Use Job Search only.** Company Search and Technographics cost 3× and we don't need them.

---

## 2. Credits — the constraint that shapes everything

✅ 1 credit per **job returned**. Not per call — per record.
✅ Credits are consumed only when data is returned, or when a webhook event dispatches.
✅ **No caching.** From the docs verbatim in substance: repeated calls without dedup filters will charge for the same jobs multiple times.
✅ Unused paid credits roll over up to 12 months.

**Two rules follow, and they're the whole game:**

### Rule 1 — never fetch a job twice

✅ `discovered_at_gte` — pass a timestamp higher than your last call. Format `YYYY-MM-DDTHH:MM:SSZ`, UTC.

✅ **Important detail:** the docs say this should be the `discovered_at` of the **last job you fetched**, not your run start time. Store the max `discovered_at` from the previous run's results and use that. It also means a failed run is safe to resume from — you fetch only what was discovered after the last successfully processed job, with no gap and no double charge.

✅ `job_id_not` — array of job IDs to exclude. The docs recommend this specifically when the same job may appear across multiple searches. If you ever run more than one query, this is how you stop paying twice for overlap.

### Rule 2 — never return a job you'd discard

Every filter you apply in Python is a credit already spent. Push everything server-side.

### Corollary — one query, not several

✅ Because overlapping searches return the same job twice and charge twice. Prefer a single query with an OR'd title array. If you must split, either keep title lists disjoint or feed `job_id_not` with everything already stored.

---

## 3. Free modes

### Preview mode ✅
Set `blur_company_data: true`. Returns records **without consuming credits**, with identifying fields blurred.

Job search — blurred: `description`, `url`, `final_url`, `source_url`, `company`, `company_domain`, `company_object.name`, `company_object.domain` (list may extend slightly).

Still readable: `job_title`, salary fields, `date_posted`, `discovered_at`, `location`/`locations`, `remote`, `hybrid`, `seniority`, `employment_statuses`, `technology_slugs`, `keyword_slugs`, `company_object.employee_count`, `funding_stage`, `industry`, `id`.

❓ **Unverified: does `hiring_team` survive preview mode?** Test this — it determines whether outreach coverage can be validated for free.

⚠️ Not available when filtering by company identifiers (`company_name`, `company_domain`, `company_linkedin_url`, `company_id`). Irrelevant for us.

### Free count ✅
Set `limit: 1` to minimize returned data and focus on the count, without consuming credits. Pair with `include_total_results: true` to get `metadata.total_results`.

❓ **Verify empirically before building the Preview feature on it:** call credit balance → run the count → call balance again. It must be unchanged. The docs describe multiple steps and I could only read part of the page.

⚠️ `include_total_results` significantly slows responses (it reads the whole dataset). Official guidance: enable on the first request only, then disable for pagination.

---

## 4. Request constraints

✅ **At least one of these is required or the request fails** (performance reasons):
`posted_at_max_age_days`, `posted_at_gte`, `posted_at_lte`, `company_domain_or`, `company_linkedin_url_or`, `company_name_or`

Note `discovered_at_gte` is **not** on that list — pair it with `posted_at_max_age_days` to satisfy the requirement.

✅ Pagination: `offset` + `limit`, or `page`, or `cursor`. Official example uses `offset: 0, limit: 500`.
⚠️ Free tier reportedly caps at 5 pages × 25 results, 2 req/sec; paid reportedly allows up to 500/page and 4 req/sec. **Verify on the actual account.**
❓ **Unverified and credit-critical: does paginating re-charge for records already returned in earlier pages of the same query?** Test with a small query before any large fetch.

---

## 5. Filters we use

```json
{
  "posted_at_max_age_days": 30,
  "discovered_at_gte": "<max discovered_at from last run, ISO8601 Z>",
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
    "hiring_team": [{                                 // ← outreach targets
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
- `discovered_at` — max of these across a run is the next run's `discovered_at_gte`
- `hiring_team[]` — name, role, LinkedIn URL. This is our contact discovery; no email-finder tool needed.
- `manager_roles[]` — populates the "reports to" field on the outreach panel
- `final_url` — prefer over `url` for the actual application link
- `min/max_annual_salary_usd` + `salary_string` — comp badges. Absent → `comp_source = 'absent'`.
- `is_recruiting_agency` — sanity check that `company_type` filtering worked
- `reposted` / `date_reposted` — ❓ **verify whether a repost re-triggers `discovered_at` and re-charges**

**Errors:** 400, 402 (credits exhausted), 422 (validation), 500. Body is `{ "error": { "code", "title", "description" }, "request_id" }`. Handle 402 explicitly — stop the run, alert, don't retry.

---

## 8. Webhooks (alternative to polling)

✅ `job.new` fires when a job matching a saved search is discovered; `job.closed` when a posting closes. Same credit cost as API results — 1 per job.

Worth considering: push means no duplicate-charge risk at all and near-real-time discovery, which serves the time-to-apply metric. Tradeoff is needing a public endpoint and a saved search configured in their app rather than in code. **Don't run both webhooks and polling for the same search — you'd pay twice.**

---

## 9. Plans

⚠️ All unverified — check current pricing.
- Free: reportedly 200 API credits/month, no time limit, reportedly only for accounts that have never paid.
- Paid: from ~$59/mo. 1,500 credits ≈ 50 jobs/day.

**Sequencing matters:** if the free tier really is unavailable after a first payment, build and validate the entire ingest and scoring path on free credits *before* subscribing. Confirm this before James pays for anything.

---

## 10. Open questions — resolve before the first live run

| # | Question | How to test |
|---|---|---|
| 1 | Is free count genuinely free? | Credit balance → count → balance. Must be unchanged. |
| 2 | Does pagination re-charge earlier pages? | Small query, page twice, watch the balance. |
| 3 | Is `discovered_at_gte` inclusive or exclusive? | Fetch, note max `discovered_at`, re-fetch with it. Count returned. |
| 4 | Does a repost re-charge? | Track a known reposted job across runs. |
| 5 | Does `hiring_team` survive preview mode? | Preview call, inspect the field. |
| 6 | `hiring_team` fill rate? | Sample ~100 results, count populated. Report the %. |
| 7 | Real rate limits and max page size on this tier? | Ramp until 429. |
| 8 | Backlog size for our query? | Free count, no `discovered_at` bound. Convert to months-of-credits. |

**Run 1 and 8 first.** One validates that the settings Preview feature is safe to build; the other prevents a first live run from consuming two months of credits in a single sweep.
