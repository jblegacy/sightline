"""One-time, tranched, bounded pull from TheirStack's existing backlog — see
CLAUDE.md's TheirStack section: "A one-time backlog sweep (if ever done) must
be tranched and bounded." Ingest is normally webhook-driven; this script is
the deliberate manual exception, for seeding the queue instead of waiting on
organic job.new deliveries.

Each job returned by TheirStack's real (unblurred, charged) search costs
1 credit — there is no free tier for this call, unlike free_count()/preview().
Every job pulled is run through the exact same pipeline as a live webhook
event (sightline.ingest.handle_job_new): idempotent on external_id, subject
to the same deterministic filter and scoring.

Usage:
    python -m scripts.backlog_sweep --automation-limit 30 --cpg-limit 20

Refuses to run with no limits set — there is no default tranche size on
purpose, so a sweep is never accidentally unbounded.
"""
from __future__ import annotations

import argparse
import sys

from sightline.anthropic_client import AnthropicClient
from sightline.config import get_settings
from sightline.db import SightlineDB
from sightline.ingest import handle_job_new
from sightline.theirstack import TheirStackClient, build_filters_for_profile


def sweep_profile(db, anthropic, ts, settings, profiles, profile, limit: int) -> int:
    filters = {**build_filters_for_profile(profile, settings), "limit": limit}
    jobs = ts.search_jobs(filters).get("data", [])
    for job in jobs:
        posting = handle_job_new(db, anthropic, settings, profiles, job)
        db.log_event(
            entity_type="posting", event="backlog_sweep_ingested", entity_id=posting["id"],
            payload={"profile_id": profile["id"], "theirstack_job_id": job["id"]},
        )
    return len(jobs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--automation-limit", type=int, default=0, help="jobs (= credits) to pull for automation")
    parser.add_argument("--cpg-limit", type=int, default=0, help="jobs (= credits) to pull for cpg")
    args = parser.parse_args()

    if args.automation_limit == 0 and args.cpg_limit == 0:
        print("Refusing to run with no bounds set — pass --automation-limit and/or --cpg-limit.")
        sys.exit(1)

    settings_obj = get_settings()
    db = SightlineDB(settings_obj)
    anthropic = AnthropicClient(api_key=settings_obj.anthropic_api_key)
    ts = TheirStackClient(api_key=settings_obj.theirstack_api_key)

    db_settings = db.get_settings()
    profiles_by_id = {p["id"]: p for p in db.get_search_profiles()}
    profiles = list(profiles_by_id.values())

    total = 0
    for profile_id, limit in (("automation", args.automation_limit), ("cpg", args.cpg_limit)):
        if not limit:
            continue
        n = sweep_profile(db, anthropic, ts, db_settings, profiles, profiles_by_id[profile_id], limit)
        print(f"{profile_id}: pulled {n} jobs")
        total += n
    print(f"total credits spent this run: {total}")


if __name__ == "__main__":
    main()
