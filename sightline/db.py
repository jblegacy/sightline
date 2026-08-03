"""Supabase (PostgREST) data access. HTTPS only — no raw Postgres connection,
so this works from anywhere, including sandboxes that block non-443 ports.
"""
from __future__ import annotations

from typing import Any

import httpx

from sightline.config import Settings


class SightlineDB:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
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

    def get_settings(self) -> dict[str, Any]:
        resp = self._client.get("/settings", params={"id": "eq.1", "select": "*"})
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise LookupError("settings row (id=1) not found — run the Phase 0 migration")
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

    def list_scored_postings(self) -> list[dict[str, Any]]:
        """Postings that survived scoring — status='scored' means total already
        cleared queue_min_score (anything lower was archived at ingest time).
        Embeds company and score in one PostgREST call rather than N+1 queries."""
        resp = self._client.get(
            "/postings",
            params={
                "status": "eq.scored",
                "select": "*,companies(id,name),scores(*)",
                "order": "first_seen_at.desc",
            },
        )
        resp.raise_for_status()
        return resp.json()
