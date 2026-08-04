"""Six real counters for the dashboard's Metrics tab — see the `v-met` view
in prototype/sightline-dashboard.html ("Six counters, one query each.").
"""
from __future__ import annotations

import datetime as _dt
import statistics
from typing import Any

from sightline.db import SightlineDB

WINDOW_DAYS = 7


def _duration_string(hours: float) -> str:
    """Matches dashboard.py's _age_string format: 'Nh' under a day, else 'Nd'."""
    if hours < 24:
        return f"{max(1, round(hours))}h"
    return f"{round(hours / 24)}d"


def compute_metrics(db: SightlineDB, settings: dict[str, Any]) -> dict[str, Any]:
    since = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=WINDOW_DAYS)).isoformat()
    score_threshold = settings.get("score_threshold", 70)

    submitted_rows = db.submitted_applications_since(since)
    lead_times = []
    for row in submitted_rows:
        posting = row.get("postings") or {}
        first_seen, submitted_at = posting.get("first_seen_at"), row.get("submitted_at")
        if not first_seen or not submitted_at:
            continue
        seen = _dt.datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
        sub = _dt.datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
        lead_times.append((sub - seen).total_seconds() / 3600)

    return {
        "medianLeadTime": _duration_string(statistics.median(lead_times)) if lead_times else "—",
        "ingested": db.count_postings_since(since),
        "surfaced": db.count_scored_above_since(since, score_threshold),
        "submitted": len(submitted_rows),
        "replies": db.replied_outreach_since(since),
        "scoringSpend": round(db.scoring_cost_since(since), 2),
    }
