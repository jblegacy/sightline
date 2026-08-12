"""Circuit breakers for TheirStack credit spend — a monthly one (real
budget exhaustion) and a daily one (throttle while quality is unproven,
per the user's explicit ask before committing to a larger paid tier).

Important: in a webhook-driven design, the credit is spent the moment
TheirStack decides to dispatch an event — not when we choose to process it.
So this can only ever stop *future* deliveries; it cannot undo the one that
just landed. There is no "reject before charging" available to us here.

Disabling the webhook alone isn't enough either — TheirStack's own docs say
matches found while a webhook is disabled are queued and delivered (charged)
in full the moment it's re-enabled. A real stop needs the saved search's
alert deactivated too, which stops matching altogether.

The monthly breaker never auto-re-enables — that's a real budget event and
stays a deliberate human action from the dashboard, same as every other
guardrail in this system stays human-in-the-loop.

The daily throttle is different: see maybe_reset_daily_breaker. Revisited
2026-08-10 after 5 days of silently stalled ingestion — a daily cap that
never resets isn't actually daily, it's the monthly breaker with worse
branding. The reactivation-burst risk is real but bounded by construction:
at most one day's worth of newly-matching postings can have queued up
(this account averages ~42/day), the same order of magnitude as the cap
itself, not the unbounded historical backlog a long-stale search could
otherwise dump. Resets lazily, the first time the app is touched after a
new UTC day starts — there's no scheduled worker process in this codebase
to fire it on a clock.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from sightline.db import SightlineDB
from sightline.theirstack import TheirStackClient

SEARCH_PROFILE_IDS = ("automation", "cpg")
SAFETY_MARGIN = 0.9  # trip at 90% of budget, not 100% — leaves buffer for the check's own lag


def saved_search_name(profile_id: str) -> str:
    return f"sightline-{profile_id}"


def _disable_all_profiles(theirstack: TheirStackClient) -> None:
    for profile_id in SEARCH_PROFILE_IDS:
        saved_search = theirstack.find_saved_search(saved_search_name(profile_id))
        if not saved_search:
            continue
        if saved_search.get("is_alert_active"):
            theirstack.set_saved_search_active(saved_search["id"], False)
        webhook = theirstack.find_webhook_for_search(saved_search["id"])
        if webhook and webhook.get("is_active"):
            theirstack.set_webhook_active(webhook["id"], False)


def _enable_all_profiles(theirstack: TheirStackClient) -> None:
    for profile_id in SEARCH_PROFILE_IDS:
        saved_search = theirstack.find_saved_search(saved_search_name(profile_id))
        if not saved_search:
            continue
        if not saved_search.get("is_alert_active"):
            theirstack.set_saved_search_active(saved_search["id"], True)
        webhook = theirstack.find_webhook_for_search(saved_search["id"])
        if webhook and not webhook.get("is_active"):
            theirstack.set_webhook_active(webhook["id"], True)


def check_and_enforce_budget(
    theirstack: TheirStackClient,
    db: SightlineDB,
    monthly_credit_budget: int,
) -> dict[str, Any]:
    """Call after processing any event that consumed a credit. Free to call —
    credit-balance is a 0-credit endpoint — so there's no cost to checking
    on every single event rather than batching or sampling."""
    balance = theirstack.credit_balance()
    used = balance["used_api_credits"]
    threshold = monthly_credit_budget * SAFETY_MARGIN

    if used < threshold:
        return {"tripped": False, "used_api_credits": used, "threshold": threshold}

    _disable_all_profiles(theirstack)
    db.log_event(
        entity_type="budget",
        event="circuit_breaker_tripped",
        payload={
            "used_api_credits": used,
            "monthly_credit_budget": monthly_credit_budget,
            "threshold": threshold,
        },
    )
    return {"tripped": True, "used_api_credits": used, "threshold": threshold}


def _get_or_reset_daily_baseline(db: SightlineDB, settings: dict[str, Any], used_total: int) -> int:
    """The credit-balance baseline for 'today' (UTC) — resets the moment the
    date rolls over or no baseline exists yet, otherwise returns the one
    already captured."""
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    baseline = settings.get("credit_balance_baseline")
    baseline_date = settings.get("credit_balance_baseline_date")
    if baseline_date == today and baseline is not None:
        return baseline
    db.update_settings({"credit_balance_baseline": used_total, "credit_balance_baseline_date": today})
    return used_total


def used_today(theirstack: TheirStackClient, db: SightlineDB, settings: dict[str, Any]) -> int:
    """Real credits spent since UTC midnight — anchored to TheirStack's own
    cumulative used_api_credits, diffed against a baseline snapshot, not
    summed from our own `events` log. The log approach drifted in practice:
    verified live, our log said 31 while TheirStack's real balance said 18,
    because hand-signed test webhook payloads posted during development
    look identical to a real delivery in our own log but never touched
    TheirStack's billing at all. The real balance can't have that problem —
    it only moves when TheirStack actually charges something."""
    balance = theirstack.credit_balance()
    used_total = balance["used_api_credits"]
    baseline = _get_or_reset_daily_baseline(db, settings, used_total)
    return used_total - baseline


def check_and_enforce_daily_cap(
    theirstack: TheirStackClient,
    db: SightlineDB,
    daily_credit_cap: int | None,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """A throttle, not a budget limit — meant to cap the blind ramp before
    quality's been reviewed, independent of how large the monthly budget is.
    No cap set (None or 0) means the throttle is off; only the monthly
    breaker applies."""
    if not daily_credit_cap:
        return {"tripped": False, "reason": "no daily cap set"}

    today_usage = used_today(theirstack, db, settings)
    if today_usage < daily_credit_cap:
        return {"tripped": False, "used_today": today_usage, "daily_credit_cap": daily_credit_cap}

    _disable_all_profiles(theirstack)
    db.log_event(
        entity_type="budget",
        event="daily_cap_tripped",
        payload={"used_today": today_usage, "daily_credit_cap": daily_credit_cap},
    )
    return {"tripped": True, "used_today": today_usage, "daily_credit_cap": daily_credit_cap}


_BREAKER_EVENTS = ("daily_cap_tripped", "circuit_breaker_tripped", "daily_cap_reset")


def maybe_reset_daily_breaker(theirstack: TheirStackClient, db: SightlineDB) -> dict[str, Any]:
    """Auto-resumes ingestion the first time the app is touched on a new UTC
    day, but only if the daily throttle — not the monthly circuit breaker —
    is what's currently paused. See module docstring for why this one
    resets and the monthly one doesn't. Call this from something the
    dashboard hits on every load (e.g. GET /api/credits); there's no
    scheduled worker in this codebase to call it on a clock instead."""
    latest = db.get_latest_event(list(_BREAKER_EVENTS))
    if not latest or latest["event"] != "daily_cap_tripped":
        return {"reset": False, "reason": "not currently paused by the daily throttle"}

    tripped_date = latest["created_at"][:10]
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    if tripped_date >= today:
        return {"reset": False, "reason": "still the same UTC day it tripped"}

    _enable_all_profiles(theirstack)
    db.log_event(
        entity_type="budget", event="daily_cap_reset", payload={"tripped_at": latest["created_at"]}
    )
    return {"reset": True, "tripped_at": latest["created_at"]}
