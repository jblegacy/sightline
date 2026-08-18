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

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from sightline.answers import chat_reply, next_ref, slugify_question
from sightline.anthropic_client import AnthropicClient
from sightline.assembly import assemble, variant_detail
from sightline.auth import SESSION_COOKIE, SESSION_MAX_AGE_SECONDS, make_session_token, verify_session_token
from sightline.budget import (
    force_reset_daily_baseline,
    maybe_reset_daily_breaker,
    profile_paused,
    set_profile_paused,
    used_today,
)
from sightline.config import Settings, get_settings
from sightline.cover_letter import (
    STYLE_DESCRIPTIONS,
    STYLE_LABELS,
    generate_cover_letter,
    generate_cover_letter_variants,
    greeting_for,
    render_cover_letter_docx,
)
from sightline.dashboard import (
    archived_postings_to_dashboard,
    postings_to_dashboard_p,
    search_profiles_to_dashboard,
    settings_to_cfg_qv,
)
from sightline.db import SightlineDB
from sightline.ingest import handle_manual_add, handle_webhook_event
from sightline.metrics import compute_metrics
from sightline.outreach import assemble_outreach
from sightline.posting_parser import RobotsDisallowedError, parse_posting_url
from sightline.provenance import ProvenanceError
from sightline.settings_service import preview_query, update_search_profile, update_settings
from sightline.theirstack import TheirStackClient, build_filters_for_profile, verify_webhook_signature

logger = logging.getLogger("sightline.web")

app = FastAPI(title="Sightline")


def require_auth(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """The dashboard exposes real company names, job descriptions, and comp
    data on a public URL — this is not optional. Session cookie set by
    POST /login after verifying credentials; see sightline/auth.py. Replaced
    HTTP Basic Auth's native browser popup with a real login page."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not verify_session_token(token, settings):
        raise HTTPException(status_code=401, detail="not authenticated")


def _request_is_https(request: Request) -> bool:
    """Railway (and most reverse proxies) terminate TLS upstream and forward
    plain HTTP internally — request.url.scheme reports "http" in production
    even though the browser is genuinely on an https:// page. Trust
    X-Forwarded-Proto when present, matching what the proxy actually saw."""
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


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
_LOGIN_HTML = Path(__file__).parent / "static" / "login.html"


@app.get("/")
def dashboard(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    """Not gated by Depends(require_auth) — an unauthenticated visit here
    should land on the login page, not a bare 401 JSON error. The API
    routes still 401 directly; this is the one HTML entry point."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not verify_session_token(token, settings):
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(_DASHBOARD_HTML)


@app.get("/login")
def login_page() -> HTMLResponse:
    return HTMLResponse(_LOGIN_HTML.read_text().replace("{ERROR_BLOCK}", ""))


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    settings: Settings = Depends(get_settings),
) -> Response:
    user_ok = secrets.compare_digest(username, settings.dashboard_username)
    pass_ok = secrets.compare_digest(password, settings.dashboard_password)
    if not (user_ok and pass_ok):
        html = _LOGIN_HTML.read_text().replace(
            "{ERROR_BLOCK}", '<p class="err">Incorrect username or password.</p>'
        )
        return HTMLResponse(html, status_code=401)

    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, make_session_token(settings),
        max_age=SESSION_MAX_AGE_SECONDS, httponly=True, samesite="lax",
        secure=_request_is_https(request),
    )
    return resp


@app.post("/logout")
def logout() -> Response:
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/postings", dependencies=[Depends(require_auth)])
def api_postings(db: SightlineDB = Depends(get_db)) -> list[dict]:
    settings = db.get_settings()
    rows = db.list_scored_postings()
    return postings_to_dashboard_p(rows, score_threshold=settings.get("score_threshold", 70))


@app.post("/api/postings/parse-url", dependencies=[Depends(require_auth)])
def api_parse_posting_url(body: dict[str, Any], anthropic: AnthropicClient = Depends(get_anthropic)) -> dict:
    """Fetches a job posting URL and extracts title/company/location/remote/
    jd_text with one Haiku call, so the manual-add form can be filled in
    from a pasted link instead of by hand. See sightline/posting_parser.py —
    respects robots.txt, sends an identifying User-Agent, and is a single
    on-demand fetch of a URL the candidate already chose, not a scraper."""
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    try:
        fields, cost_usd = parse_posting_url(anthropic, url)
    except RobotsDisallowedError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"couldn't fetch that URL: {e}") from e
    return {**fields, "cost_usd": round(cost_usd, 5)}


@app.post("/api/postings/manual", dependencies=[Depends(require_auth)])
def api_postings_manual(
    fields: dict[str, Any],
    db: SightlineDB = Depends(get_db),
    anthropic: AnthropicClient = Depends(get_anthropic),
    theirstack: TheirStackClient = Depends(get_theirstack),
) -> dict:
    """A job the candidate found themselves — pasted in by hand, 0
    TheirStack credits, scored through the exact same pipeline as a real
    webhook delivery. See sightline/ingest.py handle_manual_add — this also
    learns the title into a profile's title_include when nothing already
    would have caught it, so the search widens from what manual adds find."""
    try:
        profiles = db.get_search_profiles()
        settings = db.get_settings()
        posting = handle_manual_add(db, anthropic, theirstack, settings, profiles, fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        # Anything past validation (a Supabase hiccup, a scoring-call
        # failure, a duplicate URL constraint) used to surface to the
        # browser as a bare 500 with no body — the user had nothing to
        # copy/paste back for debugging. Log the real error both places
        # (events, for the pipeline's own trail; logger, for Railway) and
        # hand the actual message to the client instead of swallowing it.
        logger.exception("manual add failed for title=%r url=%r", fields.get("title"), fields.get("url"))
        try:
            db.log_event(
                entity_type="posting", event="manual_add_failed",
                payload={"title": fields.get("title"), "url": fields.get("url"), "error": str(e)},
            )
        except Exception:
            pass  # DB may be the reason the add failed in the first place — don't mask the real error
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
    return {"id": posting["id"], "status": posting["status"]}


@app.get("/api/postings/archived", dependencies=[Depends(require_auth)])
def api_postings_archived(db: SightlineDB = Depends(get_db)) -> list[dict]:
    """status='archived' (filtered out or below queue_min_score) and
    status='expired' (job.closed, or the dashboard's own Archive button) —
    queue-level Rejects aren't here, see /api/postings' stage='rejected'."""
    rows = db.list_archived_postings()
    return archived_postings_to_dashboard(rows)


@app.post("/api/postings/{posting_id}/restore", dependencies=[Depends(require_auth)])
def api_restore_posting(posting_id: int, db: SightlineDB = Depends(get_db)) -> dict:
    """Undo for an archive/expire — puts the posting back to status='scored'
    so it reappears in Queue or Watchlist on its existing score. A posting
    the deterministic filter archived before it was ever scored has no score
    to restore to; the dashboard doesn't offer Restore for those (see
    canRestore in archived_posting_to_row), and this rejects it too rather
    than silently producing a 'scored' row that renders as nothing."""
    try:
        posting = db.get_posting(posting_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if not posting.get("scores"):
        raise HTTPException(
            status_code=400,
            detail="No score on file for this posting — re-add it from Queue → Add job manually instead.",
        )
    db.restore_posting_status(posting_id)
    db.log_event(entity_type="posting", entity_id=posting_id, event="restored_by_user")
    return {"ok": True}


@app.get("/api/settings", dependencies=[Depends(require_auth)])
def api_settings(
    db: SightlineDB = Depends(get_db), theirstack: TheirStackClient = Depends(get_theirstack)
) -> dict:
    raw = db.get_settings()
    profiles = db.get_search_profiles()
    dashboard_profiles = search_profiles_to_dashboard(profiles)
    for p in dashboard_profiles:
        p["paused"] = profile_paused(theirstack, p["id"])
    return {"raw": raw, "profiles": dashboard_profiles, **settings_to_cfg_qv(raw)}


@app.post("/api/search-profiles/{profile_id}/pause", dependencies=[Depends(require_auth)])
def api_search_profile_pause(
    profile_id: str, body: dict[str, Any], theirstack: TheirStackClient = Depends(get_theirstack)
) -> dict:
    """Stops (or resumes) one profile's daily webhook-driven ingestion
    without touching the other — a deliberate dashboard action, not a
    credit-threshold trip. See sightline/budget.py set_profile_paused."""
    paused = bool(body.get("paused"))
    found = set_profile_paused(theirstack, profile_id, paused)
    if not found:
        raise HTTPException(
            status_code=404, detail=f"no TheirStack saved search found for profile {profile_id!r}"
        )
    return {"ok": True, "profile_id": profile_id, "paused": paused}


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
    # Lazy daily-breaker reset — see sightline/budget.py. No scheduled
    # worker exists to fire this on a clock, so it piggybacks on the one
    # endpoint every dashboard load already hits.
    reset = maybe_reset_daily_breaker(theirstack, db)
    settings = db.get_settings()
    balance = theirstack.credit_balance()
    return {
        "used_api_credits": balance["used_api_credits"],
        "api_credits": balance["api_credits"],
        "monthly_credits": settings.get("monthly_credits", 200),
        "daily_credit_cap": settings.get("daily_credit_cap"),
        "used_today": used_today(theirstack, db, settings),
        "daily_breaker_reset": reset["reset"],
    }


@app.post("/api/credits/reset-daily", dependencies=[Depends(require_auth)])
def api_credits_reset_daily(
    db: SightlineDB = Depends(get_db), theirstack: TheirStackClient = Depends(get_theirstack)
) -> dict:
    """Manual, same-day version of the lazy midnight reset above — for when
    the daily cap tripped on a real overage that's since been fixed, and
    waiting for UTC midnight isn't the point. Only clears the spend
    baseline; each profile's paused/active state is untouched, so a
    profile paused on purpose (not by the breaker) stays paused. See
    sightline/budget.py force_reset_daily_baseline."""
    return force_reset_daily_baseline(theirstack, db)


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


def _cover_letter_context(posting_id: int, db: SightlineDB) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        posting = db.get_posting(posting_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    variants = posting.get("variants") or []
    if not variants:
        raise HTTPException(
            status_code=400, detail="build a resume first — the cover letter echoes its bullet selection"
        )
    scores = posting.get("scores") or []
    if not scores:
        raise HTTPException(status_code=404, detail=f"posting {posting_id} has not been scored yet")
    return posting, variants[0], scores[0]


def _save_cover_letter(
    db: SightlineDB, posting_id: int, posting: dict[str, Any], variant_row: dict[str, Any],
    score: dict[str, Any], text: str, cost_usd: float,
    feedback_note: str | None = None, previous_text: str | None = None,
) -> dict[str, Any]:
    company = (posting.get("companies") or {}).get("name", "Unknown")
    docx_bytes = render_cover_letter_docx(text, company, posting["title"], greeting_for(score))
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
    # Not applied to anything automatically — the calibration loop is a
    # human reviewing these later, same as reject-reasons and score
    # overrides. Kept as its own event so it's easy to query separately
    # from every plain regenerate, which fires cover_letter_generated too.
    if feedback_note and feedback_note.strip():
        db.log_event(
            entity_type="variant", entity_id=variant_row["id"], event="cover_letter_feedback",
            payload={
                "posting_id": posting_id, "note": feedback_note.strip(),
                "edited_text": text, "previous_text": previous_text,
            },
        )
    return {**updated, "signed_url": signed_url}


@app.post("/api/postings/{posting_id}/cover-letter/preview", dependencies=[Depends(require_auth)])
def api_cover_letter_preview(
    posting_id: int,
    db: SightlineDB = Depends(get_db),
    anthropic: AnthropicClient = Depends(get_anthropic),
) -> dict:
    """The sandbox: generates all three structural styles from the same
    grounding data in one call — text only, nothing rendered or saved.
    POST /cover-letter with body.text set to whichever draft is picked to
    actually save one. See sightline/cover_letter.py STYLE_STRUCTURES."""
    posting, variant_row, score = _cover_letter_context(posting_id, db)
    bullets = db.get_bullets_full()
    answers = db.get_answers()
    try:
        results = generate_cover_letter_variants(
            anthropic, posting, score, bullets, variant_row.get("bullet_refs") or [], answers
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    total_cost = sum(cost for _, cost in results.values())
    db.log_event(
        entity_type="variant", entity_id=variant_row["id"], event="cover_letter_preview_generated",
        payload={"posting_id": posting_id, "styles": list(results.keys()), "cost_usd": round(total_cost, 5)},
    )
    return {
        "variants": [
            {
                "style": style, "label": STYLE_LABELS[style], "description": STYLE_DESCRIPTIONS[style],
                "text": text, "words": len(text.split()),
            }
            for style, (text, _cost) in results.items()
        ],
        "cost_usd": round(total_cost, 5),
    }


@app.post("/api/postings/{posting_id}/cover-letter", dependencies=[Depends(require_auth)])
def api_cover_letter(
    posting_id: int,
    body: dict[str, Any],
    db: SightlineDB = Depends(get_db),
    anthropic: AnthropicClient = Depends(get_anthropic),
) -> dict:
    """Generates (or regenerates) and saves a cover letter echoing the same
    bullets already selected for this posting's resume — requires a built
    variant. Pass body.text (a draft already picked from /preview, or a
    hand edit of one already saved) to save it directly without another
    model call; otherwise generates fresh with body.style (default "warm").
    body.feedback_note, if present, is logged as a distinct
    cover_letter_feedback event — a durable calibration trail rather than
    a chat aside that evaporates. Grounded only in verified bullets; see
    sightline/cover_letter.py."""
    posting, variant_row, score = _cover_letter_context(posting_id, db)

    text = (body.get("text") or "").strip()
    if text:
        if len(text) < 50:
            raise HTTPException(status_code=400, detail=f"cover letter text too short ({len(text)} chars)")
        cost_usd = 0.0
    else:
        bullets = db.get_bullets_full()
        answers = db.get_answers()
        style = body.get("style") or "warm"
        try:
            text, cost_usd = generate_cover_letter(
                anthropic, posting, score, bullets, variant_row.get("bullet_refs") or [], answers, style=style
            )
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    return _save_cover_letter(
        db, posting_id, posting, variant_row, score, text, cost_usd,
        feedback_note=body.get("feedback_note"), previous_text=body.get("previous_text"),
    )


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


@app.post("/api/postings/{posting_id}/archive", dependencies=[Depends(require_auth)])
def api_archive_posting(posting_id: int, db: SightlineDB = Depends(get_db)) -> dict:
    """Manual, credit-free alternative to waiting for TheirStack's
    job.closed webhook (which costs 1 credit same as job.new) — a plain
    judgment-call button, not an auto-dead-listing detector. Same effect
    as a real closure: status='expired', drops out of every queue/
    watchlist view, data kept, not deleted."""
    db.archive_posting_by_id(posting_id, reason="archived by user")
    db.log_event(entity_type="posting", entity_id=posting_id, event="archived_by_user")
    return {"ok": True}


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

    # The literal question just asked — the most recent user turn — feeds a
    # deterministic slug suggestion so saving an answer doesn't require
    # inventing a question_type by hand. No model call needed for this part.
    last_question = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    existing_types = [a["question_type"] for a in answers]
    suggested_question_type = slugify_question(last_question, existing_types) if last_question else None

    return {
        "reply": reply, "cost_usd": round(cost_usd, 5),
        "suggested_question_type": suggested_question_type,
    }


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
        "question_text": body.get("question_text"),
        "posting_id": body.get("posting_id"),
        "text": text,
        "tags": body.get("tags") or [],
        "status": body.get("status") or "ready",
    })
    db.log_event(entity_type="answer", event="saved", payload={"ref": ref, "question_type": question_type})
    return saved


@app.post("/api/answers/{ref}/mark-used", dependencies=[Depends(require_auth)])
def api_answers_mark_used(ref: str, db: SightlineDB = Depends(get_db)) -> dict:
    """Reusing an existing saved answer for a new application, as-is or
    lightly adapted, without changing its saved content — see
    db.mark_answer_used. Distinct from /api/answers/save, which is for when
    the text itself changed."""
    try:
        updated = db.mark_answer_used(ref)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    db.log_event(entity_type="answer", event="reused", payload={"ref": ref})
    return updated


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
    #
    # This is the only async route in the app, and handle_webhook_event is
    # fully synchronous (SightlineDB/AnthropicClient both use blocking
    # httpx.Client) — a job.new event's scoring call alone can run several
    # seconds. Called directly, that blocks this process's single asyncio
    # event loop for the whole app, not just this request: found live —
    # every other route, including /health with zero dependencies, stalled
    # or timed out during scoring, and TheirStack's own webhook client gave
    # up mid-delivery (ClientDisconnect on request.body()) waiting for the
    # loop to free up enough to even read the next request. run_in_threadpool
    # moves the blocking work off the event loop, matching what every other
    # route already gets for free by being a plain `def` (FastAPI dispatches
    # those to a worker thread automatically; this one can't be sync because
    # it needs `await request.body()`).
    try:
        result = await run_in_threadpool(handle_webhook_event, db, anthropic, theirstack, event)
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
