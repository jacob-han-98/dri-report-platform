"""/auth/check — forward_auth endpoint for Caddy.

Auth resolution order:
  1) Authorization: Bearer hybe_pat_... (skill / MCP / API client)
  2) Browser session cookie (Google OIDC)
  3) dev bypass (config.toml [dev] bypass_auth_email)
  Otherwise → 401.

Slug-level authorization: if the original URI is /r/{slug}/..., additional
visibility/owner/viewers checks happen after authentication.
"""
from __future__ import annotations

import re

import structlog
from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from app.audit import log_event
from app.auth.session import read_session, read_view_as
from app.auth.tokens import verify_token
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Report, ReportViewer, User

router = APIRouter()
log = structlog.get_logger()

# /r/<slug>/...  — slug must match the same charset as storage.SLUG_RE
_R_SLUG_RE = re.compile(r"^/r/([a-z0-9][a-z0-9\-]{1,62}[a-z0-9])(?:/|$)")


def _upsert_user(db: Session, email: str, role: str) -> User:
    user = db.get(User, email)
    if user is None:
        user = User(email=email, role=role)
        db.add(user)
    elif user.role != role:
        user.role = role
    db.commit()
    db.refresh(user)
    return user


def _ok(email: str, role: str) -> Response:
    return Response(
        status_code=200,
        headers={"X-User-Email": email, "X-User-Role": role},
    )


def _forbidden(reason: str) -> Response:
    return Response(status_code=403, content=reason, media_type="text/plain")


def _unauthorized(reason: str) -> Response:
    return Response(status_code=401, content=reason, media_type="text/plain")


def _extract_report_slug(uri: str | None) -> str | None:
    if not uri:
        return None
    # strip query string
    path = uri.split("?", 1)[0]
    m = _R_SLUG_RE.match(path)
    return m.group(1) if m else None


def _can_read_report(
    db: Session, user: User, slug: str, *, effective_role: str | None = None
) -> bool | None:
    """Returns True/False if report exists; None if report not found (404 path).

    `effective_role` lets the caller downgrade an admin to 'user' for view-as
    simulation. Defaults to the actual user.role.
    """
    report = db.get(Report, slug)
    if report is None:
        return None
    role = effective_role or user.role
    if role == "admin":
        return True
    if report.owner_email == user.email:
        return True
    if report.visibility == "internal":
        return True
    viewer = (
        db.query(ReportViewer)
        .filter_by(slug=slug, user_email=user.email)
        .first()
    )
    return viewer is not None


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@router.get("/auth/check")
def auth_check(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_forwarded_uri: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Response:
    user: User | None = None
    token_prefix: str | None = None
    actor_type = "session"

    # 1) Bearer token (skill / MCP / API client) — most explicit, takes precedence
    if authorization and authorization.lower().startswith("bearer "):
        plaintext = authorization[7:].strip()
        ip = _client_ip(request)
        user = verify_token(db, plaintext, ip=ip)
        if user is None:
            log.info("auth.check.token_invalid", uri=x_forwarded_uri)
            return _unauthorized("invalid or expired token")
        token_prefix = plaintext[:16]
        actor_type = "token"

    # 2) Browser session cookie (Google OIDC)
    if user is None:
        sess = read_session(request)
        if sess is not None:
            user = db.get(User, sess.email)
            if user is None:
                # session cookie references a user that no longer exists; treat as no auth
                log.info("auth.check.session_user_missing", email=sess.email)
            else:
                actor_type = "session"

    # 3) dev bypass (last resort — local dev when nothing else matches)
    if user is None:
        bypass_email = settings.dev.bypass_auth_email.strip()
        if bypass_email:
            user = _upsert_user(
                db, email=bypass_email, role=settings.dev.bypass_auth_role
            )
            actor_type = "session"
        else:
            return _unauthorized("not authenticated")

    # Disabled account guard — applies regardless of how the user authenticated.
    if user.disabled_at is not None:
        log.info("auth.check.disabled", email=user.email, actor=actor_type)
        return _unauthorized("account disabled")

    # view-as simulation: admins on a browser session may downgrade themselves
    # to 'user' for UX testing. Bearer/API tokens are not affected.
    effective_role = user.role
    if actor_type == "session" and user.role == "admin":
        view_as = read_view_as(request)
        if view_as == "user":
            effective_role = "user"

    # 2) authorize for /r/{slug}/...
    slug = _extract_report_slug(x_forwarded_uri)
    if slug is not None:
        allowed = _can_read_report(db, user, slug, effective_role=effective_role)
        if allowed is False:
            log.info(
                "auth.check.forbidden",
                email=user.email,
                slug=slug,
                actor=actor_type,
            )
            log_event(
                db,
                action="report.view.denied",
                actor_type=actor_type,
                user_email=user.email,
                token_prefix=token_prefix,
                resource=slug,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            return _forbidden("forbidden")
        log.info(
            "auth.check.report",
            email=user.email,
            slug=slug,
            allowed=bool(allowed),
            actor=actor_type,
        )
        if allowed is True:
            log_event(
                db,
                action="report.view",
                actor_type=actor_type,
                user_email=user.email,
                token_prefix=token_prefix,
                resource=slug,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        return _ok(user.email, effective_role)

    log.info(
        "auth.check.api",
        email=user.email,
        uri=x_forwarded_uri,
        actor=actor_type,
        effective_role=effective_role,
    )
    return _ok(user.email, effective_role)
