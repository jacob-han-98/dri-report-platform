"""/api/reports — upload, list, get, delete.

Auth: relies on `X-User-Email` header set by /auth/check forward_auth.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import log_event
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Report, ReportViewer, User
from app.storage import detect_entry_point, extract_zip, remove_report, validate_slug

router = APIRouter(prefix="/api")
log = structlog.get_logger()


class ReportMeta(BaseModel):
    slug: str
    title: str
    description: str | None = None
    visibility: str = Field(default="restricted", pattern="^(internal|restricted)$")
    tags: list[str] | None = None
    entry_point: str | None = None


class ReportOut(BaseModel):
    slug: str
    title: str
    description: str | None
    owner_email: str
    visibility: str
    entry_point: str
    tags: list[str] | None
    size_bytes: int | None
    file_count: int | None
    created_at: datetime
    updated_at: datetime
    view_count: int
    url: str


def _resolve_base_url(request: Request, settings: Settings) -> str:
    """Prefer X-Forwarded-* from Caddy if present, else config.base_url."""
    proto = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if proto and host:
        return f"{proto}://{host}"
    return settings.app.base_url


def _actor_type(request: Request) -> str:
    """If a Bearer token reached forward_auth, the original request still has it.
    Caddy forwards Authorization upstream by default, so we can detect token use here."""
    auth = request.headers.get("authorization", "")
    return "token" if auth.lower().startswith("bearer ") else "session"


def _token_prefix(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()[:16]
    return None


def get_effective_role(request: Request, user: User) -> str:
    """Role for permission checks. Reads `X-User-Role` set by /auth/check
    (which applies view-as downgrade). Falls back to `user.role` for direct
    API calls that don't go through Caddy forward_auth.
    """
    return request.headers.get("x-user-role") or user.role


def _to_out(r: Report, base_url: str, web_prefix: str = "") -> ReportOut:
    return ReportOut(
        slug=r.slug,
        title=r.title,
        description=r.description,
        owner_email=r.owner_email,
        visibility=r.visibility,
        entry_point=r.entry_point,
        tags=json.loads(r.tags) if r.tags else None,
        size_bytes=r.size_bytes,
        file_count=r.file_count,
        created_at=r.created_at,
        updated_at=r.updated_at,
        view_count=r.view_count,
        url=f"{base_url.rstrip('/')}{web_prefix}/r/{r.slug}/",
    )


def require_user(
    db: Annotated[Session, Depends(get_db)],
    x_user_email: Annotated[str | None, Header()] = None,
) -> User:
    if not x_user_email:
        raise HTTPException(status_code=401, detail="missing X-User-Email")
    user = db.get(User, x_user_email)
    if user is None:
        # Auto-create — auth/check upserts on bypass; safety net for direct API calls
        user = User(email=x_user_email, role="user")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.post("/reports", status_code=201)
async def create_report(
    request: Request,
    file: Annotated[UploadFile, File()],
    meta: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(require_user)],
) -> ReportOut:
    try:
        meta_obj = ReportMeta.model_validate_json(meta)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid meta JSON: {e}") from e

    validate_slug(meta_obj.slug)

    # 409 if slug already exists
    existing = db.get(Report, meta_obj.slug)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"slug '{meta_obj.slug}' already exists",
        )

    max_bytes = settings.storage.max_upload_mb * 1024 * 1024
    tmp_path = await _save_upload_to_tmp(file, max_bytes)
    try:
        result = extract_zip(meta_obj.slug, tmp_path)
        entry = detect_entry_point(meta_obj.slug, meta_obj.entry_point)
    except HTTPException:
        # cleanup: remove anything extracted, surface error
        remove_report(meta_obj.slug)
        raise
    finally:
        tmp_path.unlink(missing_ok=True)

    storage_path = str((settings.reports_dir_path / meta_obj.slug).resolve())
    report = Report(
        slug=meta_obj.slug,
        title=meta_obj.title,
        description=meta_obj.description,
        owner_email=user.email,
        visibility=meta_obj.visibility,
        storage_path=storage_path,
        entry_point=entry,
        tags=json.dumps(meta_obj.tags) if meta_obj.tags else None,
        size_bytes=result["size_bytes"],
        file_count=result["file_count"],
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    log.info(
        "report.created",
        slug=report.slug,
        owner=report.owner_email,
        files=result["file_count"],
        size=result["size_bytes"],
    )
    log_event(
        db,
        action="report.create",
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=report.slug,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"file_count": result["file_count"], "size_bytes": result["size_bytes"]},
    )

    return _to_out(report, _resolve_base_url(request, settings), settings.app.web_prefix)


@router.get("/reports")
def list_reports(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(require_user)],
    owner: str | None = None,
    visibility: str | None = None,
    limit: int = 50,
) -> list[ReportOut]:
    stmt = select(Report).order_by(Report.updated_at.desc()).limit(limit)
    if owner:
        stmt = stmt.where(Report.owner_email == owner)
    if visibility:
        stmt = stmt.where(Report.visibility == visibility)

    rows = db.scalars(stmt).all()

    # admin sees everything; non-admin: own + viewers + internal
    eff_role = get_effective_role(request, user)
    if eff_role != "admin":
        viewer_slugs = set(
            db.scalars(
                select(ReportViewer.slug).where(ReportViewer.user_email == user.email)
            ).all()
        )
        rows = [
            r
            for r in rows
            if r.owner_email == user.email
            or r.visibility == "internal"
            or r.slug in viewer_slugs
        ]

    base_url = _resolve_base_url(request, settings)
    return [_to_out(r, base_url, settings.app.web_prefix) for r in rows]


@router.get("/reports/{slug}")
def get_report(
    slug: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(require_user)],
) -> ReportOut:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    if not _can_read(user, report, db, role=get_effective_role(request, user)):
        raise HTTPException(status_code=403, detail="forbidden")
    return _to_out(report, _resolve_base_url(request, settings), settings.app.web_prefix)


@router.delete("/reports/{slug}", status_code=204)
def delete_report(
    slug: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> None:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    _require_owner_or_admin(user, report, role=get_effective_role(request, user))
    db.delete(report)
    db.commit()
    remove_report(slug)
    log.info("report.deleted", slug=slug, by=user.email)
    log_event(
        db,
        action="report.delete",
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=slug,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


def _can_read(user: User, report: Report, db: Session, *, role: str | None = None) -> bool:
    eff_role = role or user.role
    if eff_role == "admin":
        return True
    if report.owner_email == user.email:
        return True
    if report.visibility == "internal":
        return True
    return any(v.user_email == user.email for v in report.viewers)


def _require_owner_or_admin(user: User, report: Report, *, role: str | None = None) -> None:
    eff_role = role or user.role
    if eff_role != "admin" and report.owner_email != user.email:
        raise HTTPException(status_code=403, detail="forbidden")


async def _save_upload_to_tmp(file: UploadFile, max_bytes: int) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        size = 0
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > max_bytes:
                tmp.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"upload exceeds {max_bytes // (1024 * 1024)}MB",
                )
            tmp.write(chunk)
    return tmp_path


def _ensure_user(db: Session, email: str) -> User:
    """Get or auto-create user (used when granting viewer access by email)."""
    user = db.get(User, email)
    if user is None:
        user = User(email=email, role="user")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


# ---------- PATCH (metadata) ----------


class ReportPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    visibility: str | None = Field(default=None, pattern="^(internal|restricted)$")
    tags: list[str] | None = None
    entry_point: str | None = None


@router.patch("/reports/{slug}")
def patch_report(
    slug: str,
    body: ReportPatch,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(require_user)],
) -> ReportOut:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    _require_owner_or_admin(user, report, role=get_effective_role(request, user))

    changed: dict[str, object] = {}
    if body.title is not None:
        report.title = body.title
        changed["title"] = body.title
    if body.description is not None:
        report.description = body.description
        changed["description"] = body.description
    if body.visibility is not None:
        report.visibility = body.visibility
        changed["visibility"] = body.visibility
    if body.tags is not None:
        report.tags = json.dumps(body.tags) if body.tags else None
        changed["tags"] = body.tags
    if body.entry_point is not None:
        # validate the new entry_point exists in storage
        entry = detect_entry_point(slug, body.entry_point)
        report.entry_point = entry
        changed["entry_point"] = entry

    report.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(report)

    log.info("report.updated", slug=slug, by=user.email, changed=list(changed.keys()))
    log_event(
        db,
        action="report.update",
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=slug,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"changed": list(changed.keys())},
    )
    return _to_out(report, _resolve_base_url(request, settings), settings.app.web_prefix)


# ---------- PUT (redeploy = overwrite zip, keep metadata) ----------


@router.put("/reports/{slug}")
async def put_report(
    slug: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(require_user)],
) -> ReportOut:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    _require_owner_or_admin(user, report, role=get_effective_role(request, user))

    max_bytes = settings.storage.max_upload_mb * 1024 * 1024
    tmp_path = await _save_upload_to_tmp(file, max_bytes)
    try:
        result = extract_zip(slug, tmp_path)
        # keep existing entry_point if it still resolves; else re-detect
        try:
            entry = detect_entry_point(slug, report.entry_point)
        except HTTPException:
            entry = detect_entry_point(slug, None)
    finally:
        tmp_path.unlink(missing_ok=True)

    report.entry_point = entry
    report.size_bytes = result["size_bytes"]
    report.file_count = result["file_count"]
    report.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(report)

    log.info(
        "report.redeployed",
        slug=slug,
        by=user.email,
        files=result["file_count"],
        size=result["size_bytes"],
    )
    log_event(
        db,
        action="report.redeploy",
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=slug,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"file_count": result["file_count"], "size_bytes": result["size_bytes"]},
    )
    return _to_out(report, _resolve_base_url(request, settings), settings.app.web_prefix)


# ---------- viewers ----------


class ViewerIn(BaseModel):
    user_email: str


class ViewerOut(BaseModel):
    user_email: str
    granted_by: str
    granted_at: datetime


@router.get("/reports/{slug}/viewers")
def list_viewers(
    slug: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> list[ViewerOut]:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    _require_owner_or_admin(user, report, role=get_effective_role(request, user))
    return [
        ViewerOut(user_email=v.user_email, granted_by=v.granted_by, granted_at=v.granted_at)
        for v in report.viewers
    ]


@router.post("/reports/{slug}/viewers", status_code=201)
def add_viewer(
    slug: str,
    body: ViewerIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> ViewerOut:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    _require_owner_or_admin(user, report, role=get_effective_role(request, user))

    _ensure_user(db, body.user_email)

    existing = (
        db.query(ReportViewer)
        .filter_by(slug=slug, user_email=body.user_email)
        .one_or_none()
    )
    if existing is not None:
        return ViewerOut(
            user_email=existing.user_email,
            granted_by=existing.granted_by,
            granted_at=existing.granted_at,
        )

    viewer = ReportViewer(slug=slug, user_email=body.user_email, granted_by=user.email)
    db.add(viewer)
    db.commit()
    db.refresh(viewer)

    log.info("viewer.added", slug=slug, viewer=body.user_email, by=user.email)
    log_event(
        db,
        action="viewer.add",
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=slug,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"viewer": body.user_email},
    )
    return ViewerOut(
        user_email=viewer.user_email,
        granted_by=viewer.granted_by,
        granted_at=viewer.granted_at,
    )


@router.delete("/reports/{slug}/viewers/{email}", status_code=204)
def remove_viewer(
    slug: str,
    email: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> None:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    _require_owner_or_admin(user, report, role=get_effective_role(request, user))

    viewer = (
        db.query(ReportViewer).filter_by(slug=slug, user_email=email).one_or_none()
    )
    if viewer is None:
        raise HTTPException(status_code=404, detail="viewer not found")
    db.delete(viewer)
    db.commit()

    log.info("viewer.removed", slug=slug, viewer=email, by=user.email)
    log_event(
        db,
        action="viewer.remove",
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=slug,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"viewer": email},
    )
