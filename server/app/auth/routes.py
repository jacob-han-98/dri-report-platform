"""/auth/login, /auth/callback, /auth/logout, /auth/me — browser OIDC flow."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.audit import log_event
from app.auth.oidc import get_oauth, is_configured
from app.auth.session import (
    clear_session_cookie,
    read_session,
    set_session_cookie,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.models import User

router = APIRouter()
log = structlog.get_logger()


def _is_admin_email(email: str, settings: Settings) -> bool:
    return email in (settings.google.admin_emails or [])


def _upsert_oidc_user(
    db: DBSession,
    *,
    email: str,
    sub: str | None,
    name: str | None,
    settings: Settings,
) -> User:
    user = db.get(User, email)
    role = "admin" if _is_admin_email(email, settings) else "user"
    if user is None:
        user = User(
            email=email, google_sub=sub, display_name=name, role=role, last_login_at=datetime.utcnow()
        )
        db.add(user)
    else:
        if sub and not user.google_sub:
            user.google_sub = sub
        if name and not user.display_name:
            user.display_name = name
        # admin_emails 가 변경되면 그 결과를 반영하되, admin → user 강등은 하지 않음
        # (admin 화면에서 명시적으로 변경하도록)
        if role == "admin" and user.role != "admin":
            user.role = "admin"
        user.last_login_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return user


@router.get("/auth/login", response_model=None)
async def login(request: Request):
    if not is_configured():
        return HTMLResponse(
            "<h1>OIDC not configured</h1>"
            "<p><code>config.toml [google]</code> 의 client_id/client_secret 가 비어있어. "
            "dev bypass 만 쓰는 환경이면 이 화면을 볼 일이 없음.</p>",
            status_code=501,
        )
    oauth = get_oauth()
    # url_for honors FastAPI's root_path → produces e.g. https://host/dri_report/auth/callback
    callback_url = str(request.url_for("oidc_callback"))
    return await oauth.google.authorize_redirect(request, callback_url)


@router.get("/auth/callback", name="oidc_callback", response_model=None)
async def callback(
    request: Request,
    db: Annotated[DBSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    oauth = get_oauth()
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        log.warning("oidc.callback.token_error", error=str(e))
        login_url = (settings.app.web_prefix or "") + "/auth/login"
        return HTMLResponse(
            f"<h1>로그인 실패</h1><pre>{e}</pre>"
            f"<p><a href='{login_url}'>다시 시도</a></p>",
            status_code=400,
        )

    userinfo = token.get("userinfo")
    if not userinfo or "email" not in userinfo:
        return HTMLResponse("<h1>userinfo 없음</h1>", status_code=400)

    email: str = userinfo["email"]
    sub: str | None = userinfo.get("sub")
    name: str | None = userinfo.get("name")
    hd: str | None = userinfo.get("hd")  # G Suite domain hint

    if settings.google.allowed_domain and hd != settings.google.allowed_domain:
        log.warning("oidc.domain_mismatch", email=email, hd=hd)
        return HTMLResponse(
            f"<h1>도메인 거부</h1><p>{hd or '(no hd)'} 는 허용된 도메인이 아님.</p>",
            status_code=403,
        )

    user = _upsert_oidc_user(db, email=email, sub=sub, name=name, settings=settings)

    if user.disabled_at is not None:
        log.warning("oidc.login.disabled", email=email)
        log_event(
            db,
            action="auth.login.denied",
            actor_type="session",
            user_email=email,
            ip=request.client.host if request.client else None,
            metadata={"reason": "disabled"},
        )
        return HTMLResponse(
            "<h1>계정이 비활성화됨</h1><p>관리자에게 문의하세요.</p>",
            status_code=403,
        )

    # build redirect response with session cookie set
    home = (settings.app.web_prefix or "") + "/"
    resp = RedirectResponse(home, status_code=303)
    set_session_cookie(resp, email=user.email, google_sub=user.google_sub)

    log.info("oidc.login.success", email=email, role=user.role)
    log_event(
        db,
        action="auth.login",
        actor_type="session",
        user_email=email,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"role": user.role, "via": "google"},
    )
    return resp


@router.get("/auth/logout")
def logout(request: Request, db: Annotated[DBSession, Depends(get_db)]) -> RedirectResponse:
    sess = read_session(request)
    home = (get_settings().app.web_prefix or "") + "/"
    resp = RedirectResponse(home, status_code=303)
    clear_session_cookie(resp)
    if sess:
        log_event(
            db,
            action="auth.logout",
            actor_type="session",
            user_email=sess.email,
            ip=request.client.host if request.client else None,
        )
    return resp


@router.get("/auth/me")
def me(request: Request, db: Annotated[DBSession, Depends(get_db)]):
    sess = read_session(request)
    if not sess:
        return JSONResponse({"authenticated": False}, status_code=401)
    user = db.get(User, sess.email)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return {
        "authenticated": True,
        "email": user.email,
        "role": user.role,
        "display_name": user.display_name,
        "google_sub": user.google_sub,
    }
