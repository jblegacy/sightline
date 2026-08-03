-- Phase 0's migration created the settings table but never seeded the single
-- row it's designed around (id=1) — nothing could read config without this.
-- Values match the baseline query in docs/SIGHTLINE_BUILD_SPEC_V2.md §4;
-- edit from the dashboard once it exists, this is just a starting point.
insert into settings (id, title_include, title_exclude)
values (
  1,
  ARRAY['ai engineer','automation engineer','ai enablement','workflow automation',
        'internal tools engineer','integration engineer','business systems analyst',
        'ai operations','business automation','ai implementation'],
  ARRAY['sales engineer','recruiter','intern']
)
on conflict (id) do nothing;
