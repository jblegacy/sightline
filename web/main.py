"""FastAPI app: TheirStack webhook receiver, health check, and the dashboard
(Phase 4) — the real `postings`/`scores` data served in the exact shape
prototype/sightline-dashboard.html expects."""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from sightline.answers import chat_reply, next_ref
from sightline.anthropic_client import AnthropicClient
from sightline.assembly import assemble, variant_detail
from sightline.budget import used_today
from sightline.config import Settings, get_settings
from sightline.cover_letter import generate_cover_letter, render_cover_letter_docx
from sightline.dashboard import postings_to_dashboard_p, search_profiles_to_dashboard, settings_to_cfg_qv
from sightline.db import SightlineDB
from sightline.ingest import handle_webhook_event
from sightline.metrics import compute_metrics
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
        "daily_credit_cap": settings.get("daily_credit_cap"),
        "used_today": used_today(theirstack, db, settings),
    }


@app.get("/api/metrics", dependencies=[Depends(require_auth)])
def api_metrics(db: SightlineDB = Depends(get_db)) -> dict:
    return compute_metrics(db, db.get_settings())


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


@app.post("/api/postings/{posting_id}/cover-letter", dependencies=[Depends(require_auth)])
def api_cover_letter(
    posting_id: int,
    db: SightlineDB = Depends(get_db),
    anthropic: AnthropicClient = Depends(get_anthropic),
) -> dict:
    """Generates (or regenerates) a cover letter echoing the same bullets
    already selected for this posting's resume — requires a built variant.
    Grounded only in verified bullets; see sightline/cover_letter.py."""
    try:
        posting = db.get_posting(posting_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    variants = posting.get("variants") or []
    if not variants:
        raise HTTPException(
            status_code=400, detail="build a resume first — the cover letter echoes its bullet selection"
        )
    variant_row = variants[0]
    scores = posting.get("scores") or []
    if not scores:
        raise HTTPException(status_code=404, detail=f"posting {posting_id} has not been scored yet")
    score = scores[0]

    bullets = db.get_bullets_full()
    text, cost_usd = generate_cover_letter(
        anthropic, posting, score, bullets, variant_row.get("bullet_refs") or []
    )

    company = (posting.get("companies") or {}).get("name", "Unknown")
    docx_bytes = render_cover_letter_docx(text, company, posting["title"])
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = f"{posting_id}/cover-letter-{variant_row['kind']}-{timestamp}.docx"
    db.upload_document("resumes", path, docx_bytes)

    updated = db.update_variant(variant_row["id"], {
        "cover_letter_text": text, "cover_letter_storage_path": path,
    })
    signed_url = db.create_signed_url("resumes", path)
    db.log_event(
        entity_type="variant", entity_id=variant_row["id"], event="cover_letter_generated",
        payload={"posting_id": posting_id, "cost_usd": round(cost_usd, 5)},
    )
    return {**updated, "signed_url": signed_url}


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


@app.patch("/api/postings/{posting_id}/application", dependencies=[Depends(require_auth)])
def api_application_patch(
    posting_id: int, fields: dict[str, Any], db: SightlineDB = Depends(get_db)
) -> dict:
    """Defer, Reject, Mark submitted, the status dropdown, notes, and
    Record final on the dashboard all funnel through here — one upsert per
    posting. status='rejected'/'deferred' also logs an event: CLAUDE.md
    calls a reject "rubric training data," and this is where that reason
    actually gets captured instead of only appearing in a toast."""
    updated = db.upsert_application({**fields, "posting_id": posting_id})
    if fields.get("status") in ("rejected", "deferred"):
        db.log_event(
            entity_type="posting", entity_id=posting_id, event=f"{fields['status']}_by_user",
            payload={"reason": fields.get("notes")},
        )
    return updated


@app.patch("/api/postings/{posting_id}/score-override", dependencies=[Depends(require_auth)])
def api_score_override(
    posting_id: int, body: dict[str, Any], db: SightlineDB = Depends(get_db)
) -> dict:
    """Manual score correction — separate from Approve/Reject/Defer, which
    are application state, not scoring accuracy. Moves the posting between
    queue/watch immediately (dashboard.py uses the override as the posting's
    effective score); the reason is calibration data toward a future
    rubric_version revision, not an automatic rubric change. Pass
    {"clear": true} to remove a previously-set override."""
    try:
        posting = db.get_posting(posting_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    scores = posting.get("scores") or []
    if not scores:
        raise HTTPException(status_code=404, detail=f"posting {posting_id} has not been scored yet")
    score = scores[0]

    if body.get("clear"):
        total, reason = None, None
    else:
        total = body.get("total")
        reason = body.get("reason")
        if total is None or not reason:
            raise HTTPException(status_code=400, detail="total and reason are both required to set an override")

    updated = db.override_score(score["id"], total, reason)
    db.log_event(
        entity_type="score", entity_id=score["id"], event="human_override",
        payload={"posting_id": posting_id, "old_total": score["total"], "new_total": total, "reason": reason},
    )
    return updated


@app.get("/api/answers", dependencies=[Depends(require_auth)])
def api_answers(db: SightlineDB = Depends(get_db)) -> list[dict]:
    return db.get_answers()


@app.post("/api/answers/chat", dependencies=[Depends(require_auth)])
def api_answers_chat(
    body: dict[str, Any],
    db: SightlineDB = Depends(get_db),
    anthropic: AnthropicClient = Depends(get_anthropic),
) -> dict:
    """One turn of the answer workbench chat. Stateless server-side — the
    caller resends the full message history each turn; nothing about the
    conversation itself is persisted, only whatever gets explicitly saved
    via /api/answers/save."""
    messages = body.get("messages")
    if not messages:
        raise HTTPException(status_code=400, detail="messages is required")

    posting = None
    posting_id = body.get("posting_id")
    if posting_id:
        try:
            posting = db.get_posting(posting_id)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    bullets = db.get_bullets_full()
    answers = db.get_answers()
    reply, cost_usd = chat_reply(anthropic, bullets, answers, posting, messages)
    return {"reply": reply, "cost_usd": round(cost_usd, 5)}


@app.post("/api/answers/save", dependencies=[Depends(require_auth)])
def api_answers_save(body: dict[str, Any], db: SightlineDB = Depends(get_db)) -> dict:
    text = body.get("text")
    question_type = body.get("question_type")
    if not text or not question_type:
        raise HTTPException(status_code=400, detail="text and question_type are both required")

    ref = body.get("ref")
    if not ref:
        ref = next_ref(db.get_answers())

    saved = db.upsert_answer({
        "ref": ref,
        "question_type": question_type,
        "text": text,
        "tags": body.get("tags") or [],
        "status": body.get("status") or "ready",
    })
    db.log_event(entity_type="answer", event="saved", payload={"ref": ref, "question_type": question_type})
    return saved


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
