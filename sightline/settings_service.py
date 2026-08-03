"""Settings persistence, and keeping TheirStack's saved search in sync.

See CLAUDE.md: settings are data, not code — the app pushes fetch-criteria
changes to TheirStack via API rather than requiring manual reconfiguration
in their app UI.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sightline.budget import SAVED_SEARCH_NAME
from sightline.db import SightlineDB
from sightline.theirstack import TheirStackClient, build_filters_from_settings

# Fields that affect what TheirStack actually sends us. Changing any of these
# without re-syncing the saved search would mean the dashboard shows a
# setting that isn't being enforced upstream — a silent lie.
FETCH_CRITERIA_FIELDS = frozenset({
    "title_include", "title_exclude", "remote_only", "open_only",
    "direct_employer", "countries", "min_employee_count", "employment_types",
})


def update_settings(
    db: SightlineDB, theirstack: TheirStackClient, fields: dict[str, Any]
) -> dict[str, Any]:
    updated = db.update_settings(fields)
    if FETCH_CRITERIA_FIELDS & fields.keys():
        theirstack.upsert_saved_search(SAVED_SEARCH_NAME, build_filters_from_settings(updated))
    return updated


def preview_query(theirstack: TheirStackClient, filters: dict[str, Any]) -> dict[str, Any]:
    """Real numbers from TheirStack's free-count/preview, not a client-side
    simulation. Uses a 7-day count rather than a 1-day count for the daily
    estimate — verified empirically that single-day counts are noisy (9 vs.
    a 42.4/day 7-day average on the same query), so a 1-day number would be
    actively misleading for tuning decisions."""
    # Three independent TheirStack round-trips, each several seconds on their
    # own — run concurrently so "Run free preview" doesn't read as hung.
    with ThreadPoolExecutor(max_workers=3) as pool:
        week_future = pool.submit(theirstack.free_count, {**filters, "posted_at_max_age_days": 7})
        backlog_future = pool.submit(
            theirstack.free_count, {**filters, "posted_at_max_age_days": 3650}
        )
        sample_future = pool.submit(theirstack.preview, filters, limit=8)
        week = week_future.result()
        backlog = backlog_future.result()
        sample = sample_future.result()
    return {
        "day": round(week / 7, 1),
        "week": week,
        "backlog": backlog,
        "sample": sample.get("data", []),
    }
