"""TheirStack API client. See docs/THEIRSTACK_API_REFERENCE.md for the full
verified reference this is built against.

Ingest is webhook-driven (see CLAUDE.md): this client's job is to keep the
saved search and webhook in sync with the `settings` table, plus a manual
jobs/search + free-count path for tuning and one-off backfills.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

BASE_URL = "https://api.theirstack.com"

# TheirStack requires at least one of these on every Job Search / saved-search
# call, or the request fails validation. discovered_at_gte alone doesn't count.
_REQUIRED_ANY_OF = (
    "posted_at_max_age_days",
    "posted_at_gte",
    "posted_at_lte",
    "company_domain_or",
    "company_linkedin_url_or",
    "company_name_or",
)


class TheirStackError(Exception):
    pass


class CreditsExhausted(TheirStackError):
    """HTTP 402 — stop the run, alert, don't retry. Never swallow this."""


def _require_dedup_filter(filters: dict[str, Any]) -> None:
    if not any(k in filters for k in _REQUIRED_ANY_OF):
        raise TheirStackError(
            f"filters must include at least one of {_REQUIRED_ANY_OF} "
            "or TheirStack will reject the request"
        )


def build_filters_from_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Map a `settings` table row to the TheirStack filter body.

    No salary filter — deliberately. See CLAUDE.md: a fetch-time salary filter
    drops every posting with no published band, which is most of them.
    """
    filters: dict[str, Any] = {
        "posted_at_max_age_days": 30,
        "remote": bool(settings.get("remote_only", True)),
        "job_country_code_or": settings.get("countries") or ["US"],
        "company_type": "direct_employer" if settings.get("direct_employer", True) else "all",
        "employment_statuses_or": settings.get("employment_types") or ["full_time", "contract"],
        "job_title_or": settings["title_include"],
        "job_title_not": settings.get("title_exclude") or [],
        "min_employee_count_or_null": settings.get("min_employee_count", 50),
        "limit": 100,
    }
    if settings.get("open_only", True):
        filters["is_closed"] = False
    return filters


class TheirStackClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            base_url=BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, json: dict[str, Any]) -> httpx.Response:
        resp = self._client.post(path, json=json)
        if resp.status_code == 402:
            raise CreditsExhausted(resp.text)
        resp.raise_for_status()
        return resp

    # ---- credits ----

    def credit_balance(self) -> dict[str, Any]:
        resp = self._client.get("/v0/billing/credit-balance")
        resp.raise_for_status()
        return resp.json()

    # ---- job search / free modes ----

    def search_jobs(self, filters: dict[str, Any]) -> dict[str, Any]:
        _require_dedup_filter(filters)
        return self._post("/v1/jobs/search", filters).json()

    def free_count(self, filters: dict[str, Any]) -> int:
        """0-credit estimate of matching records. Verified empirically — see
        docs/THEIRSTACK_API_REFERENCE.md §3. All three of these fields, together,
        not limit:1 alone."""
        query = {**filters, "include_total_results": True, "blur_company_data": True, "limit": 1}
        _require_dedup_filter(query)
        data = self._post("/v1/jobs/search", query).json()
        return data["metadata"]["total_results"]

    def preview(self, filters: dict[str, Any], limit: int = 25) -> dict[str, Any]:
        """0-credit blurred sample — for Settings > Criteria > Preview."""
        query = {**filters, "blur_company_data": True, "limit": limit}
        _require_dedup_filter(query)
        return self._post("/v1/jobs/search", query).json()

    # ---- saved searches + webhooks (settings stay data, not code — see CLAUDE.md) ----

    def find_saved_search(self, name: str) -> dict[str, Any] | None:
        resp = self._client.get("/v0/saved_searches")
        resp.raise_for_status()
        for s in resp.json():
            if s.get("name") == name:
                return s
        return None

    def upsert_saved_search(self, name: str, filters: dict[str, Any]) -> dict[str, Any]:
        """Field names (`body`, `type`) verified against the live OpenAPI spec
        (SavedSearchCreate/SavedSearchUpdate) — the API doesn't use `filters`/
        `search_type` despite those being the more obvious guesses."""
        _require_dedup_filter(filters)
        existing = self.find_saved_search(name)
        body = {"name": name, "type": "jobs", "body": filters, "is_alert_active": True}
        if existing:
            resp = self._client.patch(f"/v0/saved_searches/{existing['id']}", json=body)
        else:
            resp = self._client.post("/v0/saved_searches", json=body)
        resp.raise_for_status()
        return resp.json()

    def find_webhook(self, url: str) -> dict[str, Any] | None:
        """WebhookResponseV0 has no `name` field — match on `url` instead,
        verified against the live OpenAPI spec."""
        resp = self._client.get("/v0/webhooks")
        resp.raise_for_status()
        for w in resp.json():
            if w.get("url") == url:
                return w
        return None

    def upsert_webhook(
        self,
        saved_search_id: int,
        url: str,
        secret: str,
        event_types: list[str],
        description: str = "",
        listen_from_now: bool = True,
    ) -> dict[str, Any]:
        """event_types use underscore form (job_new, job_closed) per
        WebhookEventType — the *delivered* event payload's own `type` field
        uses dots (job.new, job.closed); these are two different enums for
        the request vs. the response, both verified against the live spec.

        listen_from_now=True sets listening_start_time to now, matching the
        webhook-driven design decision (no backlog sweep). Passing False (or
        omitting listening_start_time) makes TheirStack replay every
        historical match too — that's the 28,624-job backlog, don't do it
        without deliberately tranching it first.
        """
        existing = self.find_webhook(url)
        body: dict[str, Any] = {
            "url": url,
            "search_id": saved_search_id,
            "description": description,
            "secret": secret,
            "active_event_types": event_types,
            "trigger_once_per_company": False,
        }
        if listen_from_now:
            import datetime as _dt

            body["listening_start_time"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        if existing:
            resp = self._client.patch(f"/v0/webhooks/{existing['id']}", json=body)
        else:
            resp = self._client.post("/v0/webhooks", json=body)
        resp.raise_for_status()
        return resp.json()


def verify_webhook_signature(payload: bytes, secret: str, signature_header: str | None) -> bool:
    """Verify X-TheirStack-Signature-256. See docs/THEIRSTACK_API_REFERENCE.md.

    Must be checked against the raw request body bytes, not a re-serialized
    JSON object — re-serialization can change key order or whitespace and
    break the comparison even for a genuine payload.
    """
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
