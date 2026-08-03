-- Phase 2: dedup constraints needed for upsert-based ingest.
--
-- postings.external_id will hold TheirStack's job `id`, which TheirStack's own
-- docs describe as globally unique and the recommended dedup key ("You can use
-- the id field to deduplicate jobs"). The original (company_id, external_id)
-- unique constraint doesn't work as an upsert target when company_id can be
-- null, and isn't needed anyway since external_id alone is already unique.
alter table postings add constraint postings_external_id_key unique (external_id);

-- Lets the ingest client upsert a company by domain instead of duplicating rows
-- per job. Partial index so companies with no known domain don't collide on NULL.
create unique index companies_domain_key on companies (domain) where domain is not null;
