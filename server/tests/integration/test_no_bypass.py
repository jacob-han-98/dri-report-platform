"""Regression: when [dev] bypass_auth_email is empty, OIDC-only mode is enforced.

Uses FastAPI TestClient + dependency override to flip bypass off without
restarting the server. Caddy redirect logic (401 → /auth/login) is not
exercised here — that's a Caddy concern. We assert the FastAPI surface returns
401 / accepts Bearer / accepts a session cookie identically.
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.auth.session import set_session_cookie
from app.config import Settings, get_settings
from app.main import app


@pytest.fixture()
def bypass_token():
    """Issue a token directly via the DB layer (no /api/* round-trip needed)."""
    from datetime import datetime, timezone

    from app.auth.tokens import issue_token
    from app.db import SessionLocal
    from app.models import ApiToken, User

    real = get_settings()
    if not real.dev.bypass_auth_email:
        pytest.skip("need bypass enabled in config to identify a target user")

    db = SessionLocal()
    try:
        user = db.get(User, real.dev.bypass_auth_email)
        assert user is not None, "bypass user must exist (run bypass once first)"
        issued = issue_token(db, user=user, name="no-bypass-test", expires_in_days=1)
        token, prefix = issued.plaintext, issued.prefix
    finally:
        db.close()

    yield token, prefix

    db = SessionLocal()
    try:
        t = db.query(ApiToken).filter(ApiToken.prefix == prefix).one_or_none()
        if t and t.revoked_at is None:
            t.revoked_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@pytest.fixture()
def no_bypass_client():
    """TestClient with bypass_auth_email forced empty."""
    real = get_settings()
    patched = copy.deepcopy(real)
    patched.dev.bypass_auth_email = ""

    app.dependency_overrides[get_settings] = lambda: patched
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_auth_check_without_anything_returns_401(no_bypass_client: TestClient) -> None:
    r = no_bypass_client.get("/auth/check")
    assert r.status_code == 401


def test_auth_check_with_invalid_bearer_returns_401(no_bypass_client: TestClient) -> None:
    r = no_bypass_client.get(
        "/auth/check", headers={"Authorization": "Bearer hybe_pat_definitelynotreal"}
    )
    assert r.status_code == 401


def test_auth_check_with_valid_bearer_returns_200(
    no_bypass_client: TestClient, bypass_token
) -> None:
    """Bearer auth still works when bypass is off — that's the whole point."""
    token, _ = bypass_token
    r = no_bypass_client.get(
        "/auth/check", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert r.headers.get("x-user-email")


def test_auth_check_with_session_cookie_returns_200(no_bypass_client: TestClient) -> None:
    """A signed session cookie is honored even when bypass is off."""
    real_settings = get_settings()
    if not real_settings.dev.bypass_auth_email:
        pytest.skip("need bypass enabled to seed a real user row")

    bypass_email = real_settings.dev.bypass_auth_email

    # Construct a session cookie value the same way /auth/callback would.
    from itsdangerous import URLSafeTimedSerializer
    serializer = URLSafeTimedSerializer(real_settings.app.secret_key, salt="hybe-session-v1")
    cookie_value = serializer.dumps({"email": bypass_email, "sub": None})

    r = no_bypass_client.get(
        "/auth/check", cookies={"hybe_session": cookie_value}
    )
    assert r.status_code == 200
    assert r.headers.get("x-user-email") == bypass_email


def test_api_endpoints_reject_anonymous_when_bypass_off(no_bypass_client: TestClient) -> None:
    """In production, Caddy's forward_auth gates /api/* via /auth/check. So when
    bypass is off, /auth/check refuses anonymous and Caddy returns 401 — meaning
    /api/* never even reaches FastAPI. Verify the gate (/auth/check) directly."""
    r = no_bypass_client.get("/auth/check")
    assert r.status_code == 401, f"got {r.status_code}: {r.text}"
