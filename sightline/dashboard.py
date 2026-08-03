"""Transforms DB rows into the shapes prototype/sightline-dashboard.html
expects. That prototype is the API contract per CLAUDE.md — match its shapes,
don't invent new ones. See the `P` array and `CFG`/`QV` objects in the
prototype source for the canonical field list.

`app` (resume/application state) is populated from the `variants` embed when
a posting has been assembled; `o` (outreach state) from the `outreach`
embed once drafts exist. Both stay None otherwise — the prototype already
treats them as optional. Application-status tracking beyond "resume ready"
(submitted/screen/interview/etc.) isn't backed by the `applications` table
yet and stays client-side, same as before.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sightline.scoring import DIMENSIONS

_VARIANT_CODE = {"engineer": "eng", "leadership": "lead"}
_VARIANT_LABEL = {"engineer": "Engineer", "leadership": "Leadership"}


def _variant_to_app(variant: dict[str, Any]) -> dict[str, Any]:
    return {
        "variant": _VARIANT_LABEL.get(variant.get("kind"), variant.get("kind")),
        "file": (variant.get("storage_path") or "").split("/")[-1] or None,
        "status": "ready to submit",
        "sent": None,
        "due": None,
        "notes": "",
    }


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

    return {
        "id": row["id"],
        "app": _variant_to_app(variants[0]) if variants else None,
        "o": _outreach_to_o(outreach[0]) if outreach else None,
        "co": company.get("name", "Unknown"),
        "ti": row["title"],
        "loc": row.get("location_raw") or "not stated",
        "age": _age_string(row["first_seen_at"]),
        "compMin": row.get("comp_min"),
        "compMax": row.get("comp_max"),
        "compSrc": row.get("comp_source") or "absent",
        "coCount": co_count,
        "ko": score.get("knockouts") or [],
        "score": score["total"],
        "d": _dimensions_array(score["dimensions"]),
        "v": _VARIANT_CODE.get(score.get("suggested_variant"), "eng"),
        "stage": "queue",  # placeholder — postings_to_dashboard_p sets the real value below
        "rat": score.get("rationale") or "",
        "kw": score.get("keywords") or [],
        "gaps": score.get("unmet_requirements") or [],
        "brief": variants[0].get("brief") or "" if variants else "",
        "rt": score.get("reports_to") or "Not stated in posting",
        "nc": [
            {"n": c.get("name"), "t": c.get("title")} for c in (score.get("named_contacts") or [])
        ],
        "tt": score.get("target_titles") or [],
        "sig": score.get("company_signals") or [],
    }


def postings_to_dashboard_p(rows: list[dict[str, Any]], score_threshold: int) -> list[dict[str, Any]]:
    co_counts = Counter(r["company_id"] for r in rows if r.get("company_id"))
    result = []
    for row in rows:
        p = posting_row_to_p(row, co_counts.get(row.get("company_id"), 1))
        if p is None:
            continue
        if p["app"] is not None:
            p["stage"] = "approved"  # resume built; ATS-submission tracking isn't wired yet
        else:
            p["stage"] = "queue" if p["score"] >= score_threshold else "watch"
        result.append(p)
    return result


def settings_to_cfg_qv(settings: dict[str, Any]) -> dict[str, Any]:
    """Matches the prototype's separate CFG (fetch criteria) and QV (queue
    filter) objects."""
    return {
        "cfg": {
            "inc": settings.get("title_include") or [],
            "exc": settings.get("title_exclude") or [],
        },
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
