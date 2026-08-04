"""FastAPI app: TheirStack webhook receiver, health check, and the dashboard
(Phase 4) — the real `postings`/`scores` data served in the exact shape
prototype/sightline-dashboard.html expects."""
from __future__ import annotations

import json
import logging
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from sightline.anthropic_client import AnthropicClient
from sightline.assembly import assemble, variant_detail
from sightline.config import Settings, get_settings
from sightline.dashboard import postings_to_dashboard_p, search_profiles_to_dashboard, settings_to_cfg_qv
from sightline.db import SightlineDB
from sightline.ingest import handle_webhook_event
from sightline.outreach import assemble_outreach
from sightline.provenance import ProvenanceError
from sightline.settings_service import preview_query, update_search_profile, update_settings
from sightline.theirstack import TheirStackClient, build_filters_for_profile, verify_webhook_signature

logger = logging.getLogger("sightline.web")

app = FastAPI(title="Sightline")

_basic_auth = HTTPBasic()


def require_auth(
    credentials: HTTPBasicCredentials = Depends(_basic_auth),
    settings: Settings = Depends(get_settings),
) -> None:
    """The dashboard exposes real company names, job descriptions, and comp
    data on a public URL — this is not optional. Timing-safe comparisons per
    FastAPI's own recommended pattern for HTTP Basic Auth."""
    user_ok = secrets.compare_digest(credentials.username, settings.dashboard_username)
    pass_ok = secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})


@lru_cache
def _db_singleton(settings: Settings) -> SightlineDB:
    return SightlineDB(settings)


def get_db(settings: Settings = Depends(get_settings)) -> SightlineDB:
    return _db_singleton(settings)


@lru_cache
def _anthropic_singleton(settings: Settings) -> AnthropicClient:
    return AnthropicClient(api_key=settings.anthropic_api_key)


def get_anthropic(settings: Settings = Depends(get_settings)) -> AnthropicClient:
    return _anthropic_singleton(settings)


@lru_cache
def _theirstack_singleton(settings: Settings) -> TheirStackClient:
    return TheirStackClient(api_key=settings.theirstack_api_key)


def get_theirstack(settings: Settings = Depends(get_settings)) -> TheirStackClient:
    return _theirstack_singleton(settings)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


_DASHBOARD_HTML = Path(__file__).parent / "static" / "dashboard.html"


@app.get("/", dependencies=[Depends(require_auth)])
def dashboard() -> FileResponse:
    return FileResponse(_DASHBOARD_HTML)


@app.get("/api/postings", dependencies=[Depends(require_auth)])
def api_postings(db: SightlineDB = Depends(get_db)) -> list[dict]:
    settings = db.get_settings()
    rows = db.list_scored_postings()
    return postings_to_dashboard_p(rows, score_threshold=settings.get("score_threshold", 70))


@app.get("/api/settings", dependencies=[Depends(require_auth)])
def api_settings(db: SightlineDB = Depends(get_db)) -> dict:
    raw = db.get_settings()
    profiles = db.get_search_profiles()
    return {"raw": raw, "profiles": search_profiles_to_dashboard(profiles), **settings_to_cfg_qv(raw)}


@app.patch("/api/settings", dependencies=[Depends(require_auth)])
def api_settings_patch(
    fields: dict[str, Any],
    db: SightlineDB = Depends(get_db),
    theirstack: TheirStackClient = Depends(get_theirstack),
) -> dict:
    """Persists shared fetch/queue criteria to `settings`, and — for fields
    that affect what TheirStack sends us — re-syncs both search profiles'
    saved searches. See sightline/settings_service.py. Title lists live on
    search_profiles now; use PATCH /api/search-profiles/{id} for those."""
    updated = update_settings(db, theirstack, fields)
    profiles = db.get_search_profiles()
    return {"raw": updated, "profiles": search_profiles_to_dashboard(profiles), **settings_to_cfg_qv(updated)}


@app.patch("/api/search-profiles/{profile_id}", dependencies=[Depends(require_auth)])
def api_search_profile_patch(
    profile_id: str,
    fields: dict[str, Any],
    db: SightlineDB = Depends(get_db),
    theirstack: TheirStackClient = Depends(get_theirstack),
) -> dict:
    """Persists one profile's title lists (or budget share), and re-syncs
    that profile's saved search if the title lists changed."""
    try:
        updated = update_search_profile(db, theirstack, profile_id, fields)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return search_profiles_to_dashboard([updated])[0]


@app.post("/api/preview", dependencies=[Depends(require_auth)])
def api_preview(
    body: dict[str, Any],
    db: SightlineDB = Depends(get_db),
    theirstack: TheirStackClient = Depends(get_theirstack),
) -> dict:
    """Real TheirStack free-count/preview numbers for the form's current
    (possibly unsaved) values — 0 credits, safe to call on every keystroke's
    worth of tuning. `body.profile_id` selects which profile's title lists
    to preview; any other fields merge as overrides over the saved profile
    and settings rows so Preview reflects what's in the form, not just
    what's already saved."""
    profile_id = body.get("profile_id", "automation")
    stored_profiles = {p["id"]: p for p in db.get_search_profiles()}
    if profile_id not in stored_profiles:
        raise HTTPException(status_code=400, detail=f"unknown profile_id {profile_id!r}")
    profile = {**stored_profiles[profile_id], **body}
    settings = {**db.get_settings(), **body}
    filters = build_filters_for_profile(profile, settings)
    return preview_query(theirstack, filters)


@app.get("/api/credits", dependencies=[Depends(require_auth)])
def api_credits(
    db: SightlineDB = Depends(get_db), theirstack: TheirStackClient = Depends(get_theirstack)
) -> dict:
    settings = db.get_settings()
    balance = theirstack.credit_balance()
    return {
        "used_api_credits": balance["used_api_credits"],
        "api_credits": balance["api_credits"],
        "monthly_credits": settings.get("monthly_credits", 200),
        "per_run_cap": settings.get("per_run_cap", 120),
    }


@app.post("/api/postings/{posting_id}/assemble", dependencies=[Depends(require_auth)])
def api_assemble(
    posting_id: int,
    body: dict[str, Any],
    db: SightlineDB = Depends(get_db),
    anthropic: AnthropicClient = Depends(get_anthropic),
) -> dict:
    """Stage 4: selects bullets, gates on provenance, renders and uploads
    the .docx, generates the brief, and records the variant. `body.variant`
    optionally overrides the score's suggested_variant ('engineer' or
    'leadership')."""
    try:
        return assemble(db, anthropic, posting_id, variant=body.get("variant"))
    except ProvenanceError as e:
        # Not a bypass — the validator already ran and refused. This just
        # turns that refusal into a response instead of a 500.
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/postings/{posting_id}/variant", dependencies=[Depends(require_auth)])
def api_variant_detail(posting_id: int, db: SightlineDB = Depends(get_db)) -> dict:
    """Read-only — restores the diff view / fresh download link for an
    already-assembled posting after a page reload, without re-running Sonnet
    or re-uploading a new document."""
    try:
        return variant_detail(db, posting_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/postings/{posting_id}/outreach", dependencies=[Depends(require_auth)])
def api_outreach_generate(
    posting_id: int,
    body: dict[str, Any],
    db: SightlineDB = Depends(get_db),
    anthropic: AnthropicClient = Depends(get_anthropic),
) -> dict:
    try:
        return assemble_outreach(
            db, anthropic, posting_id,
            target_name=body.get("target_name") or "",
            target_title=body.get("target_title"),
            target_linkedin_url=body.get("target_linkedin_url"),
        )
    except ProvenanceError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/postings/{posting_id}/outreach/sent", dependencies=[Depends(require_auth)])
def api_outreach_sent(
    posting_id: int, body: dict[str, Any], db: SightlineDB = Depends(get_db)
) -> dict:
    try:
        return db.mark_outreach_sent(posting_id, body.get("channel") or "linkedin_message")
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/webhooks/theirstack")
async def theirstack_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: SightlineDB = Depends(get_db),
    anthropic: AnthropicClient = Depends(get_anthropic),
    theirstack: TheirStackClient = Depends(get_theirstack),
) -> Response:
    if not settings.theirstack_webhook_secret:
        # Fail closed: an unauthenticated public endpoint that writes straight
        # to the DB is a real risk, not a convenience to skip in dev.
        logger.error("THEIRSTACK_WEBHOOK_SECRET is not configured")
        return Response(status_code=500, content="webhook secret not configured")

    body = await request.body()
    signature = request.headers.get("x-theirstack-signature-256")
    if not verify_webhook_signature(body, settings.theirstack_webhook_secret, signature):
        db.log_event(entity_type="webhook", event="signature_rejected", payload={})
        return Response(status_code=403, content="invalid signature")

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=400, content="invalid JSON")

    # Handle at least 2 concurrent requests, return 2xx promptly, and treat
    # duplicate deliveries as harmless (upsert-on-external_id is idempotent) —
    # all per docs/THEIRSTACK_API_REFERENCE.md §8's delivery requirements.
    try:
        result = handle_webhook_event(db, anthropic, theirstack, event)
    except Exception:
        logger.exception(
            "failed to process webhook event id=%s type=%s", event.get("id"), event.get("type")
        )
        # Non-2xx on purpose: TheirStack retries hourly for 48h on anything
        # that isn't 2xx, which is the safety net for a transient failure
        # (e.g. Supabase hiccup). Returning 2xx here would tell TheirStack the
        # event was handled when it wasn't, and it would never come back.
        return Response(status_code=500, content="processing failed")

    return Response(status_code=200, content=json.dumps(result), media_type="application/json")
