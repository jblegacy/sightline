-- Sightline — Phase 0 schema
-- Source: docs/SIGHTLINE_BUILD_SPEC_V2.md §3 (authoritative). Copied verbatim.
-- Run once in the Supabase SQL Editor (Project → SQL Editor → New query → paste → Run).

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

-- `bullets` above is created empty here (schema only) per Phase 0 scope.
-- Seeding ~45 rows from both resume variants, plus the `answers` table
-- (schema in docs/ANSWER_LIBRARY.md) and the provenance validator, is
-- Phase 1 — see docs/SIGHTLINE_BUILD_SPEC_V2.md §11 build order. Don't
-- seed or build against those until Phase 0 passes its acceptance check.
