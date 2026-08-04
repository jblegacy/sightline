-- credits_used_today() summed our own `events` log, which drifted from
-- TheirStack's real billing (verified live: our log said 31, TheirStack
-- said 18 — some of our own live-verification webhook calls this session
-- were hand-signed test payloads that never touched TheirStack's billing
-- at all, but looked identical to a real delivery in our own log).
-- Anchoring to TheirStack's own cumulative used_api_credits and diffing
-- against a baseline captured at the start of each UTC day can't drift —
-- it's the real number, not our approximation of it.
alter table settings add column credit_balance_baseline int;
alter table settings add column credit_balance_baseline_date date;
