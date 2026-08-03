-- The Phase 0 seed set monthly_credits to 1500, the paid-tier planning
-- assumption from the original spec. This account is actually on the free
-- tier: 200 API credits/month. The circuit breaker in sightline/budget.py
-- reads this value directly, so a wrong number here means it trips at the
-- wrong threshold. Bump this back up if/when the account goes to a paid plan.
update settings set monthly_credits = 200 where id = 1;
