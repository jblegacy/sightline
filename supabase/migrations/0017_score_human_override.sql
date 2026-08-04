-- Manual score correction, separate from application state (Approve/Reject/
-- Defer). The user reviews a JD directly, disagrees with the rubric, and
-- wants the posting bumped into (or out of) the queue immediately while the
-- reason becomes calibration data toward a future rubric_version revision.
-- The AI's original `total` is never overwritten — it's the calibration
-- baseline, not something to silently erase.
alter table scores add column human_override_total int;
alter table scores add column human_override_reason text;
alter table scores add column human_override_at timestamptz;
