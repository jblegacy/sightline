-- Repositioning: two disjoint search profiles (automation / cpg) replace the
-- single global title_include/exclude on `settings`. See CLAUDE.md "Two
-- search profiles" — this is data, not code, same as `settings`.

create table search_profiles (
  id              text primary key,            -- 'automation' | 'cpg'
  label           text not null,
  title_include   text[] not null,
  title_exclude   text[] not null,
  resume_variant  text not null,                -- engineer|leadership
  budget_share    numeric(3,2) not null,         -- 0.60 / 0.40
  active          boolean not null default true
);

insert into search_profiles (id, label, title_include, title_exclude, resume_variant, budget_share) values
('automation', 'AI / Workflow Automation',
 ARRAY['workflow automation','business systems analyst','ai enablement','ai operations',
       'automation specialist','business process automation','solutions consultant',
       'implementation consultant','process improvement','ai adoption','systems analyst',
       'ai integration analyst','automation consultant','no-code developer'],
 ARRAY['software engineer','backend','frontend','full stack','machine learning engineer',
       'data engineer','devops','site reliability','platform engineer','ai engineer',
       'research scientist','recruiter','intern','staff engineer','principal engineer'],
 'engineer', 0.60),
('cpg', 'CPG Operations',
 ARRAY['director of operations','head of operations','operations manager',
       'supply chain manager','demand planning','sales and operations planning',
       'inventory planning','ecommerce operations','dtc operations','fulfillment operations',
       'logistics manager','category manager','sales operations manager','revenue operations',
       'chief of staff','business operations manager','commercialization manager',
       'procurement manager','planning manager'],
 ARRAY['warehouse associate','forklift','production supervisor','line lead','machine operator',
       'quality technician','maintenance','driver','merchandiser','sanitation','intern','recruiter'],
 'leadership', 0.40);

-- Superseded by search_profiles.title_include/title_exclude — the old single
-- global query is gone, not just unused.
alter table settings drop column title_include;
alter table settings drop column title_exclude;

-- Which profile's fetch criteria matched this posting — traceability for
-- "why did this surface", and future per-profile budget tracking.
alter table postings add column search_profile_id text references search_profiles(id);

-- Title alone is unreliable in both directions (CLAUDE.md). Distinct from
-- knockouts — a coding-interview signal isn't a hard requirement the JD
-- states, it's a pattern the scorer read and is flagging for judgment.
alter table scores add column coding_interview_signals jsonb;

-- New target shape excludes junior and pure-executive roles by default.
update settings set seniority = ARRAY['mid_level','senior','staff'] where id = 1;

