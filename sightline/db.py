"""Supabase (PostgREST) data access. HTTPS only — no raw Postgres connection,
so this works from anywhere, including sandboxes that block non-443 ports.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from sightline.config import Settings


class SightlineDB:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._storage_origin = settings.supabase_url
        self._client = client or httpx.Client(
            base_url=f"{settings.supabase_url}/rest/v1",
            headers={
                "apikey": settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def upsert_company(self, name: str, domain: str | None) -> int:
        """Upsert by domain when known; otherwise insert a new row by name.

        Companies without a domain can't be deduped server-side (no unique key),
        so callers should prefer passing a domain whenever TheirStack provides one.
        """
        if domain:
            resp = self._client.post(
                "/companies",
                params={"on_conflict": "domain"},
                headers={"Prefer": "resolution=merge-duplicates,return=representation"},
                json={"name": name, "domain": domain},
            )
        else:
            existing = self._client.get(
                "/companies", params={"name": f"eq.{name}", "domain": "is.null", "select": "id"}
            )
            existing.raise_for_status()
            rows = existing.json()
            if rows:
                return rows[0]["id"]
            resp = self._client.post(
                "/companies",
                headers={"Prefer": "return=representation"},
                json={"name": name, "domain": None},
            )
        resp.raise_for_status()
        return resp.json()[0]["id"]

    def find_posting_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        resp = self._client.get(
            "/postings", params={"external_id": f"eq.{external_id}", "select": "id,status"}
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def upsert_posting(self, posting: dict[str, Any]) -> dict[str, Any]:
        """Upsert on external_id (TheirStack's globally-unique job id)."""
        resp = self._client.post(
            "/postings",
            params={"on_conflict": "external_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json=posting,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0]

    def mark_posting_closed(self, external_id: str, closed_at: str) -> None:
        """job.closed carries only {id, closed_at} — no closed_at column exists
        on postings, so it's folded into `raw` alongside a status flip.
        Silently no-ops if we never saw the posting (e.g. it closed before our
        webhook existed), matching CLAUDE.md's archive-don't-error posture."""
        resp = self._client.patch(
            "/postings",
            params={"external_id": f"eq.{external_id}"},
            headers={"Prefer": "return=representation"},
            json={"status": "expired", "filter_reason": f"closed_at={closed_at}"},
        )
        resp.raise_for_status()

    def archive_posting_by_id(self, posting_id: int, reason: str) -> None:
        """Same effect as mark_posting_closed, but for the user's own manual
        "archive this" click — pure judgment call, no auto-detection of
        whether the listing is actually dead (deliberately not attempted;
        the user can tell better than a URL check can). Keyed by our
        internal id since that's what the dashboard already has, not
        TheirStack's external_id. Zero TheirStack credits; this is a
        queue-filter action (hide, don't delete, always reversible in the
        data even with no undo button today) per CLAUDE.md's filter-layer
        split, not a fetch-filter one."""
        resp = self._client.patch(
            "/postings",
            params={"id": f"eq.{posting_id}"},
            headers={"Prefer": "return=representation"},
            json={"status": "expired", "filter_reason": reason},
        )
        resp.raise_for_status()

    def log_event(
        self,
        entity_type: str,
        event: str,
        entity_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        resp = self._client.post(
            "/events",
            json={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "event": event,
                "payload": payload or {},
            },
        )
        resp.raise_for_status()

    def get_latest_event(self, events: list[str]) -> dict[str, Any] | None:
        """Most recent row among the given event names — used to figure out
        which of two possible states is currently "in effect" (e.g. was the
        daily throttle or the monthly circuit breaker the last thing to
        touch the search profiles) without a dedicated status column."""
        resp = self._client.get(
            "/events",
            params={"event": f"in.({','.join(events)})", "order": "id.desc", "limit": "1"},
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def get_settings(self) -> dict[str, Any]:
        resp = self._client.get("/settings", params={"id": "eq.1", "select": "*"})
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise LookupError("settings row (id=1) not found — run the Phase 0 migration")
        return rows[0]

    def get_search_profiles(self) -> list[dict[str, Any]]:
        resp = self._client.get(
            "/search_profiles", params={"select": "*", "active": "eq.true", "order": "id"}
        )
        resp.raise_for_status()
        return resp.json()

    def update_search_profile(self, profile_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.patch(
            "/search_profiles", params={"id": f"eq.{profile_id}"},
            headers={"Prefer": "return=representation"}, json=fields,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise LookupError(f"search profile {profile_id!r} not found")
        return rows[0]

    def get_bullets(self) -> list[dict[str, Any]]:
        """All bullets regardless of status — scoring is keyword-matching
        guidance, not a document build, so it isn't gated by the provenance
        validator the way assembly (Phase 5) will be."""
        resp = self._client.get("/bullets", params={"select": "ref,text,tags,variants"})
        resp.raise_for_status()
        return resp.json()

    def update_posting(self, posting_id: int, fields: dict[str, Any]) -> None:
        resp = self._client.patch("/postings", params={"id": f"eq.{posting_id}"}, json=fields)
        resp.raise_for_status()

    def insert_score(self, score: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(
            "/scores", headers={"Prefer": "return=representation"}, json=score
        )
        resp.raise_for_status()
        return resp.json()[0]

    def update_settings(self, fields: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.patch(
            "/settings", params={"id": "eq.1"}, headers={"Prefer": "return=representation"},
            json=fields,
        )
        resp.raise_for_status()
        return resp.json()[0]

    def get_bullets_full(self) -> list[dict[str, Any]]:
        """Full bullet rows for assembly — includes provenance/status (what
        the validator gates on) and source_org/source_period (for grouping
        into resume sections), unlike get_bullets() which is scoring-context
        only."""
        resp = self._client.get(
            "/bullets",
            params={
                "select": "id,ref,text,source_org,source_period,tags,variants,provenance,status"
            },
        )
        resp.raise_for_status()
        return resp.json()

    def get_posting(self, posting_id: int) -> dict[str, Any]:
        resp = self._client.get(
            "/postings",
            params={
                "id": f"eq.{posting_id}",
                "select": "*,companies(id,name),scores(*),variants(*),outreach(*),applications(*)",
                "scores.order": "id.desc",
                "variants.order": "id.desc",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise LookupError(f"posting {posting_id} not found")
        return rows[0]

    def insert_variant(self, fields: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(
            "/variants", headers={"Prefer": "return=representation"}, json=fields
        )
        resp.raise_for_status()
        return resp.json()[0]

    def update_variant(self, variant_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.patch(
            "/variants", params={"id": f"eq.{variant_id}"},
            headers={"Prefer": "return=representation"}, json=fields,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise LookupError(f"variant {variant_id} not found")
        return rows[0]

    def upload_document(self, bucket: str, path: str, content: bytes) -> None:
        """Storage's REST surface lives at /storage/v1, not /rest/v1 — this
        client's base_url is PostgREST's (.../rest/v1), and httpx merges a
        leading-slash relative path onto that base rather than replacing it
        (found live: it was actually requesting /rest/v1/storage/v1/... and
        404ing — this was assembly's first real invocation all session, the
        provenance gate blocked every earlier attempt). Passing a full
        absolute URL bypasses base_url entirely instead of merging with it.
        """
        resp = self._client.post(
            f"{self._storage_origin}/storage/v1/object/{bucket}/{path}",
            content=content,
            headers={
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "x-upsert": "true",
            },
        )
        resp.raise_for_status()

    def create_signed_url(
        self, bucket: str, path: str, expires_in: int = 3600, download_filename: str | None = None
    ) -> str:
        """Bucket is private (see CLAUDE.md) — every download goes through a
        signed, time-limited URL, never a public one. Supabase's response
        `signedURL` is relative to /storage/v1 (e.g. "/object/sign/..."),
        not to the bare origin — found live: concatenating it onto the bare
        origin dropped /storage/v1 and produced a 404 download link.

        `download_filename`, when given, is appended as a `download` query
        param — Supabase Storage honors it as the Content-Disposition
        filename, so the object key (which carries a variant/timestamp for
        uniqueness) can differ from what actually lands on disk."""
        resp = self._client.post(
            f"{self._storage_origin}/storage/v1/object/sign/{bucket}/{path}",
            json={"expiresIn": expires_in},
        )
        resp.raise_for_status()
        url = f"{self._storage_origin}/storage/v1{resp.json()['signedURL']}"
        if download_filename:
            url += f"&download={quote(download_filename)}"
        return url

    def list_scored_postings(self) -> list[dict[str, Any]]:
        """Postings that survived scoring — status='scored' means total already
        cleared queue_min_score (anything lower was archived at ingest time).
        Embeds company, score, variant (if assembled), and outreach (if
        drafted) in one PostgREST call rather than N+1 queries."""
        resp = self._client.get(
            "/postings",
            params={
                "status": "eq.scored",
                "select": "*,companies(id,name),scores(*),variants(*),outreach(*),applications(*)",
                "order": "first_seen_at.desc",
                "scores.order": "id.desc",
                "variants.order": "id.desc",
            },
        )
        resp.raise_for_status()
        return resp.json()

    def get_answers(self) -> list[dict[str, Any]]:
        resp = self._client.get("/answers", params={"select": "*", "order": "ref"})
        resp.raise_for_status()
        return resp.json()

    def upsert_answer(self, fields: dict[str, Any]) -> dict[str, Any]:
        """answers.ref is unique (migration 0002) — upsert on that, same
        pattern as applications.posting_id."""
        resp = self._client.post(
            "/answers", params={"on_conflict": "ref"},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json=fields,
        )
        resp.raise_for_status()
        return resp.json()[0]

    def override_score(self, score_id: int, total: int | None, reason: str | None) -> dict[str, Any]:
        """Corrects the effective score for a posting without touching the
        AI's original `total` — that stays the calibration baseline. Pass
        total=None to clear a previously-set override."""
        import datetime as _dt

        resp = self._client.patch(
            "/scores", params={"id": f"eq.{score_id}"},
            headers={"Prefer": "return=representation"},
            json={
                "human_override_total": total,
                "human_override_reason": reason,
                "human_override_at": (
                    _dt.datetime.now(_dt.timezone.utc).isoformat() if total is not None else None
                ),
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise LookupError(f"score {score_id} not found")
        return rows[0]

    def upsert_application(self, fields: dict[str, Any]) -> dict[str, Any]:
        """One row per posting — Defer, Reject, Mark submitted, the status
        dropdown, notes, and Record final on the dashboard all funnel here.
        See sightline/dashboard.py for how status/submitted_at drive the
        posting's displayed stage."""
        import datetime as _dt

        resp = self._client.post(
            "/applications",
            params={"on_conflict": "posting_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json={**fields, "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat()},
        )
        resp.raise_for_status()
        return resp.json()[0]

    def count_postings_since(self, since_iso: str) -> int:
        resp = self._client.get(
            "/postings", params={"first_seen_at": f"gte.{since_iso}", "select": "id"}
        )
        resp.raise_for_status()
        return len(resp.json())

    def count_scored_above_since(self, since_iso: str, score_threshold: int) -> int:
        """Postings whose latest score cleared the queue threshold, ingested
        in the window — matches what 'surfaced to queue' means on the
        dashboard (see dashboard.py's stage derivation)."""
        resp = self._client.get(
            "/postings",
            params={
                "first_seen_at": f"gte.{since_iso}",
                "status": "eq.scored",
                "select": "id,scores(total)",
                "scores.order": "id.desc",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return sum(
            1 for r in rows if (r.get("scores") or [{}])[0].get("total", 0) >= score_threshold
        )

    def submitted_applications_since(self, since_iso: str) -> list[dict[str, Any]]:
        resp = self._client.get(
            "/applications",
            params={
                "submitted_at": f"gte.{since_iso}",
                "select": "submitted_at,postings(first_seen_at)",
            },
        )
        resp.raise_for_status()
        return resp.json()

    def replied_outreach_since(self, since_iso: str) -> int:
        resp = self._client.get(
            "/outreach", params={"replied_at": f"gte.{since_iso}", "select": "id"}
        )
        resp.raise_for_status()
        return len(resp.json())

    def scoring_cost_since(self, since_iso: str) -> float:
        resp = self._client.get(
            "/scores", params={"created_at": f"gte.{since_iso}", "select": "cost_usd"}
        )
        resp.raise_for_status()
        return sum(r.get("cost_usd") or 0 for r in resp.json())

    def upsert_outreach(self, fields: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(
            "/outreach",
            params={"on_conflict": "posting_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json=fields,
        )
        resp.raise_for_status()
        return resp.json()[0]

    def mark_outreach_sent(self, posting_id: int, channel: str) -> dict[str, Any]:
        import datetime as _dt

        follow_up_due = (_dt.date.today() + _dt.timedelta(days=7)).isoformat()
        resp = self._client.patch(
            "/outreach",
            params={"posting_id": f"eq.{posting_id}"},
            headers={"Prefer": "return=representation"},
            json={
                "sent_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "sent_channel": channel,
                "follow_up_due": follow_up_due,
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise LookupError(f"no outreach row for posting {posting_id}")
        return rows[0]
