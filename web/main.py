"""FastAPI app. Currently just the TheirStack webhook receiver + a health
check; the HTMX dashboard (Phase 4) lands on top of this later."""
from __future__ import annotations

import json
import logging
from functools import lru_cache

from fastapi import Depends, FastAPI, Request, Response

from sightline.config import Settings, get_settings
from sightline.db import SightlineDB
from sightline.ingest import handle_webhook_event
from sightline.theirstack import verify_webhook_signature

logger = logging.getLogger("sightline.web")

app = FastAPI(title="Sightline")


@lru_cache
def _db_singleton(settings: Settings) -> SightlineDB:
    return SightlineDB(settings)


def get_db(settings: Settings = Depends(get_settings)) -> SightlineDB:
    return _db_singleton(settings)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/theirstack")
async def theirstack_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: SightlineDB = Depends(get_db),
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
        result = handle_webhook_event(db, event)
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
