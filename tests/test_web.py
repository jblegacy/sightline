import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from sightline.config import Settings
from tests.test_ingest import SAMPLE_JOB, FakeAnthropic, FakeDB
from web.main import app, get_anthropic, get_db
from sightline.config import get_settings as real_get_settings

SECRET = "a-long-enough-test-secret-value"


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
    )
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_anthropic] = lambda: FakeAnthropic()
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
    )
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_anthropic] = lambda: FakeAnthropic()
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
