-- Fixes a real bug in 0003: a partial unique index (`where domain is not
-- null`) can't be used as a PostgREST on_conflict target — Postgres requires
-- the ON CONFLICT clause to repeat the exact same predicate as its arbiter
-- index, which PostgREST's generated upsert doesn't do. Confirmed live: every
-- upsert_company call with a real domain 400'd.
--
-- A plain unique constraint doesn't need the partial predicate anyway —
-- Postgres already treats NULL as distinct from NULL under standard
-- uniqueness rules, so multiple companies with no known domain were never
-- actually at risk of colliding.
drop index if exists companies_domain_key;
alter table companies add constraint companies_domain_key unique (domain);
