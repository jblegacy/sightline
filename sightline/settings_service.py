"""Settings persistence, and keeping both TheirStack search profiles in sync.

See CLAUDE.md: settings — and now search_profiles — are data, not code. The
app pushes fetch-criteria changes to TheirStack via API rather than
requiring manual reconfiguration in their app UI.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sightline.budget import saved_search_name
from sightline.db import SightlineDB
from sightline.theirstack import TheirStackClient, build_filters_for_profile

# Shared fields that affect what BOTH search profiles fetch from TheirStack.
# Changing any of these without re-syncing both saved searches would mean
# the dashboard shows a setting that isn't being enforced upstream — a
# silent lie. Title lists live on search_profiles now, not here — see
# update_search_profile.
SHARED_FETCH_CRITERIA_FIELDS = frozenset({
    "remote_only", "open_only", "direct_employer", "countries",
    "min_employee_count", "employment_types", "seniority", "source_exclude",
    "fetch_salary_filter", "fetch_salary_min", "fetch_salary_max",
})


def _sync_all_profiles(db: SightlineDB, theirstack: TheirStackClient, settings: dict[str, Any]) -> None:
    for profile in db.get_search_profiles():
        theirstack.upsert_saved_search(
            saved_search_name(profile["id"]), build_filters_for_profile(profile, settings)
        )


def update_settings(
    db: SightlineDB, theirstack: TheirStackClient, fields: dict[str, Any]
) -> dict[str, Any]:
    updated = db.update_settings(fields)
    if SHARED_FETCH_CRITERIA_FIELDS & fields.keys():
        _sync_all_profiles(db, theirstack, updated)
    return updated


def update_search_profile(
    db: SightlineDB, theirstack: TheirStackClient, profile_id: str, fields: dict[str, Any]
) -> dict[str, Any]:
    updated = db.update_search_profile(profile_id, fields)
    if {"title_include", "title_exclude"} & fields.keys():
        settings = db.get_settings()
        theirstack.upsert_saved_search(
            saved_search_name(profile_id), build_filters_for_profile(updated, settings)
        )
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
