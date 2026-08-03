"""Turns a TheirStack webhook event into rows in `postings`/`companies`/`events`.

Pure mapping + orchestration, no HTTP concerns — web/main.py owns the request/
response side (signature check, status codes) and calls into this module.
"""
from __future__ import annotations

import hashlib
from typing import Any

from sightline.db import SightlineDB


def job_to_posting(job: dict[str, Any], company_id: int) -> dict[str, Any]:
    """Map a TheirStack job object (search response or job.new payload — same
    schema) to a `postings` row. See docs/THEIRSTACK_API_REFERENCE.md §7."""
    comp_min = job.get("min_annual_salary_usd")
    comp_max = job.get("max_annual_salary_usd")
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
    }


def handle_job_new(db: SightlineDB, job: dict[str, Any]) -> dict[str, Any]:
    """1 credit was already spent delivering this event — log it regardless of
    what happens next; the credit ledger reflects real billing, not our DB state."""
    company_id = db.upsert_company(
        name=job.get("company") or job.get("company_object", {}).get("name") or "Unknown",
        domain=job.get("company_domain") or job.get("company_object", {}).get("domain"),
    )
    posting = db.upsert_posting(job_to_posting(job, company_id))
    db.log_event(
        entity_type="posting",
        event="ingested",
        entity_id=posting["id"],
        payload={"source": "theirstack_webhook", "theirstack_job_id": job["id"], "credits_consumed": 1},
    )
    return posting


def handle_job_closed(db: SightlineDB, payload: dict[str, Any]) -> None:
    external_id = str(payload["id"])
    db.mark_posting_closed(external_id=external_id, closed_at=payload.get("closed_at", ""))
    db.log_event(
        entity_type="posting",
        event="closed",
        payload={"source": "theirstack_webhook", "theirstack_job_id": payload["id"], "credits_consumed": 1},
    )


def handle_webhook_event(db: SightlineDB, event: dict[str, Any]) -> dict[str, Any]:
    """Dispatch on the envelope's `type`. Unknown/unsubscribed types are logged
    and ignored rather than erroring — we only ever subscribe to job.new and
    job.closed, but a stray company.new shouldn't take the endpoint down."""
    event_type = event["type"]
    payload = event["payload"]
    if event_type == "job.new":
        posting = handle_job_new(db, payload)
        return {"ok": True, "type": event_type, "posting_id": posting["id"]}
    if event_type == "job.closed":
        handle_job_closed(db, payload)
        return {"ok": True, "type": event_type}
    db.log_event(entity_type="webhook", event="unhandled_event_type", payload={"type": event_type})
    return {"ok": True, "type": event_type, "ignored": True}
