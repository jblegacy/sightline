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

Neither breaker auto-re-enables. A daily-cap trip looks identical on
TheirStack's side to a monthly trip (both profiles disabled) — auto-resuming
at midnight risks the exact "queued matches delivered in full on re-enable"
charge spike this module exists to prevent. Re-enabling is a deliberate
human action from the dashboard, same as every other guardrail in this
system stays human-in-the-loop.
"""
from __future__ import annotations

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


def check_and_enforce_daily_cap(
    theirstack: TheirStackClient,
    db: SightlineDB,
    daily_credit_cap: int | None,
) -> dict[str, Any]:
    """A throttle, not a budget limit — meant to cap the blind ramp before
    quality's been reviewed, independent of how large the monthly budget is.
    No cap set (None or 0) means the throttle is off; only the monthly
    breaker applies."""
    if not daily_credit_cap:
        return {"tripped": False, "reason": "no daily cap set"}

    used_today = db.credits_used_today()
    if used_today < daily_credit_cap:
        return {"tripped": False, "used_today": used_today, "daily_credit_cap": daily_credit_cap}

    _disable_all_profiles(theirstack)
    db.log_event(
        entity_type="budget",
        event="daily_cap_tripped",
        payload={"used_today": used_today, "daily_credit_cap": daily_credit_cap},
    )
    return {"tripped": True, "used_today": used_today, "daily_credit_cap": daily_credit_cap}
