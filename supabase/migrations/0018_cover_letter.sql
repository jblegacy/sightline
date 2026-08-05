-- Cover letters are a sibling document to the resume variant they
-- accompany, not a separate application-level concept — one resume, one
-- letter, generated to echo the same bullet selection.
alter table variants add column cover_letter_text text;
alter table variants add column cover_letter_storage_path text;
