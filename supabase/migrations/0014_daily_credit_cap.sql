-- per_run_cap was a settings field with no enforcement anywhere — just
-- displayed on the dashboard (CLAUDE.md flagged this: "the UI value is a
-- display of it, not the enforcement"). Webhook-driven ingest has no
-- discrete "run" to cap, so the natural throttle is a daily one: rename to
-- match what it actually protects against, and see budget.py for the real
-- enforcement now wired to it.
alter table settings rename column per_run_cap to daily_credit_cap;
