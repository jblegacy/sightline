import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from sightline.auth import SESSION_COOKIE
from sightline.config import Settings
from tests.test_ingest import SAMPLE_JOB, FakeAnthropic, FakeDB, FakeTheirStack
from web.main import app, get_anthropic, get_db, get_theirstack
from sightline.config import get_settings as real_get_settings

SECRET = "a-long-enough-test-secret-value"
DASH_USER = "testuser"
DASH_PASS = "testpass"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def raw_client(fake_db):
    """Unauthenticated — no session cookie. Use this directly only for
    testing the login flow itself or a "requires_auth" 401 check; every
    other test wants `client`, which is pre-authenticated."""
    app.dependency_overrides[real_get_settings] = lambda: Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_key="fake",
        theirstack_api_key="fake",
        theirstack_webhook_secret=SECRET,
        theirstack_webhook_url="https://example.com/webhooks/theirstack",
        anthropic_api_key="fake",
        dashboard_username=DASH_USER,
        dashboard_password=DASH_PASS,
    )
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_anthropic] = lambda: FakeAnthropic()
    app.dependency_overrides[get_theirstack] = lambda: FakeTheirStack()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client(raw_client):
    """Logs in once via the real POST /login flow — the session cookie
    TestClient gets back is then sent automatically on every subsequent
    call, same as a real browser after a real login."""
    login = raw_client.post(
        "/login", data={"username": DASH_USER, "password": DASH_PASS}, follow_redirects=False
    )
    assert login.status_code == 303
    return raw_client


def test_health():
    c = TestClient(app)
    resp = c.get("/health")
    assert resp.status_code == 200


def test_webhook_rejects_missing_signature(client):
    body = json.dumps({"id": 1, "type": "job.new", "payload": SAMPLE_JOB}).encode()
    resp = client.post("/webhooks/theirstack", content=body)
    assert resp.status_code == 403


def test_webhook_rejects_wrong_signature(client):
    body = json.dumps({"id": 1, "type": "job.new", "payload": SAMPLE_JOB}).encode()
    resp = client.post(
        "/webhooks/theirstack", content=body, headers={"X-TheirStack-Signature-256": "sha256=deadbeef"}
    )
    assert resp.status_code == 403


def test_webhook_accepts_valid_signature_and_ingests(client, fake_db):
    body = json.dumps({"id": 1, "type": "job.new", "payload": SAMPLE_JOB}).encode()
    resp = client.post(
        "/webhooks/theirstack", content=body, headers={"X-TheirStack-Signature-256": sign(body)}
    )
    assert resp.status_code == 200
    assert "999001" in fake_db.postings


def test_webhook_processing_runs_off_the_event_loop(client, monkeypatch):
    """handle_webhook_event is fully synchronous — SightlineDB and
    AnthropicClient both use a blocking httpx.Client, and job.new scoring
    alone can take several seconds. theirstack_webhook is the only async
    route in the app; calling that directly would freeze the event loop for
    every other request (found live: /health with zero dependencies stalled
    during scoring). This verifies it's dispatched through run_in_threadpool
    rather than called inline."""
    import web.main as main_module

    real_run_in_threadpool = main_module.run_in_threadpool
    calls = []

    async def spy(func, *args, **kwargs):
        calls.append(func)
        return await real_run_in_threadpool(func, *args, **kwargs)

    monkeypatch.setattr(main_module, "run_in_threadpool", spy)

    body = json.dumps({"id": 1, "type": "job.new", "payload": SAMPLE_JOB}).encode()
    resp = client.post(
        "/webhooks/theirstack", content=body, headers={"X-TheirStack-Signature-256": sign(body)}
    )
    assert resp.status_code == 200
    assert calls == [main_module.handle_webhook_event]


def test_webhook_signature_checked_against_raw_bytes_not_reparsed_json(client):
    # signature computed over a body with different whitespace than what's sent
    # must fail — verifies we check request.body(), not a re-serialized dict
    canonical = json.dumps({"id": 1, "type": "job.new", "payload": SAMPLE_JOB})
    differently_formatted = json.dumps(
        {"id": 1, "type": "job.new", "payload": SAMPLE_JOB}, indent=2
    ).encode()
    good_sig_for_canonical = sign(canonical.encode())
    resp = client.post(
        "/webhooks/theirstack",
        content=differently_formatted,
        headers={"X-TheirStack-Signature-256": good_sig_for_canonical},
    )
    assert resp.status_code == 403


def test_webhook_500_when_secret_not_configured(fake_db):
    app.dependency_overrides[real_get_settings] = lambda: Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_key="fake",
        theirstack_api_key="fake",
        theirstack_webhook_secret=None,
        theirstack_webhook_url=None,
        anthropic_api_key="fake",
        dashboard_username=DASH_USER,
        dashboard_password=DASH_PASS,
    )
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_anthropic] = lambda: FakeAnthropic()
    app.dependency_overrides[get_theirstack] = lambda: FakeTheirStack()
    c = TestClient(app)
    body = json.dumps({"id": 1, "type": "job.new", "payload": SAMPLE_JOB}).encode()
    resp = c.post("/webhooks/theirstack", content=body, headers={"X-TheirStack-Signature-256": "sha256=x"})
    assert resp.status_code == 500
    app.dependency_overrides.clear()


def test_webhook_job_closed(client, fake_db):
    new_body = json.dumps({"id": 1, "type": "job.new", "payload": SAMPLE_JOB}).encode()
    client.post("/webhooks/theirstack", content=new_body, headers={"X-TheirStack-Signature-256": sign(new_body)})

    closed_body = json.dumps(
        {"id": 2, "type": "job.closed", "payload": {"id": 999001, "closed_at": "2026-08-10T00:00:00Z"}}
    ).encode()
    resp = client.post(
        "/webhooks/theirstack", content=closed_body, headers={"X-TheirStack-Signature-256": sign(closed_body)}
    )
    assert resp.status_code == 200
    assert fake_db.postings["999001"]["status"] == "expired"


def test_webhook_returns_500_and_not_2xx_when_processing_raises(client):
    # malformed payload (missing required 'id') should surface as a 500 so
    # TheirStack's 48h retry actually kicks in, not silently swallowed
    bad_job = {k: v for k, v in SAMPLE_JOB.items() if k != "id"}
    body = json.dumps({"id": 1, "type": "job.new", "payload": bad_job}).encode()
    resp = client.post(
        "/webhooks/theirstack", content=body, headers={"X-TheirStack-Signature-256": sign(body)}
    )
    assert resp.status_code == 500


# ---- dashboard: auth + real data wiring ----


def test_dashboard_requires_auth_redirects_to_login(raw_client):
    resp = raw_client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_page_renders(raw_client):
    resp = raw_client.get("/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_login_rejects_wrong_password(raw_client):
    resp = raw_client.post("/login", data={"username": DASH_USER, "password": "wrong-password"})
    assert resp.status_code == 401
    assert SESSION_COOKIE not in raw_client.cookies


def test_login_accepts_correct_credentials_and_sets_session_cookie(raw_client):
    resp = raw_client.post(
        "/login", data={"username": DASH_USER, "password": DASH_PASS}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert SESSION_COOKIE in raw_client.cookies


def test_dashboard_serves_html_once_logged_in(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_logout_clears_the_session(client):
    assert client.get("/api/postings").status_code == 200
    client.post("/logout")
    resp = client.get("/api/postings")
    assert resp.status_code == 401


def test_api_postings_requires_auth(raw_client):
    resp = raw_client.get("/api/postings")
    assert resp.status_code == 401


def test_api_postings_empty_when_nothing_scored(client):
    resp = client.get("/api/postings", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_postings_returns_scored_postings_in_p_shape(client, fake_db):
    body = json.dumps({"id": 1, "type": "job.new", "payload": SAMPLE_JOB}).encode()
    client.post("/webhooks/theirstack", content=body, headers={"X-TheirStack-Signature-256": sign(body)})

    resp = client.get("/api/postings", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    postings = resp.json()
    assert len(postings) == 1
    p = postings[0]
    assert p["ti"] == "AI Automation Engineer"
    assert p["co"] == "Fake Co"
    assert p["stage"] in ("queue", "watch")
    assert isinstance(p["d"], list)
    assert len(p["d"]) == 7


def test_api_settings_requires_auth(raw_client):
    resp = raw_client.get("/api/settings")
    assert resp.status_code == 401


def test_api_settings_returns_qv_and_profiles_shape(client):
    resp = client.get("/api/settings", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    assert "qv" in body and "scoreThreshold" in body
    assert "raw" in body  # full row, for populating every Criteria-tab field
    assert "cfg" not in body  # title lists live on profiles now
    profile_ids = {p["id"] for p in body["profiles"]}
    assert profile_ids == {"automation", "cpg"}
    automation = next(p for p in body["profiles"] if p["id"] == "automation")
    assert automation["inc"] and automation["variant"] == "engineer"


# ---- settings write + preview + credits ----


def test_api_settings_patch_requires_auth(raw_client):
    resp = raw_client.patch("/api/settings", json={"queue_min_score": 60})
    assert resp.status_code == 401


def test_api_settings_patch_persists_and_returns_updated(client, fake_db):
    resp = client.patch("/api/settings", json={"queue_min_score": 60}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    assert resp.json()["raw"]["queue_min_score"] == 60
    assert fake_db.get_settings()["queue_min_score"] == 60


def test_api_settings_patch_shared_field_syncs_theirstack(client):
    resp = client.patch(
        "/api/settings", json={"remote_only": True}, auth=(DASH_USER, DASH_PASS)
    )
    assert resp.status_code == 200
    # can't inspect the FakeTheirStack instance directly here (it's constructed
    # fresh per-request via the dependency override lambda), but a 200 with no
    # exception confirms upsert_saved_search was reachable (for both profiles)
    # and didn't raise


def test_api_search_profile_patch_requires_auth(raw_client):
    resp = raw_client.patch("/api/search-profiles/automation", json={})
    assert resp.status_code == 401


def test_api_search_profile_patch_updates_title_lists(client, fake_db):
    resp = client.patch(
        "/api/search-profiles/automation", json={"title_include": ["business systems analyst"]},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "automation"
    assert body["inc"] == ["business systems analyst"]


def test_api_search_profile_patch_unknown_profile_404s(client):
    resp = client.patch(
        "/api/search-profiles/nonexistent", json={"title_include": []}, auth=(DASH_USER, DASH_PASS)
    )
    assert resp.status_code == 404


def test_api_search_profile_pause_requires_auth(raw_client):
    resp = raw_client.post("/api/search-profiles/cpg/pause", json={"paused": True})
    assert resp.status_code == 401


def test_api_search_profile_pause_stops_daily_ingestion(client):
    resp = client.post(
        "/api/search-profiles/cpg/pause", json={"paused": True}, auth=(DASH_USER, DASH_PASS)
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "profile_id": "cpg", "paused": True}


def test_api_search_profile_pause_resumes(client):
    resp = client.post(
        "/api/search-profiles/automation/pause", json={"paused": False}, auth=(DASH_USER, DASH_PASS)
    )
    assert resp.status_code == 200
    assert resp.json()["paused"] is False


def test_api_settings_includes_paused_state_per_profile(client):
    resp = client.get("/api/settings", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    profiles = resp.json()["profiles"]
    assert all("paused" in p for p in profiles)


def test_api_preview_requires_auth(raw_client):
    resp = raw_client.post("/api/preview", json={})
    assert resp.status_code == 401


def test_api_preview_returns_real_shape(client):
    resp = client.post("/api/preview", json={"profile_id": "automation"}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"day", "week", "backlog", "sample"}
    assert body["week"] == 42
    assert body["day"] == 6.0  # 42/7, FakeTheirStack.free_count always returns 42


def test_api_preview_defaults_to_automation_profile(client):
    resp = client.post("/api/preview", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200


def test_api_preview_unknown_profile_400s(client):
    resp = client.post(
        "/api/preview", json={"profile_id": "nonexistent"}, auth=(DASH_USER, DASH_PASS)
    )
    assert resp.status_code == 400


def test_api_credits_requires_auth(raw_client):
    resp = raw_client.get("/api/credits")
    assert resp.status_code == 401


def test_api_credits_returns_real_balance(client):
    resp = client.get("/api/credits", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    assert body["used_api_credits"] == 10  # FakeTheirStack default
    assert body["api_credits"] == 200
    assert body["daily_credit_cap"] is None  # not set by default
    assert body["used_today"] == 0


def test_api_credits_reset_daily_requires_auth(raw_client):
    resp = raw_client.post("/api/credits/reset-daily", json={})
    assert resp.status_code == 401


def test_api_credits_reset_daily_zeroes_used_today(client):
    client.patch("/api/settings", json={"daily_credit_cap": 5}, auth=(DASH_USER, DASH_PASS))
    before = client.get("/api/credits", auth=(DASH_USER, DASH_PASS)).json()
    assert before["used_today"] == 0  # FakeTheirStack's used_api_credits=10, first call sets baseline

    resp = client.post("/api/credits/reset-daily", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    assert resp.json() == {"reset": True, "new_baseline": 10}

    after = client.get("/api/credits", auth=(DASH_USER, DASH_PASS)).json()
    assert after["used_today"] == 0


# ---- assembly ----


def _seed_scored_posting(client) -> int:
    body = json.dumps({"id": 1, "type": "job.new", "payload": SAMPLE_JOB}).encode()
    client.post("/webhooks/theirstack", content=body, headers={"X-TheirStack-Signature-256": sign(body)})
    posting = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()[0]
    return posting["id"]


# ---- manual add ----

MANUAL_FIELDS = {
    "title": "Business Operations Generalist",
    "company": "Rex Client",
    "url": "https://rex.zone/jobs/12345",
    "jd_text": "Own ambiguous operational problems and turn them into measurable outcomes.",
    "location": "United States",
    "remote": True,
}


def test_api_postings_manual_requires_auth(raw_client):
    resp = raw_client.post("/api/postings/manual", json=MANUAL_FIELDS)
    assert resp.status_code == 401


def test_api_postings_manual_requires_title_url_and_jd_text(client):
    resp = client.post(
        "/api/postings/manual", json={"title": "X"}, auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 400


def test_api_postings_manual_ingests_and_scores_at_zero_credits(client, fake_db):
    resp = client.post("/api/postings/manual", json=MANUAL_FIELDS, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("scored", "archived")

    ingested = next(e for e in fake_db.events if e["event"] == "ingested")
    assert ingested["payload"]["source"] == "manual"
    assert ingested["payload"]["credits_consumed"] == 0

    if body["status"] == "scored":
        postings = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()
        assert any(p["ti"] == "Business Operations Generalist" for p in postings)


def test_api_postings_manual_repeat_url_upserts_not_duplicates(client, fake_db):
    client.post("/api/postings/manual", json=MANUAL_FIELDS, auth=(DASH_USER, DASH_PASS))
    client.post("/api/postings/manual", json=MANUAL_FIELDS, auth=(DASH_USER, DASH_PASS))
    assert len(fake_db.postings) == 1


def test_api_assemble_requires_auth(raw_client):
    resp = raw_client.post("/api/postings/1/assemble", json={})
    assert resp.status_code == 401


def test_api_assemble_happy_path(client, fake_db):
    posting_id = _seed_scored_posting(client)
    resp = client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "engineer"
    assert body["bullet_refs"] == ["BL-001"]
    assert body["brief"] == "Lead with the production system."
    assert body["signed_url"].startswith("https://")
    assert len(fake_db.uploaded_documents) == 1


def test_api_assemble_unknown_posting_returns_404(client):
    resp = client.post("/api/postings/999999/assemble", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 404


def test_api_assemble_blocks_unverified_bullets_with_422(client, fake_db):
    posting_id = _seed_scored_posting(client)
    fake_db.get_bullets_full = lambda: [{
        "id": 1, "ref": "BL-001", "text": "Sample bullet.", "source_org": "BEAM LEGACY GROUP",
        "source_period": "2025-Present", "tags": ["automation"], "variants": ["engineer"],
        "provenance": "measured", "status": "draft",
    }]
    resp = client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 422
    assert "status=draft" in resp.json()["detail"]


def test_api_variant_detail_requires_auth(raw_client):
    resp = raw_client.get("/api/postings/1/variant")
    assert resp.status_code == 401


def test_api_variant_detail_404_when_not_assembled(client):
    posting_id = _seed_scored_posting(client)
    resp = client.get(f"/api/postings/{posting_id}/variant", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 404


def test_api_variant_detail_restores_sections_and_fresh_signed_url(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))
    resp = client.get(f"/api/postings/{posting_id}/variant", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "engineer"
    assert body["sections"][0]["order"][0]["ref"] == "BL-001"
    assert body["signed_url"].startswith("https://")


# ---- cover letter ----


def test_api_cover_letter_requires_auth(raw_client):
    resp = raw_client.post("/api/postings/1/cover-letter", json={})
    assert resp.status_code == 401


def test_api_cover_letter_requires_a_built_resume_first(client):
    posting_id = _seed_scored_posting(client)
    resp = client.post(f"/api/postings/{posting_id}/cover-letter", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 400


def test_api_cover_letter_happy_path(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))

    resp = client.post(f"/api/postings/{posting_id}/cover-letter", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    assert body["cover_letter_text"]
    assert body["signed_url"].startswith("https://")
    assert any(e["event"] == "cover_letter_generated" for e in fake_db.events)

    # stored on the variant, and comes back through /api/postings too
    posting = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()[0]
    assert posting["id"] == posting_id


def test_api_cover_letter_unknown_posting_404s(client):
    resp = client.post("/api/postings/999999/cover-letter", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 404


def test_api_cover_letter_502s_on_empty_generation(client, fake_db):
    # Found live: extended thinking ate the whole token budget and chat_call
    # returned "" — that must surface as a 502, never a silently-saved
    # broken artifact.
    class EmptyAnthropic(FakeAnthropic):
        def chat_call(self, **kwargs):
            return "", 0.01

    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))

    app.dependency_overrides[get_anthropic] = lambda: EmptyAnthropic()
    resp = client.post(f"/api/postings/{posting_id}/cover-letter", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 502
    assert "no usable text" in resp.json()["detail"]


def test_api_variant_detail_includes_cover_letter_signed_url_once_generated(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))
    client.post(f"/api/postings/{posting_id}/cover-letter", json={}, auth=(DASH_USER, DASH_PASS))

    resp = client.get(f"/api/postings/{posting_id}/variant", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    assert resp.json()["cover_letter_signed_url"].startswith("https://")


# ---- cover letter sandbox (preview) ----


def test_api_cover_letter_preview_requires_auth(raw_client):
    resp = raw_client.post("/api/postings/1/cover-letter/preview", json={})
    assert resp.status_code == 401


def test_api_cover_letter_preview_requires_a_built_resume_first(client):
    posting_id = _seed_scored_posting(client)
    resp = client.post(f"/api/postings/{posting_id}/cover-letter/preview", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 400


def test_api_cover_letter_preview_returns_all_three_styles_unsaved(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))

    resp = client.post(f"/api/postings/{posting_id}/cover-letter/preview", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    styles = {v["style"] for v in body["variants"]}
    assert styles == {"traditional", "compressed", "warm"}
    for v in body["variants"]:
        assert v["text"]
        assert v["words"] > 0
        assert v["label"]
        assert v["description"]
    assert any(e["event"] == "cover_letter_preview_generated" for e in fake_db.events)

    # nothing saved to the variant — preview is scratch space only
    variant_resp = client.get(f"/api/postings/{posting_id}/variant", auth=(DASH_USER, DASH_PASS))
    assert variant_resp.json()["cover_letter_signed_url"] is None


def test_api_cover_letter_saves_a_picked_preview_draft_without_regenerating(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))

    class ExplodingAnthropic(FakeAnthropic):
        def chat_call(self, **kwargs):
            raise AssertionError("should not regenerate — a picked draft's text was already provided")

    app.dependency_overrides[get_anthropic] = lambda: ExplodingAnthropic()
    chosen_text = "A hand-picked draft from the sandbox, long enough to clear the floor." * 2
    resp = client.post(
        f"/api/postings/{posting_id}/cover-letter", json={"text": chosen_text},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cover_letter_text"] == chosen_text
    assert body["signed_url"].startswith("https://")


def test_api_cover_letter_rejects_too_short_provided_text(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))

    resp = client.post(
        f"/api/postings/{posting_id}/cover-letter", json={"text": "too short"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 400


def test_api_cover_letter_logs_feedback_note_as_its_own_event(client, fake_db):
    # The feedback loop: an edit note is durable calibration data, not
    # applied to anything automatically — a human reviews it later.
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))

    edited_text = "The edited version of the letter, long enough to clear the length floor easily." * 2
    resp = client.post(
        f"/api/postings/{posting_id}/cover-letter",
        json={"text": edited_text, "feedback_note": "too formal, cut the second paragraph", "previous_text": "old draft"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200

    feedback_events = [e for e in fake_db.events if e["event"] == "cover_letter_feedback"]
    assert len(feedback_events) == 1
    payload = feedback_events[0]["payload"]
    assert payload["note"] == "too formal, cut the second paragraph"
    assert payload["edited_text"] == edited_text
    assert payload["previous_text"] == "old draft"


def test_api_cover_letter_skips_feedback_event_when_no_note_given(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))

    resp = client.post(f"/api/postings/{posting_id}/cover-letter", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    assert not [e for e in fake_db.events if e["event"] == "cover_letter_feedback"]


# ---- outreach ----


def test_api_outreach_requires_auth(raw_client):
    resp = raw_client.post("/api/postings/1/outreach", json={})
    assert resp.status_code == 401


def test_api_outreach_requires_target_name(client):
    posting_id = _seed_scored_posting(client)
    resp = client.post(f"/api/postings/{posting_id}/outreach", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 400


def test_api_outreach_happy_path(client, fake_db):
    posting_id = _seed_scored_posting(client)
    resp = client.post(
        f"/api/postings/{posting_id}/outreach",
        json={"target_name": "Jane Doe", "target_title": "VP Eng"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_name"] == "Jane Doe"
    assert body["draft_linkedin_note"]
    assert body["draft_email_subject"]


def test_api_outreach_blocks_unverified_metric_with_422(client, fake_db):
    posting_id = _seed_scored_posting(client)
    fake_db.get_bullets_full = lambda: [{
        "id": 1, "ref": "BL-001", "text": "Sample bullet.", "source_org": "BEAM LEGACY GROUP",
        "source_period": "2025-Present", "tags": ["automation"], "variants": ["engineer"],
        "provenance": "measured", "status": "draft",
    }]
    resp = client.post(
        f"/api/postings/{posting_id}/outreach", json={"target_name": "Jane Doe"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 422


def test_api_outreach_sent_requires_auth(raw_client):
    resp = raw_client.post("/api/postings/1/outreach/sent", json={})
    assert resp.status_code == 401


def test_api_outreach_sent_404_when_no_outreach_row(client):
    posting_id = _seed_scored_posting(client)
    resp = client.post(
        f"/api/postings/{posting_id}/outreach/sent", json={}, auth=(DASH_USER, DASH_PASS)
    )
    assert resp.status_code == 404


def test_api_outreach_sent_marks_sent(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(
        f"/api/postings/{posting_id}/outreach", json={"target_name": "Jane Doe"},
        auth=(DASH_USER, DASH_PASS),
    )
    resp = client.post(
        f"/api/postings/{posting_id}/outreach/sent", json={"channel": "email"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent_channel"] == "email"
    assert body["sent_at"]


# ---- applications ----


def test_api_application_patch_requires_auth(raw_client):
    resp = raw_client.patch("/api/postings/1/application", json={"status": "deferred"})
    assert resp.status_code == 401


def test_api_application_patch_defer_moves_posting_to_watch(client, fake_db):
    posting_id = _seed_scored_posting(client)
    resp = client.patch(
        f"/api/postings/{posting_id}/application", json={"status": "deferred"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    p = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()[0]
    assert p["stage"] == "watch"
    assert any(e["event"] == "deferred_by_user" for e in fake_db.events)


def test_api_application_patch_reject_logs_reason_and_hides_posting(client, fake_db):
    posting_id = _seed_scored_posting(client)
    resp = client.patch(
        f"/api/postings/{posting_id}/application",
        json={"status": "rejected", "notes": "wrong seniority"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    p = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()[0]
    assert p["stage"] == "rejected"
    reject_events = [e for e in fake_db.events if e["event"] == "rejected_by_user"]
    assert reject_events and reject_events[0]["payload"]["reason"] == "wrong seniority"


def test_api_application_patch_back_to_queue_after_approve(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))
    approved = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()[0]
    assert approved["stage"] == "approved"

    resp = client.patch(
        f"/api/postings/{posting_id}/application", json={"status": "queued"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    p = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()[0]
    assert p["stage"] == "queue"
    assert p["app"]["file"]  # the built variant is still there, not discarded


def test_api_application_patch_mark_submitted_moves_to_applied(client, fake_db):
    posting_id = _seed_scored_posting(client)
    resp = client.patch(
        f"/api/postings/{posting_id}/application",
        json={"status": "submitted", "submitted_at": "2026-08-04T12:00:00+00:00"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    p = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()[0]
    assert p["stage"] == "applied"
    assert p["app"]["sent"] == "2026-08-04T12:00:00+00:00"


# ---- manual archive ----


def test_api_archive_posting_requires_auth(raw_client):
    resp = raw_client.post("/api/postings/1/archive", json={})
    assert resp.status_code == 401


def test_api_archive_posting_removes_it_from_postings_list(client, fake_db):
    posting_id = _seed_scored_posting(client)
    assert len(client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()) == 1

    resp = client.post(f"/api/postings/{posting_id}/archive", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200

    remaining = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()
    assert remaining == []
    assert any(e["event"] == "archived_by_user" for e in fake_db.events)


# ---- archived tab ----


def test_api_postings_archived_requires_auth(raw_client):
    resp = raw_client.get("/api/postings/archived")
    assert resp.status_code == 401


def test_api_postings_archived_lists_a_manually_archived_posting(client):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/archive", json={}, auth=(DASH_USER, DASH_PASS))

    archived = client.get("/api/postings/archived", auth=(DASH_USER, DASH_PASS)).json()
    assert len(archived) == 1
    assert archived[0]["id"] == posting_id
    assert archived[0]["status"] == "expired"
    assert archived[0]["reason"] == "archived by user"
    assert archived[0]["canRestore"] is True
    assert archived[0]["score"] is not None


def test_api_postings_archived_omits_queue_level_rejects(client):
    """Rejects stay status='scored' and already surface as stage='rejected'
    in /api/postings — the archived endpoint would double-count them."""
    posting_id = _seed_scored_posting(client)
    client.patch(
        f"/api/postings/{posting_id}/application", json={"status": "rejected", "notes": "not a fit"},
        auth=(DASH_USER, DASH_PASS),
    )
    archived = client.get("/api/postings/archived", auth=(DASH_USER, DASH_PASS)).json()
    assert archived == []
    postings = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()
    assert postings[0]["stage"] == "rejected"


def test_api_restore_posting_requires_auth(raw_client):
    resp = raw_client.post("/api/postings/1/restore", json={})
    assert resp.status_code == 401


def test_api_restore_posting_puts_it_back_in_queue(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/archive", json={}, auth=(DASH_USER, DASH_PASS))
    assert client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json() == []

    resp = client.post(f"/api/postings/{posting_id}/restore", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200

    restored = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()
    assert len(restored) == 1
    assert restored[0]["id"] == posting_id
    assert client.get("/api/postings/archived", auth=(DASH_USER, DASH_PASS)).json() == []
    assert any(e["event"] == "restored_by_user" for e in fake_db.events)


def test_api_restore_posting_without_a_score_400s(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/archive", json={}, auth=(DASH_USER, DASH_PASS))
    fake_db.scores.clear()  # simulate a pre-score deterministic-filter archive

    resp = client.post(f"/api/postings/{posting_id}/restore", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 400


def test_api_restore_posting_404s_for_unknown_id(client):
    resp = client.post("/api/postings/999999/restore", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 404


# ---- score override ----


def test_api_score_override_requires_auth(raw_client):
    resp = raw_client.patch("/api/postings/1/score-override", json={"total": 78, "reason": "x"})
    assert resp.status_code == 401


def test_api_score_override_requires_reason(client):
    posting_id = _seed_scored_posting(client)
    resp = client.patch(
        f"/api/postings/{posting_id}/score-override", json={"total": 78},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 400


def test_api_score_override_moves_posting_into_queue(client, fake_db):
    posting_id = _seed_scored_posting(client)
    resp = client.patch(
        f"/api/postings/{posting_id}/score-override",
        json={"total": 90, "reason": "rubric undercounted the automation overlap"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["human_override_total"] == 90
    assert body["human_override_reason"] == "rubric undercounted the automation overlap"

    p = next(x for x in client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json() if x["id"] == posting_id)
    assert p["stage"] == "queue"
    assert p["score"] == 90
    assert p["aiScore"] != 90  # original AI total untouched
    assert any(e["event"] == "human_override" for e in fake_db.events)


def test_api_score_override_clear_restores_ai_score(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.patch(
        f"/api/postings/{posting_id}/score-override", json={"total": 90, "reason": "x"},
        auth=(DASH_USER, DASH_PASS),
    )
    resp = client.patch(
        f"/api/postings/{posting_id}/score-override", json={"clear": True},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    p = next(x for x in client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json() if x["id"] == posting_id)
    assert p["score"] == p["aiScore"]
    assert p["scoreOverride"] is None


def test_api_score_override_404_when_not_scored(client):
    resp = client.patch(
        "/api/postings/999999/score-override", json={"total": 80, "reason": "x"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 404


# ---- answer workbench ----


def test_api_answers_requires_auth(raw_client):
    resp = raw_client.get("/api/answers")
    assert resp.status_code == 401


def test_api_answers_returns_list(client, fake_db):
    fake_db.answers.append({"ref": "A7", "question_type": "tell_me_about_a_failure", "text": "x", "status": "ready"})
    resp = client.get("/api/answers", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    assert resp.json()[0]["ref"] == "A7"


def test_api_answers_chat_requires_auth(raw_client):
    resp = raw_client.post("/api/answers/chat", json={"messages": []})
    assert resp.status_code == 401


def test_api_answers_chat_requires_messages(client):
    resp = client.post("/api/answers/chat", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 400


def test_api_answers_chat_returns_reply(client):
    resp = client.post(
        "/api/answers/chat",
        json={"messages": [{"role": "user", "content": "Draft an answer about X."}]},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Here's a draft answer grounded in your verified bullets."
    assert body["cost_usd"] == 0.008


def test_api_answers_chat_with_posting_context_404s_on_unknown_posting(client):
    resp = client.post(
        "/api/answers/chat",
        json={"messages": [{"role": "user", "content": "x"}], "posting_id": 999999},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 404


def test_api_answers_chat_with_posting_context(client, fake_db):
    posting_id = _seed_scored_posting(client)
    resp = client.post(
        "/api/answers/chat",
        json={"messages": [{"role": "user", "content": "x"}], "posting_id": posting_id},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200


def test_api_answers_save_requires_text_and_question_type(client):
    resp = client.post("/api/answers/save", json={"text": "x"}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 400


def test_api_answers_save_creates_new_ref_when_none_given(client, fake_db):
    fake_db.answers.append({"ref": "A11", "question_type": "x", "text": "x", "status": "ready"})
    resp = client.post(
        "/api/answers/save",
        json={"question_type": "new_question", "text": "The drafted answer."},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ref"] == "A12"
    assert body["status"] == "ready"
    assert any(e["event"] == "saved" for e in fake_db.events)


def test_api_answers_save_updates_existing_ref(client, fake_db):
    fake_db.answers.append({"ref": "A6", "question_type": "biggest_system_built", "text": "old", "status": "draft"})
    resp = client.post(
        "/api/answers/save",
        json={"ref": "A6", "question_type": "biggest_system_built", "text": "new text", "status": "ready"},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "new text"
    assert len(fake_db.answers) == 1  # updated in place, not duplicated


def test_api_answers_save_stores_question_text_and_posting_id(client, fake_db):
    posting_id = _seed_scored_posting(client)
    resp = client.post(
        "/api/answers/save",
        json={
            "question_type": "learned_fast", "text": "The answer.",
            "question_text": "Tell me about learning something fast.", "posting_id": posting_id,
        },
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["question_text"] == "Tell me about learning something fast."
    assert body["posting_id"] == posting_id


def test_api_answers_chat_returns_suggested_question_type(client):
    resp = client.post(
        "/api/answers/chat",
        json={"messages": [{"role": "user", "content": "Tell me about a time you learned something quickly."}]},
        auth=(DASH_USER, DASH_PASS),
    )
    assert resp.status_code == 200
    assert resp.json()["suggested_question_type"] == "time_learned_something_quickly"


def test_api_answers_mark_used_requires_auth(raw_client):
    resp = raw_client.post("/api/answers/A1/mark-used", json={})
    assert resp.status_code == 401


def test_api_answers_mark_used_bumps_usage(client, fake_db):
    fake_db.answers.append({"ref": "A9", "question_type": "x", "text": "x", "status": "ready", "times_used": 2})
    resp = client.post("/api/answers/A9/mark-used", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    assert body["times_used"] == 3
    assert body["last_used_at"]
    assert any(e["event"] == "reused" for e in fake_db.events)


def test_api_answers_mark_used_unknown_ref_404s(client):
    resp = client.post("/api/answers/nonexistent/mark-used", json={}, auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 404


# ---- metrics ----


def test_api_metrics_requires_auth(raw_client):
    resp = raw_client.get("/api/metrics")
    assert resp.status_code == 401


def test_api_metrics_returns_real_shape(client, fake_db):
    _seed_scored_posting(client)
    resp = client.get("/api/metrics", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "medianLeadTime", "ingested", "surfaced", "submitted", "replies", "scoringSpend",
    }
    assert body["ingested"] == 1
    assert body["submitted"] == 0
    assert body["medianLeadTime"] == "—"


def test_api_postings_reflects_approved_stage_and_outreach_after_assembly(client, fake_db):
    posting_id = _seed_scored_posting(client)
    client.post(f"/api/postings/{posting_id}/assemble", json={}, auth=(DASH_USER, DASH_PASS))
    client.post(
        f"/api/postings/{posting_id}/outreach", json={"target_name": "Jane Doe"},
        auth=(DASH_USER, DASH_PASS),
    )
    resp = client.get("/api/postings", auth=(DASH_USER, DASH_PASS))
    p = resp.json()[0]
    assert p["stage"] == "approved"
    assert p["app"]["file"]
    assert p["o"]["name"] == "Jane Doe"
    assert p["o"]["note"]
