-- Phase 6 (outreach): one outreach record per posting, upserted as the
-- target/drafts change — needs a real unique constraint for PostgREST's
-- on_conflict to target. Draft length caps live in settings, not code,
-- per docs/SIGHTLINE_BUILD_SPEC_V2.md §7: "LinkedIn's limits move."

alter table outreach add constraint outreach_posting_id_key unique (posting_id);

alter table settings add column linkedin_note_max_chars int not null default 300;
alter table settings add column linkedin_message_max_words int not null default 150;
alter table settings add column email_max_words int not null default 80;
alter table settings add column email_subject_max_words int not null default 10;
