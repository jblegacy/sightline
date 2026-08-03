-- Red-flag phrases are referenced by the spec's deterministic filter but were
-- never defined anywhere — not something to invent, so this starts empty.
-- The filter treats an empty list as "no red-flag check," not "archive
-- everything." Edit from the dashboard once it exists.
alter table settings add column red_flag_phrases text[] not null default '{}';
