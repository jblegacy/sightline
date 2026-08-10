"""Transforms DB rows into the shapes prototype/sightline-dashboard.html
expects. That prototype is the API contract per CLAUDE.md — match its shapes,
don't invent new ones. See the `P` array and `CFG`/`QV` objects in the
prototype source for the canonical field list.

`app` (resume/application state) comes from the `applications` row once one
exists (Defer, Reject, Mark submitted, a status change, notes, or Record
final all create one), falling back to a synthesized "ready to submit"
view of the `variants` embed when a resume's been built but no application
action has happened yet. `o` (outreach state) comes from the `outreach`
embed once drafts exist. All stay None otherwise — the prototype already
treats them as optional.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sightline.assembly import RESUME_DOWNLOAD_NAME
from sightline.scoring import DIMENSIONS

_VARIANT_CODE = {"engineer": "eng", "leadership": "lead"}
_VARIANT_LABEL = {"engineer": "Engineer", "leadership": "Leadership"}


def _variant_to_app(variant: dict[str, Any]) -> dict[str, Any]:
    return {
        "variant": _VARIANT_LABEL.get(variant.get("kind"), variant.get("kind")),
        "file": RESUME_DOWNLOAD_NAME if variant.get("storage_path") else None,
        "finalFile": None,
        "coverLetterFile": (variant.get("cover_letter_storage_path") or "").split("/")[-1] or None,
        "status": "ready to submit",
        "sent": None,
        "due": None,
        "notes": "",
    }


def _application_to_app(application: dict[str, Any], variant: dict[str, Any] | None) -> dict[str, Any]:
    variant = variant or {}
    return {
        "variant": _VARIANT_LABEL.get(variant.get("kind"), variant.get("kind")) if variant else None,
        "file": (RESUME_DOWNLOAD_NAME if variant.get("storage_path") else None) if variant else None,
        "finalFile": application.get("final_filename"),
        "coverLetterFile": (variant.get("cover_letter_storage_path") or "").split("/")[-1] or None,
        "status": application.get("status") or ("ready to submit" if variant else "deferred"),
        "sent": application.get("submitted_at"),
        "due": application.get("follow_up_due"),
        "notes": application.get("notes") or "",
    }


def _stage_from_app(app: dict[str, Any] | None, score: int, score_threshold: int) -> str:
    if app is None:
        return "queue" if score >= score_threshold else "watch"
    status = app["status"]
    if status == "queued":
        return "queue"  # explicit "not now" — reverses Approve, keeps any built variant
    if status == "deferred":
        return "watch"
    if status == "rejected" and not app["sent"]:
        return "rejected"  # queue-level reject, before ever applying
    if app["sent"] or status in ("submitted", "screen", "interview", "offer", "ghosted", "rejected"):
        return "applied"
    return "approved"


def _outreach_to_o(outreach: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": outreach.get("target_name") or "",
        "title": outreach.get("target_title") or "",
        "url": outreach.get("target_linkedin_url") or "",
        "note": outreach.get("draft_linkedin_note"),
        "message": outreach.get("draft_linkedin_message"),
        "subject": outreach.get("draft_email_subject"),
        "email": outreach.get("draft_email_body"),
        "sent": outreach.get("sent_at"),
    }


def _age_string(first_seen_at: str) -> str:
    """Matches the prototype's own format: 'Nh' under a day, else 'Nd' — its
    filter logic (`p.age.includes('h') ? 0 : parseInt(p.age)`) depends on
    exactly this shape, not on the value being a real duration otherwise."""
    seen = datetime.fromisoformat(first_seen_at.replace("Z", "+00:00"))
    delta = datetime.now(timezone.utc) - seen
    hours = delta.total_seconds() / 3600
    if hours < 24:
        return f"{max(1, round(hours))}h"
    return f"{round(hours / 24)}d"


def _dimensions_array(dimensions: dict[str, int]) -> list[int]:
    """dict -> ordered array matching DIMS in the prototype, which is drawn
    positionally (index into DIMS), not by key."""
    return [dimensions.get(key, 0) for key, _ in DIMENSIONS]


def posting_row_to_p(row: dict[str, Any], co_count: int) -> dict[str, Any] | None:
    """One `postings` row (with embedded companies/scores) -> one P-array
    entry. Returns None for a posting with no score yet (shouldn't happen for
    status='scored', but don't render garbage if it does)."""
    scores = row.get("scores") or []
    if not scores:
        return None
    score = scores[0]
    company = row.get("companies") or {}
    variants = row.get("variants") or []
    outreach = row.get("outreach") or []
    # applications.posting_id is unique (migration 0016), so PostgREST embeds
    # it as a to-one object, not an array like variants/outreach above.
    application = row.get("applications") or None
    variant = variants[0] if variants else None

    if application:
        app = _application_to_app(application, variant)
    elif variant:
        app = _variant_to_app(variant)
    else:
        app = None

    # A human override corrects the *effective* score (queue/watch, display)
    # without touching the AI's own total — that stays the calibration
    # baseline toward a future rubric_version revision, not something a
    # correction silently erases.
    override_total = score.get("human_override_total")
    effective_score = override_total if override_total is not None else score["total"]

    return {
        "id": row["id"],
        "app": app,
        "o": _outreach_to_o(outreach[0]) if outreach else None,
        "co": company.get("name", "Unknown"),
        "ti": row["title"],
        "url": row.get("url"),
        "loc": row.get("location_raw") or "not stated",
        "age": _age_string(row["first_seen_at"]),
        "compMin": row.get("comp_min"),
        "compMax": row.get("comp_max"),
        "compSrc": row.get("comp_source") or "absent",
        "coCount": co_count,
        "ko": score.get("knockouts") or [],
        "score": effective_score,
        "aiScore": score["total"],
        "scoreOverride": (
            {"total": override_total, "reason": score.get("human_override_reason")}
            if override_total is not None else None
        ),
        "d": _dimensions_array(score["dimensions"]),
        "v": _VARIANT_CODE.get(score.get("suggested_variant"), "eng"),
        "stage": "queue",  # placeholder — postings_to_dashboard_p sets the real value below
        "rat": score.get("rationale") or "",
        "kw": score.get("keywords") or [],
        "gaps": score.get("unmet_requirements") or [],
        "brief": (variant.get("brief") or "") if variant else "",
        "rt": score.get("reports_to") or "Not stated in posting",
        "nc": [
            {"n": c.get("name"), "t": c.get("title")} for c in (score.get("named_contacts") or [])
        ],
        "tt": score.get("target_titles") or [],
        "sig": score.get("company_signals") or [],
        "ci": score.get("coding_interview_signals") or [],
    }


def postings_to_dashboard_p(rows: list[dict[str, Any]], score_threshold: int) -> list[dict[str, Any]]:
    co_counts = Counter(r["company_id"] for r in rows if r.get("company_id"))
    result = []
    for row in rows:
        p = posting_row_to_p(row, co_counts.get(row.get("company_id"), 1))
        if p is None:
            continue
        p["stage"] = _stage_from_app(p["app"], p["score"], score_threshold)
        result.append(p)
    return result


def search_profiles_to_dashboard(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """search_profiles rows -> the shape the dashboard's profile selector
    reads (was a hardcoded PROFILES object in the prototype; now real data
    per CLAUDE.md's "two search profiles are data, not code")."""
    return [
        {
            "id": p["id"],
            "label": p["label"],
            "inc": p.get("title_include") or [],
            "exc": p.get("title_exclude") or [],
            "variant": p["resume_variant"],
            "budgetShare": p["budget_share"],
        }
        for p in profiles
    ]


def settings_to_cfg_qv(settings: dict[str, Any]) -> dict[str, Any]:
    """Matches the prototype's QV (queue filter) object. Fetch-criteria title
    lists moved to search_profiles — see search_profiles_to_dashboard."""
    return {
        "qv": {
            "salMin": settings.get("queue_salary_min", 0),
            "salMax": settings.get("queue_salary_max", 500000),
            "score": settings.get("queue_min_score", 55),
            "age": settings.get("queue_max_age_days", 21),
            "ko": settings.get("queue_ko_tolerance", 9),
            "noSal": settings.get("queue_include_no_salary", True),
        },
        "scoreThreshold": settings.get("score_threshold", 70),
    }
