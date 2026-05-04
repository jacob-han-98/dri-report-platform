"""CSRF protection — double-submit cookie pattern.

cookie `hybe_csrf` (httponly, samesite=Lax) holds a random token.
forms include `<input name="csrf_token" value="...">` with the same value.
on unsafe (POST/PUT/PATCH/DELETE) requests, middleware compares them.

Bearer-auth requests skip the check (browsers don't auto-attach Authorization).
"""
from __future__ import annotations

import hmac
import secrets

from fastapi import Request, Response

from app.config import get_settings

CSRF_COOKIE = "hybe_csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"  # alternative for AJAX clients
CSRF_MAX_AGE = 30 * 24 * 3600  # 30 days


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def ensure_token_on_request(request: Request) -> tuple[str, bool]:
    """Read existing CSRF cookie, or mint a fresh token. Stash on request.state
    so downstream template rendering can read it via `request.state.csrf_token`.

    Returns (token, is_new). If is_new=True, the caller must set the cookie on
    the response.
    """
    existing = request.cookies.get(CSRF_COOKIE)
    if existing:
        request.state.csrf_token = existing
        return existing, False
    token = generate_token()
    request.state.csrf_token = token
    return token, True


def get_token(request: Request) -> str:
    """Read the request's CSRF token. Falls back to cookie if state was unset
    (e.g., direct test access without going through middleware)."""
    token = getattr(request.state, "csrf_token", None)
    if token:
        return token
    return request.cookies.get(CSRF_COOKIE, "")


def set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        max_age=CSRF_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path=get_settings().app.web_prefix or "/",
    )


def validate(request: Request, submitted: str | None) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or not submitted:
        return False
    return hmac.compare_digest(cookie, submitted)
