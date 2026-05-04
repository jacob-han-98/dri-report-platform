"""Front page + report detail + tokens management — server-rendered (Jinja2)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin import (
    _audit as admin_audit,
    _last_admin_protect,
    _self_protect,
    require_admin,
)
from app.api.reports import (
    _actor_type,
    _can_read,
    _ensure_user,
    _require_owner_or_admin,
    _token_prefix,
    get_effective_role,
    require_user,
)
from app.auth.session import clear_view_as, read_view_as, set_view_as
from app.audit import log_event
from app.auth.csrf import CSRF_FIELD, get_token as get_csrf_token
from app.auth.tokens import issue_token, revoke_token
from app.config import get_settings
from app.db import get_db
from app.models import ApiToken, AuditLog, Report, ReportViewer, User
from app.storage import remove_report

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


@pass_context
def _csrf_input(context) -> Markup:  # noqa: ANN001 - jinja ctx
    request: Request = context["request"]
    token = get_csrf_token(request)
    return Markup(
        f'<input type="hidden" name="{CSRF_FIELD}" value="{token}">'
    )


def _url(path: str) -> str:
    """Prefix `path` with the configured web_prefix.

    Pass internal paths as-is (e.g., "/upload"); returns "/dri_report/upload"
    in prod or "/upload" in dev. Use in templates: `{{ url("/upload") }}`.
    """
    prefix = get_settings().app.web_prefix
    if not path.startswith("/"):
        path = "/" + path
    return f"{prefix}{path}"


templates.env.globals["csrf_input"] = _csrf_input
templates.env.globals["url"] = _url


def _decode_tags(s: str | None) -> list[str]:
    if not s:
        return []
    try:
        out = json.loads(s)
        return out if isinstance(out, list) else []
    except json.JSONDecodeError:
        return []


def _humanize_size(n: int | None) -> str:
    if not n:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def _humanize_when(ts: datetime | None) -> str:
    if ts is None:
        return "-"
    delta = datetime.utcnow() - (ts.replace(tzinfo=None) if ts.tzinfo else ts)
    s = int(delta.total_seconds())
    if s < 60:
        return "방금"
    if s < 3600:
        return f"{s // 60}분 전"
    if s < 86400:
        return f"{s // 3600}시간 전"
    if s < 86400 * 30:
        return f"{s // 86400}일 전"
    return ts.strftime("%Y-%m-%d")


def _user_ctx(request: Request, user: User) -> dict:
    """Common per-request context for templates: includes view-as awareness."""
    eff_role = get_effective_role(request, user)
    return {
        "email": user.email,
        "role": eff_role,
        "actual_role": user.role,
        "view_as_active": eff_role != user.role,
    }


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
    q: str | None = None,
    filter: str | None = None,  # 'mine' | 'shared' | None
) -> HTMLResponse:
    stmt = select(Report).order_by(Report.updated_at.desc())
    rows = list(db.scalars(stmt).all())

    # permission filter (admin sees all)
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

    if filter == "mine":
        rows = [r for r in rows if r.owner_email == user.email]
    elif filter == "shared":
        rows = [r for r in rows if r.owner_email != user.email]

    if q:
        ql = q.lower()
        rows = [
            r
            for r in rows
            if ql in r.title.lower()
            or ql in (r.description or "").lower()
            or ql in r.slug.lower()
            or any(ql in t.lower() for t in _decode_tags(r.tags))
        ]

    cards = [
        {
            "slug": r.slug,
            "title": r.title,
            "description": r.description,
            "owner": r.owner_email,
            "visibility": r.visibility,
            "tags": _decode_tags(r.tags),
            "size": _humanize_size(r.size_bytes),
            "updated": _humanize_when(r.updated_at),
            "view_count": r.view_count,
            "url": _url(f"/r/{r.slug}/"),
            "manage_url": _url(f"/reports/{r.slug}"),
            "is_owner": r.owner_email == user.email,
        }
        for r in rows
    ]

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user": _user_ctx(request, user),
            "cards": cards,
            "q": q or "",
            "filter": filter or "",
            "total": len(cards),
        },
    )


@router.get("/reports/{slug}", response_class=HTMLResponse)
def report_detail(
    slug: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> HTMLResponse:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    eff_role = get_effective_role(request, user)
    if not _can_read(user, report, db, role=eff_role):
        raise HTTPException(status_code=403, detail="forbidden")

    is_owner = report.owner_email == user.email
    is_admin = eff_role == "admin"
    can_manage = is_owner or is_admin

    viewers = list(report.viewers) if can_manage else []

    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "user": _user_ctx(request, user),
            "report": {
                "slug": report.slug,
                "title": report.title,
                "description": report.description,
                "owner": report.owner_email,
                "visibility": report.visibility,
                "entry_point": report.entry_point,
                "tags": _decode_tags(report.tags),
                "size": _humanize_size(report.size_bytes),
                "file_count": report.file_count,
                "created_at": report.created_at,
                "updated_at": report.updated_at,
                "view_count": report.view_count,
                "url": _url(f"/r/{report.slug}/"),
            },
            "viewers": [
                {
                    "user_email": v.user_email,
                    "granted_by": v.granted_by,
                    "granted_at": v.granted_at,
                }
                for v in viewers
            ],
            "can_manage": can_manage,
        },
    )


def _list_tokens(db: Session, user: User) -> list[dict]:
    rows = (
        db.query(ApiToken)
        .filter(ApiToken.user_email == user.email)
        .order_by(ApiToken.created_at.desc())
        .all()
    )
    return [
        {
            "prefix": r.prefix,
            "name": r.name,
            "expires_at": r.expires_at,
            "last_used_at": r.last_used_at,
            "revoked_at": r.revoked_at,
            "created_at": r.created_at,
            "active": r.revoked_at is None,
        }
        for r in rows
    ]


def _render_tokens(
    request: Request,
    user: User,
    tokens: list[dict],
    *,
    new_token: dict | None = None,
    error: str | None = None,
    form: dict | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "tokens.html",
        {
            "user": _user_ctx(request, user),
            "tokens": tokens,
            "new_token": new_token,
            "error": error,
            "form": form or {},
        },
    )


@router.get("/settings/tokens", response_class=HTMLResponse)
def tokens_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> HTMLResponse:
    return _render_tokens(request, user, _list_tokens(db, user))


@router.post("/settings/tokens/issue", response_class=HTMLResponse)
def tokens_issue(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form()],
    expires_in_days: Annotated[int | None, Form()] = None,
) -> HTMLResponse:
    name = name.strip()
    if not name:
        return _render_tokens(
            request, user, _list_tokens(db, user),
            error="이름을 입력해주세요.",
            form={"name": name, "expires_in_days": expires_in_days},
        )
    try:
        issued = issue_token(
            db, user=user, name=name, expires_in_days=expires_in_days
        )
    except ValueError as e:
        return _render_tokens(
            request, user, _list_tokens(db, user),
            error=str(e),
            form={"name": name, "expires_in_days": expires_in_days},
        )

    log_event(
        db,
        action="token.issue",
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=issued.prefix,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"name": name, "via": "ui"},
    )

    return _render_tokens(
        request, user, _list_tokens(db, user),
        new_token={
            "plaintext": issued.plaintext,
            "prefix": issued.prefix,
            "name": name,
            "expires_at": issued.expires_at,
        },
    )


@router.post("/settings/tokens/{prefix}/revoke")
def tokens_revoke(
    prefix: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    revoked = revoke_token(db, prefix=prefix, user=user)
    if revoked:
        log_event(
            db,
            action="token.revoke",
            actor_type=_actor_type(request),
            user_email=user.email,
            token_prefix=_token_prefix(request),
            resource=prefix,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            metadata={"via": "ui"},
        )
    return RedirectResponse(url=_url("/settings/tokens"), status_code=303)


# ---------- upload (browser form) ----------


def _slug_from_filename(name: str) -> str:
    """Best-effort slug from an upload filename. Lowercase, dashes, strip ext."""
    base = Path(name).stem.lower()
    safe = "".join(ch if (ch.isalnum() or ch == "-") else "-" for ch in base)
    safe = "-".join(p for p in safe.split("-") if p)  # collapse repeats / strip ends
    return safe[:64] or "report"


@router.get("/upload", response_class=HTMLResponse)
def upload_page(
    request: Request,
    user: Annotated[User, Depends(require_user)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "upload.html",
        {
            "user": _user_ctx(request, user),
            "form": {},
            "error": None,
        },
    )


@router.post("/upload", response_model=None)
async def upload_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    # Imported here to avoid circular imports during module load
    from app.api.reports import _save_upload_to_tmp
    from app.config import get_settings as _get_settings
    from app.storage import (
        StorageError,
        detect_entry_point,
        extract_zip,
        remove_report,
        validate_slug,
    )

    settings = _get_settings()

    form = await request.form()
    file = form.get("file")
    slug = (form.get("slug") or "").strip().lower()
    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip() or None
    visibility = (form.get("visibility") or "restricted").strip()
    tags_raw = (form.get("tags") or "").strip()

    def _err(msg: str, status_code: int = 400):
        return templates.TemplateResponse(
            request,
            "upload.html",
            {
                "user": _user_ctx(request, user),
                "form": {
                    "slug": slug,
                    "title": title,
                    "description": description or "",
                    "visibility": visibility,
                    "tags": tags_raw,
                },
                "error": msg,
            },
            status_code=status_code,
        )

    if file is None or not getattr(file, "filename", None):
        return _err("zip 파일을 선택해주세요.")
    if visibility not in ("internal", "restricted"):
        return _err("visibility 는 internal 또는 restricted")

    if not slug:
        slug = _slug_from_filename(file.filename)
    if not title:
        title = slug

    try:
        validate_slug(slug)
    except StorageError as e:
        return _err(f"잘못된 slug: {e.detail}")

    if db.get(Report, slug) is not None:
        return _err(
            f"slug '{slug}' 가 이미 존재합니다. 다른 이름을 쓰거나 redeploy 사용.",
            status_code=409,
        )

    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else None

    max_bytes = settings.storage.max_upload_mb * 1024 * 1024
    try:
        tmp_path = await _save_upload_to_tmp(file, max_bytes)
    except HTTPException as e:
        return _err(f"업로드 실패: {e.detail}", status_code=e.status_code)

    try:
        try:
            result = extract_zip(slug, tmp_path)
            entry = detect_entry_point(slug, None)
        except StorageError as e:
            remove_report(slug)
            return _err(f"zip 처리 실패: {e.detail}", status_code=e.status_code)
    finally:
        tmp_path.unlink(missing_ok=True)

    storage_path = str((settings.reports_dir_path / slug).resolve())
    report = Report(
        slug=slug,
        title=title,
        description=description,
        owner_email=user.email,
        visibility=visibility,
        storage_path=storage_path,
        entry_point=entry,
        tags=json.dumps(tags) if tags else None,
        size_bytes=result["size_bytes"],
        file_count=result["file_count"],
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    log_event(
        db,
        action="report.create",
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=slug,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={
            "file_count": result["file_count"],
            "size_bytes": result["size_bytes"],
            "via": "web",
        },
    )
    return _redirect(f"/reports/{slug}")


# ---------- form handlers (Slice 6) ----------


def _redirect(target: str) -> Response:
    # 303 = browser uses GET to follow, so the redirected page is fresh.
    # Prefix with web_prefix so the Location header points at the right path
    # behind a reverse-proxy subpath mount.
    return RedirectResponse(_url(target), status_code=303)


def _audit_owner_action(
    db: Session,
    request: Request,
    user: User,
    *,
    action: str,
    slug: str,
    metadata: dict | None = None,
) -> None:
    log_event(
        db,
        action=action,
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=slug,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata=metadata,
    )


@router.post("/reports/{slug}/viewers/add")
def web_viewer_add(
    slug: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
    email: Annotated[str, Form()],
) -> Response:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    _require_owner_or_admin(user, report)

    email = email.strip()
    if not email:
        raise HTTPException(status_code=400, detail="email required")

    _ensure_user(db, email)

    existing = (
        db.query(ReportViewer)
        .filter_by(slug=slug, user_email=email)
        .one_or_none()
    )
    if existing is None:
        db.add(ReportViewer(slug=slug, user_email=email, granted_by=user.email))
        db.commit()
        _audit_owner_action(
            db, request, user, action="viewer.add", slug=slug,
            metadata={"viewer": email},
        )
    return _redirect(f"/reports/{slug}")


@router.post("/reports/{slug}/viewers/remove")
def web_viewer_remove(
    slug: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
    email: Annotated[str, Form()],
) -> Response:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    _require_owner_or_admin(user, report)

    viewer = (
        db.query(ReportViewer).filter_by(slug=slug, user_email=email).one_or_none()
    )
    if viewer is not None:
        db.delete(viewer)
        db.commit()
        _audit_owner_action(
            db, request, user, action="viewer.remove", slug=slug,
            metadata={"viewer": email},
        )
    return _redirect(f"/reports/{slug}")


@router.post("/reports/{slug}/visibility")
def web_visibility(
    slug: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
    visibility: Annotated[str, Form()],
) -> Response:
    if visibility not in ("internal", "restricted"):
        raise HTTPException(status_code=400, detail="invalid visibility")

    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    _require_owner_or_admin(user, report)

    if report.visibility != visibility:
        report.visibility = visibility
        report.updated_at = datetime.utcnow()
        db.commit()
        _audit_owner_action(
            db, request, user, action="report.update", slug=slug,
            metadata={"changed": ["visibility"], "visibility": visibility},
        )
    return _redirect(f"/reports/{slug}")


@router.post("/reports/{slug}/delete")
def web_delete(
    slug: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> Response:
    report = db.get(Report, slug)
    if report is None:
        raise HTTPException(status_code=404, detail="not found")
    _require_owner_or_admin(user, report)

    db.delete(report)
    db.commit()
    remove_report(slug)
    _audit_owner_action(db, request, user, action="report.delete", slug=slug)
    return _redirect("/")


# ---------- admin pages (Slice 6.5 / scope C) ----------


def _user_status(u: User) -> str:
    if u.disabled_at is not None:
        return "disabled"
    if u.last_login_at is None:
        return "pending"
    return "active"


@router.get("/settings/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
) -> HTMLResponse:
    from sqlalchemy import func

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
    users = [
        {
            "email": u.email,
            "role": u.role,
            "status": _user_status(u),
            "display_name": u.display_name,
            "last_login_at": u.last_login_at,
            "disabled_at": u.disabled_at,
            "invited_at": u.invited_at,
            "invited_by": u.invited_by,
            "created_at": u.created_at,
            "report_count": int(counts.get(u.email, 0)),
            "is_self": u.email == actor.email,
        }
        for u in rows
    ]
    return templates.TemplateResponse(
        request,
        "users.html",
        {"user": _user_ctx(request, actor), "users": users},
    )


@router.post("/settings/users/invite")
def web_invite(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
    email: Annotated[str, Form()],
    role: Annotated[str, Form()] = "user",
) -> Response:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="invalid email")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="invalid role")
    existing = db.get(User, email)
    if existing is not None:
        # idempotent: do nothing, just go back
        return _redirect("/settings/users")
    db.add(
        User(
            email=email,
            role=role,
            invited_at=datetime.utcnow(),
            invited_by=actor.email,
        )
    )
    db.commit()
    admin_audit(db, request, actor, action="user.invite", resource=email,
                metadata={"role": role})
    return _redirect("/settings/users")


@router.post("/settings/users/{email}/role")
def web_set_role(
    email: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
    role: Annotated[str, Form()],
) -> Response:
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="invalid role")
    target = db.get(User, email)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    _self_protect(actor, email, action="role 변경")
    _last_admin_protect(db, target, role_after=role, disable=False)
    if target.role != role:
        target.role = role
        db.commit()
        admin_audit(db, request, actor, action="user.role", resource=email,
                    metadata={"role": role})
    return _redirect("/settings/users")


@router.post("/settings/users/{email}/disable")
def web_disable(
    email: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
) -> Response:
    target = db.get(User, email)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    _self_protect(actor, email, action="비활성화")
    _last_admin_protect(db, target, role_after=target.role, disable=True)
    if target.disabled_at is None:
        target.disabled_at = datetime.utcnow()
        db.commit()
        admin_audit(db, request, actor, action="user.disable", resource=email)
    return _redirect("/settings/users")


@router.post("/settings/users/{email}/enable")
def web_enable(
    email: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
) -> Response:
    target = db.get(User, email)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    if target.disabled_at is not None:
        target.disabled_at = None
        db.commit()
        admin_audit(db, request, actor, action="user.enable", resource=email)
    return _redirect("/settings/users")


@router.get("/settings/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    actor: Annotated[User, Depends(require_admin)],
    action: str | None = None,
    user: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    page_size = 50
    page = max(1, page)
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if user:
        stmt = stmt.where(AuditLog.user_email == user)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = db.scalars(stmt).all()

    distinct_actions = sorted(
        set(db.scalars(select(AuditLog.action).distinct()).all()) or set()
    )

    entries = []
    for r in rows:
        meta = None
        if r.metadata_json:
            try:
                meta = json.loads(r.metadata_json)
            except Exception:
                meta = None
        entries.append(
            {
                "id": r.id,
                "timestamp": r.timestamp,
                "action": r.action,
                "actor_type": r.actor_type,
                "user_email": r.user_email,
                "token_prefix": r.token_prefix,
                "resource": r.resource,
                "ip": r.ip,
                "metadata": meta,
            }
        )
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "user": _user_ctx(request, actor),
            "entries": entries,
            "actions": distinct_actions,
            "filter_action": action or "",
            "filter_user": user or "",
            "page": page,
            "has_next": len(entries) == page_size,
        },
    )


# ---------- view-as (admin → user UX simulation) ----------


def _safe_redirect(request: Request, fallback: str = "/") -> Response:
    """303 to the Referer if it's same-origin, else fallback (web_prefix-aware)."""
    ref = request.headers.get("referer")
    if ref:
        # only follow same-origin referers (avoid open-redirect)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if host and (ref.startswith("/") or f"//{host}" in ref):
            # ref is already a full URL (or absolute path) — don't re-prefix
            return RedirectResponse(ref, status_code=303)
    return _redirect(fallback)


@router.post("/settings/view-as")
def web_view_as(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
    role: Annotated[str, Form()],
) -> Response:
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="invalid role")

    # only actual admins may downgrade themselves
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")

    resp = _safe_redirect(request, "/")
    if role == "user":
        set_view_as(resp, "user")
        log_event(
            db, action="user.view_as_set", actor_type="session",
            user_email=user.email,
            ip=request.client.host if request.client else None,
            metadata={"role": "user"},
        )
    else:
        clear_view_as(resp)
        log_event(
            db, action="user.view_as_clear", actor_type="session",
            user_email=user.email,
            ip=request.client.host if request.client else None,
        )
    return resp


@router.post("/settings/view-as/clear")
def web_view_as_clear(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> Response:
    resp = _safe_redirect(request, "/")
    clear_view_as(resp)
    if read_view_as(request) is not None:
        log_event(
            db, action="user.view_as_clear", actor_type="session",
            user_email=user.email,
            ip=request.client.host if request.client else None,
        )
    return resp
