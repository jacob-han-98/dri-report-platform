"""/api/admin/* — users 관리 + audit log 조회. admin 권한 필수."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.reports import _actor_type, _token_prefix, get_effective_role, require_user
from app.audit import log_event
from app.db import get_db
from app.models import AuditLog, Report, User

router = APIRouter(prefix="/api/admin")
log = structlog.get_logger()


def require_admin(
    request: Request,
    user: Annotated[User, Depends(require_user)],
) -> User:
    if get_effective_role(request, user) != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user


def _status(u: User) -> str:
    if u.disabled_at is not None:
        return "disabled"
    if u.last_login_at is None:
        return "pending"
    return "active"


class UserOut(BaseModel):
    email: str
    role: str
    status: str
    display_name: str | None
    google_sub: str | None
    last_login_at: datetime | None
    disabled_at: datetime | None
    invited_at: datetime | None
    invited_by: str | None
    created_at: datetime
    report_count: int


class InviteIn(BaseModel):
    email: EmailStr
    role: Literal["admin", "user"] = "user"


class RoleIn(BaseModel):
    role: Literal["admin", "user"]


def _audit(
    db: Session,
    request: Request,
    actor: User,
    *,
    action: str,
    resource: str | None = None,
    metadata: dict | None = None,
) -> None:
    log_event(
        db,
        action=action,
        actor_type=_actor_type(request),
        user_email=actor.email,
        token_prefix=_token_prefix(request),
        resource=resource,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata=metadata,
    )


def _count_admins(db: Session, *, active_only: bool = True) -> int:
    stmt = select(func.count(User.email)).where(User.role == "admin")
    if active_only:
        stmt = stmt.where(User.disabled_at.is_(None))
    return int(db.scalar(stmt) or 0)


def _self_protect(actor: User, target_email: str, *, action: str) -> None:
    if actor.email == target_email:
        raise HTTPException(
            status_code=400,
            detail=f"본인을 {action} 할 수 없습니다 (잠금 방지)",
        )


def _last_admin_protect(db: Session, target: User, *, role_after: str | None, disable: bool) -> None:
    """Guard against demoting/disabling the last active admin."""
    if target.role != "admin" or target.disabled_at is not None:
        return  # not an active admin
    if role_after == "admin" and not disable:
        return  # no-op for admin status
    if _count_admins(db, active_only=True) <= 1:
        raise HTTPException(
            status_code=400,
            detail="마지막 활성 admin 입니다 — demote/disable 불가",
        )


# ---------- list / detail ----------


@router.get("/users")
def list_users(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> list[UserOut]:
    # report_count via a left-join group-by
    counts = dict(
        db.execute(
            select(Report.owner_email, func.count(Report.slug)).group_by(Report.owner_email)
        ).all()
    )
    rows = (
        db.query(User)
        .order_by(User.disabled_at.is_(None).desc(), User.last_login_at.desc().nulls_last())
        .all()
    )
    return [
        UserOut(
            email=u.email,
            role=u.role,
            status=_status(u),
            display_name=u.display_name,
            google_sub=u.google_sub,
            last_login_at=u.last_login_at,
            disabled_at=u.disabled_at,
            invited_at=u.invited_at,
            invited_by=u.invited_by,
            created_at=u.created_at,
            report_count=int(counts.get(u.email, 0)),
        )
        for u in rows
    ]


# ---------- invite ----------


@router.post("/users/invite", status_code=201)
def invite(
    body: InviteIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
) -> UserOut:
    email = body.email.lower()
    existing = db.get(User, email)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"이미 등록된 사용자: {email}",
        )
    user = User(
        email=email,
        role=body.role,
        invited_at=datetime.utcnow(),
        invited_by=actor.email,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    _audit(db, request, actor, action="user.invite", resource=email, metadata={"role": body.role})
    return UserOut(
        email=user.email, role=user.role, status=_status(user),
        display_name=None, google_sub=None,
        last_login_at=None, disabled_at=None,
        invited_at=user.invited_at, invited_by=user.invited_by,
        created_at=user.created_at, report_count=0,
    )


# ---------- role / disable / enable ----------


@router.patch("/users/{email}")
def patch_user_role(
    email: str,
    body: RoleIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
) -> UserOut:
    target = db.get(User, email)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    _self_protect(actor, email, action="role 변경")
    _last_admin_protect(db, target, role_after=body.role, disable=False)

    if target.role != body.role:
        target.role = body.role
        db.commit()
        _audit(db, request, actor, action="user.role", resource=email,
               metadata={"role": body.role})
    return _to_out(db, target)


@router.post("/users/{email}/disable", status_code=200)
def disable_user(
    email: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
) -> UserOut:
    target = db.get(User, email)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    _self_protect(actor, email, action="비활성화")
    _last_admin_protect(db, target, role_after=target.role, disable=True)

    if target.disabled_at is None:
        target.disabled_at = datetime.utcnow()
        db.commit()
        _audit(db, request, actor, action="user.disable", resource=email)
    return _to_out(db, target)


@router.post("/users/{email}/enable", status_code=200)
def enable_user(
    email: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
) -> UserOut:
    target = db.get(User, email)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    if target.disabled_at is not None:
        target.disabled_at = None
        db.commit()
        _audit(db, request, actor, action="user.enable", resource=email)
    return _to_out(db, target)


def _to_out(db: Session, u: User) -> UserOut:
    cnt = (
        db.scalar(select(func.count(Report.slug)).where(Report.owner_email == u.email)) or 0
    )
    return UserOut(
        email=u.email, role=u.role, status=_status(u),
        display_name=u.display_name, google_sub=u.google_sub,
        last_login_at=u.last_login_at, disabled_at=u.disabled_at,
        invited_at=u.invited_at, invited_by=u.invited_by,
        created_at=u.created_at, report_count=int(cnt),
    )


# ---------- audit logs ----------


class AuditLogOut(BaseModel):
    id: int
    timestamp: datetime
    action: str
    actor_type: str
    user_email: str | None
    token_prefix: str | None
    resource: str | None
    ip: str | None
    metadata: dict | None = None


@router.get("/audit-logs")
def list_audit_logs(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
    action: str | None = None,
    user: str | None = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 100,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> list[AuditLogOut]:
    import json as _json

    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if user:
        stmt = stmt.where(AuditLog.user_email == user)
    stmt = stmt.offset(offset).limit(limit)
    rows = db.scalars(stmt).all()
    out: list[AuditLogOut] = []
    for r in rows:
        meta = None
        if r.metadata_json:
            try:
                meta = _json.loads(r.metadata_json)
            except Exception:
                meta = None
        out.append(
            AuditLogOut(
                id=r.id,
                timestamp=r.timestamp,
                action=r.action,
                actor_type=r.actor_type,
                user_email=r.user_email,
                token_prefix=r.token_prefix,
                resource=r.resource,
                ip=r.ip,
                metadata=meta,
            )
        )
    return out
