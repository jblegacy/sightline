"""Turns a TheirStack webhook event into rows in `postings`/`companies`/`events`,
then runs it through the deterministic filter and — if it survives — scores it
immediately. Real-time rather than a nightly batch: ingest is already
webhook-driven, so filtering/scoring inline gets a posting into the queue as
fast as possible, which is what the time-to-submit metric actually cares
about. A separate scheduled `worker` isn't needed until the digest email.

Also runs the credit circuit breaker (sightline/budget.py) after every event
that cost a credit — job.new and job.closed both do, per TheirStack's docs.

Pure mapping + orchestration, no HTTP concerns — web/main.py owns the request/
response side (signature check, status codes) and calls into this module.
"""
from __future__ import annotations

import hashlib
from typing import Any

from sightline.anthropic_client import AnthropicClient
from sightline.budget import check_and_enforce_budget, check_and_enforce_daily_cap
from sightline.db import SightlineDB
from sightline.filter import apply_filter
from sightline.scoring import score_posting
from sightline.theirstack import TheirStackClient


def classify_search_profile(title: str, profiles: list[dict[str, Any]]) -> str | None:
    """Best-effort match of a job title against each profile's title lists,
    for traceability (`postings.search_profile_id`) — not a filter. The real
    filtering already happened server-side in TheirStack's own matching;
    this is a simpler approximation of the same word lists, only used to
    record which profile likely fetched it."""
    t = title.lower()
    for profile in profiles:
        if any(term in t for term in profile.get("title_exclude") or []):
            continue
        if any(term in t for term in profile["title_include"]):
            return profile["id"]
    return None


def job_to_posting(
    job: dict[str, Any], company_id: int, search_profile_id: str | None = None
) -> dict[str, Any]:
    """Map a TheirStack job object (search response or job.new payload — same
    schema) to a `postings` row. See docs/THEIRSTACK_API_REFERENCE.md §7."""
    # TheirStack sometimes sends salary as a float (e.g. 208705.0) — postings.
    # comp_min/comp_max are int columns, and Postgres rejects "208705.0" as
    # invalid integer input outright, failing the whole insert.
    raw_comp_min = job.get("min_annual_salary_usd")
    raw_comp_max = job.get("max_annual_salary_usd")
    comp_min = round(raw_comp_min) if raw_comp_min is not None else None
    comp_max = round(raw_comp_max) if raw_comp_max is not None else None
    url = job.get("final_url") or job["url"]  # prefer the ATS-original link
    content_hash = hashlib.sha256(
        f"{job['job_title']}|{job.get('company_domain', '')}|{job.get('description', '')}".encode()
    ).hexdigest()
    return {
        "company_id": company_id,
        "external_id": str(job["id"]),
        "title": job["job_title"],
        "url": url,
        "location_raw": job.get("location"),
        "remote_flag": "true" if job.get("remote") else ("unclear" if job.get("remote") is None else "false"),
        "comp_min": comp_min,
        "comp_max": comp_max,
        "comp_source": "posted" if (comp_min or comp_max) else "absent",
        "posted_at": job.get("date_posted"),
        "content_hash": content_hash,
        "jd_text": job.get("description"),
        "raw": job,
        "status": "new",
        "search_profile_id": search_profile_id,
    }


def handle_job_new(
    db: SightlineDB,
    anthropic: AnthropicClient,
    settings: dict[str, Any],
    profiles: list[dict[str, Any]],
    job: dict[str, Any],
) -> dict[str, Any]:
    """1 credit was already spent delivering this event — log it regardless of
    what happens next; the credit ledger reflects real billing, not our DB state.

    TheirStack redelivers job.new for a still-open match on every scan cycle
    of an active alert, not just once (see CLAUDE.md: "Credits burn per job
    delivered, including repeats — there is no caching"). A posting that's
    already past ingest (filtered/scored/archived/expired) short-circuits
    here — re-running upsert_posting on a redelivery would reset its status
    back to 'new' via the merge, and re-scoring would burn an Anthropic call
    for an answer we already have."""
    external_id = str(job["id"])
    existing = db.find_posting_by_external_id(external_id)
    if existing is not None and existing["status"] != "new":
        db.log_event(
            entity_type="posting", event="duplicate_delivery", entity_id=existing["id"],
            payload={
                "source": "theirstack_webhook", "theirstack_job_id": job["id"],
                "credits_consumed": 1, "existing_status": existing["status"],
            },
        )
        return existing

    company_id = db.upsert_company(
        name=job.get("company") or job.get("company_object", {}).get("name") or "Unknown",
        domain=job.get("company_domain") or job.get("company_object", {}).get("domain"),
    )
    search_profile_id = classify_search_profile(job["job_title"], profiles)
    posting = db.upsert_posting(job_to_posting(job, company_id, search_profile_id))
    db.log_event(
        entity_type="posting",
        event="ingested",
        entity_id=posting["id"],
        payload={"source": "theirstack_webhook", "theirstack_job_id": job["id"], "credits_consumed": 1},
    )

    filter_status, filter_reason = apply_filter(posting, settings.get("red_flag_phrases") or [])
    if filter_status == "archived":
        db.update_posting(posting["id"], {"status": "archived", "filter_reason": filter_reason})
        db.log_event(
            entity_type="posting", event="filtered_archived", entity_id=posting["id"],
            payload={"reason": filter_reason},
        )
        return posting

    db.update_posting(posting["id"], {"status": "filtered"})

    bullets = db.get_bullets()
    score_row = score_posting(anthropic, posting, bullets)
    score_row["posting_id"] = posting["id"]
    score = db.insert_score(score_row)
    db.log_event(
        entity_type="score", event="scored", entity_id=score["id"],
        payload={"cost_usd": score_row["cost_usd"], "total": score_row["total"]},
    )

    min_score = settings.get("queue_min_score", 55)
    if score_row["total"] < min_score:
        db.update_posting(
            posting["id"],
            {"status": "archived", "filter_reason": f"score {score_row['total']} below queue_min_score {min_score}"},
        )
    else:
        db.update_posting(posting["id"], {"status": "scored"})

    return posting


def handle_job_closed(db: SightlineDB, payload: dict[str, Any]) -> None:
    external_id = str(payload["id"])
    db.mark_posting_closed(external_id=external_id, closed_at=payload.get("closed_at", ""))
    db.log_event(
        entity_type="posting",
        event="closed",
        payload={"source": "theirstack_webhook", "theirstack_job_id": payload["id"], "credits_consumed": 1},
    )


def handle_webhook_event(
    db: SightlineDB,
    anthropic: AnthropicClient,
    theirstack: TheirStackClient,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch on the envelope's `type`. Unknown/unsubscribed types are logged
    and ignored rather than erroring — we only ever subscribe to job.new and
    job.closed, but a stray company.new shouldn't take the endpoint down.

    One endpoint serves both search profiles' webhooks — the envelope
    doesn't distinguish which profile matched, so job.new classifies it
    itself from the job title (see classify_search_profile).

    Runs both credit circuit breakers after any event that cost a credit —
    the monthly budget limit and the daily throttle. Both can only stop
    *future* deliveries — the credit for the event we just processed was
    already spent before we ever received the request."""
    event_type = event["type"]
    payload = event["payload"]
    settings = db.get_settings()

    if event_type == "job.new":
        profiles = db.get_search_profiles()
        posting = handle_job_new(db, anthropic, settings, profiles, payload)
        result: dict[str, Any] = {"ok": True, "type": event_type, "posting_id": posting["id"]}
    elif event_type == "job.closed":
        handle_job_closed(db, payload)
        result = {"ok": True, "type": event_type}
    else:
        db.log_event(entity_type="webhook", event="unhandled_event_type", payload={"type": event_type})
        return {"ok": True, "type": event_type, "ignored": True}

    check_and_enforce_budget(theirstack, db, monthly_credit_budget=settings.get("monthly_credits", 200))
    check_and_enforce_daily_cap(theirstack, db, settings.get("daily_credit_cap"), settings)
    return result
