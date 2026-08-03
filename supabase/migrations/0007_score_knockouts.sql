-- The scores table (spec §3) never had a column for knockouts, but the
-- prototype dashboard — the actual API contract per CLAUDE.md — treats
-- knockouts (ko) and general gaps (gaps) as two separate fields with
-- different UI treatment (a red badge vs. a plain chip). Conflating them
-- into unmet_requirements would lose that distinction.
alter table scores add column knockouts jsonb;
