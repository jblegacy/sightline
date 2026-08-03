-- The Criteria tab's prototype UI has "Seniority" and "Exclude sources"
-- fields (job_seniority_or / url_domain_not in TheirStack's API), but
-- neither ever got a settings column or made it into
-- sightline.theirstack.build_filters_from_settings. Without this they'd be
-- permanently-dead inputs — wiring the UI doesn't help if there's nowhere
-- to persist the value or anything reading it.
alter table settings add column seniority text[] not null default '{}';
alter table settings add column source_exclude text[] not null default '{}';
