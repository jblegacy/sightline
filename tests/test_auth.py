from sightline.auth import make_session_token, verify_session_token
from sightline.config import Settings

SETTINGS = Settings(
    supabase_url="https://example.supabase.co",
    supabase_service_key="fake",
    theirstack_api_key="fake",
    theirstack_webhook_secret="fake",
    theirstack_webhook_url="https://example.com/webhooks/theirstack",
    anthropic_api_key="fake",
    dashboard_username="james",
    dashboard_password="correct-horse-battery-staple",
)

OTHER_SETTINGS = Settings(
    supabase_url=SETTINGS.supabase_url,
    supabase_service_key=SETTINGS.supabase_service_key,
    theirstack_api_key=SETTINGS.theirstack_api_key,
    theirstack_webhook_secret=SETTINGS.theirstack_webhook_secret,
    theirstack_webhook_url=SETTINGS.theirstack_webhook_url,
    anthropic_api_key=SETTINGS.anthropic_api_key,
    dashboard_username="james",
    dashboard_password="a-totally-different-password",
)


def test_a_freshly_made_token_verifies():
    token = make_session_token(SETTINGS)
    assert verify_session_token(token, SETTINGS) is True


def test_tampered_username_fails():
    token = make_session_token(SETTINGS)
    payload, sig = token.rsplit(".", 1)
    _, expiry = payload.split(".", 1)
    forged = f"someone-else.{expiry}.{sig}"
    assert verify_session_token(forged, SETTINGS) is False


def test_tampered_expiry_fails():
    token = make_session_token(SETTINGS)
    username, expiry, sig = token.split(".", 2)
    forged = f"{username}.{int(expiry) + 999999}.{sig}"
    assert verify_session_token(forged, SETTINGS) is False


def test_expired_token_fails():
    token = make_session_token(SETTINGS, now=1_000_000)
    # verified far enough past its expiry (now + 30 days) that it's stale
    assert verify_session_token(token, SETTINGS, now=1_000_000 + 60 * 60 * 24 * 31) is False


def test_token_signed_with_a_different_password_fails():
    token = make_session_token(SETTINGS)
    assert verify_session_token(token, OTHER_SETTINGS) is False


def test_garbage_token_fails():
    assert verify_session_token("not-a-real-token", SETTINGS) is False
    assert verify_session_token("", SETTINGS) is False
    assert verify_session_token("a.b.c.d", SETTINGS) is False


def test_changing_the_password_invalidates_existing_sessions():
    # rotating dashboard_password should log everyone out — deriving the
    # signing key from the password is what makes that true for free
    token = make_session_token(SETTINGS)
    assert verify_session_token(token, SETTINGS) is True
    assert verify_session_token(token, OTHER_SETTINGS) is False
