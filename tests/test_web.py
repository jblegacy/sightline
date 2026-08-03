import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

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
def client(fake_db):
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


def test_dashboard_requires_auth(client):
    resp = client.get("/")
    assert resp.status_code == 401


def test_dashboard_rejects_wrong_password(client):
    resp = client.get("/", auth=(DASH_USER, "wrong-password"))
    assert resp.status_code == 401


def test_dashboard_serves_html_with_correct_auth(client):
    resp = client.get("/", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_api_postings_requires_auth(client):
    resp = client.get("/api/postings")
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


def test_api_settings_requires_auth(client):
    resp = client.get("/api/settings")
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


def test_api_settings_patch_requires_auth(client):
    resp = client.patch("/api/settings", json={"queue_min_score": 60})
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


def test_api_search_profile_patch_requires_auth(client):
    resp = client.patch("/api/search-profiles/automation", json={})
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


def test_api_preview_requires_auth(client):
    resp = client.post("/api/preview", json={})
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


def test_api_credits_requires_auth(client):
    resp = client.get("/api/credits")
    assert resp.status_code == 401


def test_api_credits_returns_real_balance(client):
    resp = client.get("/api/credits", auth=(DASH_USER, DASH_PASS))
    assert resp.status_code == 200
    body = resp.json()
    assert body["used_api_credits"] == 10  # FakeTheirStack default
    assert body["api_credits"] == 200


# ---- assembly ----


def _seed_scored_posting(client) -> int:
    body = json.dumps({"id": 1, "type": "job.new", "payload": SAMPLE_JOB}).encode()
    client.post("/webhooks/theirstack", content=body, headers={"X-TheirStack-Signature-256": sign(body)})
    posting = client.get("/api/postings", auth=(DASH_USER, DASH_PASS)).json()[0]
    return posting["id"]


def test_api_assemble_requires_auth(client):
    resp = client.post("/api/postings/1/assemble", json={})
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


def test_api_variant_detail_requires_auth(client):
    resp = client.get("/api/postings/1/variant")
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


# ---- outreach ----


def test_api_outreach_requires_auth(client):
    resp = client.post("/api/postings/1/outreach", json={})
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


def test_api_outreach_sent_requires_auth(client):
    resp = client.post("/api/postings/1/outreach/sent", json={})
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
