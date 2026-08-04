-- Applications tab had zero backend persistence — Mark submitted, status
-- changes, notes, Reject, and Defer all only mutated in-memory JS state.
-- One row per posting, upserted as actions happen.
alter table applications add constraint applications_posting_id_key unique (posting_id);

-- "Record final" captures the filename actually submitted after manual
-- edits — genuinely new information, no existing column fit.
alter table applications add column final_filename text;
