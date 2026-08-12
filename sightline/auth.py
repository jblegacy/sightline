"""Cookie-based session auth for the dashboard — replaces HTTP Basic Auth's
native browser popup with a real login page (POST /login in web/main.py).

Session tokens are HMAC-signed with a key derived from dashboard_password,
stdlib only, no new dependency and no session-store table: there is exactly
one user, so a database-backed session isn't buying anything a signed cookie
doesn't already give for free. Deriving the signing key from the password
also means rotating the password invalidates every existing session
automatically — the right default for a personal single-user tool, not
something that needs its own revocation mechanism.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from sightline.config import Settings

SESSION_COOKIE = "sightline_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days — personal tool, convenience over rotation


def _signing_key(settings: Settings) -> bytes:
    return hashlib.sha256(settings.dashboard_password.encode()).digest()


def make_session_token(settings: Settings, now: int | None = None) -> str:
    expiry = (now if now is not None else int(time.time())) + SESSION_MAX_AGE_SECONDS
    payload = f"{settings.dashboard_username}.{expiry}"
    sig = hmac.new(_signing_key(settings), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str, settings: Settings, now: int | None = None) -> bool:
    parts = token.split(".", 2)
    if len(parts) != 3:
        return False
    username, expiry_s, sig = parts
    payload = f"{username}.{expiry_s}"
    expected_sig = hmac.new(_signing_key(settings), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return False
    try:
        expiry = int(expiry_s)
    except ValueError:
        return False
    if expiry < (now if now is not None else int(time.time())):
        return False
    return hmac.compare_digest(username, settings.dashboard_username)
