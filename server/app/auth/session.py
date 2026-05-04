"""Browser session — signed cookie via itsdangerous."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

COOKIE_NAME = "hybe_session"
VIEW_AS_COOKIE = "hybe_view_as"
VIEW_AS_MAX_AGE = 4 * 3600  # 4 hours


@dataclass
class Session:
    email: str
    google_sub: str | None


def _cookie_path() -> str:
    return get_settings().app.web_prefix or "/"


def _serializer() -> URLSafeTimedSerializer:
    s = get_settings()
    return URLSafeTimedSerializer(s.app.secret_key, salt="hybe-session-v1")


def _view_as_serializer() -> URLSafeTimedSerializer:
    s = get_settings()
    return URLSafeTimedSerializer(s.app.secret_key, salt="hybe-view-as-v1")


def set_session_cookie(resp: Response, email: str, google_sub: str | None = None) -> None:
    s = get_settings()
    payload = {"email": email, "sub": google_sub}
    token = _serializer().dumps(payload)
    max_age = s.app.session_lifetime_days * 24 * 3600
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=True,  # Caddy terminates TLS upstream of FastAPI; cookies travel over HTTPS
        samesite="lax",
        path=_cookie_path(),
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(key=COOKIE_NAME, path=_cookie_path())


def read_session(request: Request) -> Session | None:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    s = get_settings()
    max_age = s.app.session_lifetime_days * 24 * 3600
    try:
        payload = _serializer().loads(raw, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict) or "email" not in payload:
        return None
    return Session(email=payload["email"], google_sub=payload.get("sub"))


# ---------- view-as (admin → user simulation) ----------


def set_view_as(resp: Response, role: str) -> None:
    token = _view_as_serializer().dumps({"role": role})
    resp.set_cookie(
        key=VIEW_AS_COOKIE,
        value=token,
        max_age=VIEW_AS_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path=_cookie_path(),
    )


def clear_view_as(resp: Response) -> None:
    resp.delete_cookie(key=VIEW_AS_COOKIE, path=_cookie_path())


def read_view_as(request: Request) -> str | None:
    raw = request.cookies.get(VIEW_AS_COOKIE)
    if not raw:
        return None
    try:
        payload = _view_as_serializer().loads(raw, max_age=VIEW_AS_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    role = payload.get("role")
    return role if isinstance(role, str) else None
