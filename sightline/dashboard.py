"""Transforms DB rows into the shapes prototype/sightline-dashboard.html
expects. That prototype is the API contract per CLAUDE.md — match its shapes,
don't invent new ones. See the `P` array and `CFG`/`QV` objects in the
prototype source for the canonical field list.

Scope note: `app`/`o` (application/outreach state) and `brief` (Phase 5's
Sonnet-generated brief) aren't populated here — those belong to Phase 5/6,
which aren't built yet. Rows render fine without them; the prototype already
treats them as optional.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sightline.scoring import DIMENSIONS

_VARIANT_CODE = {"engineer": "eng", "leadership": "lead"}


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

    return {
        "id": row["id"],
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
        "stage": "queue",  # 'watch' vs 'queue' split is a queue-layer threshold, applied by the caller
        "rat": score.get("rationale") or "",
        "kw": score.get("keywords") or [],
        "gaps": score.get("unmet_requirements") or [],
        "brief": "",  # Phase 5 — not generated yet
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
