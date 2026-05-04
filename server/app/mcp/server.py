"""MCP server — 5 tools for searching, reading, and listing reports.

Auth: Caddy forward_auth populates X-User-Email/X-User-Role; ASGI middleware
in app.main bridges those into mcp.context.McpUser before tool dispatch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import or_, select

from app.api.reports import _can_read
from app.audit import log_event
from app.config import get_settings
from app.db import SessionLocal
from app.mcp.context import current as current_user
from app.models import AuditLog, Report, ReportViewer, User

mcp = FastMCP(
    name="hybe-reports",
    instructions=(
        "Search, fetch, and inspect HTML reports stored in Hybe Reports. "
        "Reports are identified by a `slug` (URL-safe). Use list_my_reports / "
        "search_reports to discover; get_report_metadata for details; "
        "fetch_report to read HTML/text content. recent_activity returns "
        "audit events visible to the caller."
    ),
    # Mounted at /mcp in app.main, so the inner streamable HTTP path is /.
    streamable_http_path="/",
)


def _decode_tags(s: str | None) -> list[str]:
    import json
    if not s:
        return []
    try:
        out = json.loads(s)
        return out if isinstance(out, list) else []
    except json.JSONDecodeError:
        return []


def _serialize_report(r: Report, viewer_emails: list[str] | None = None) -> dict[str, Any]:
    return {
        "slug": r.slug,
        "title": r.title,
        "description": r.description,
        "owner_email": r.owner_email,
        "visibility": r.visibility,
        "tags": _decode_tags(r.tags),
        "entry_point": r.entry_point,
        "size_bytes": r.size_bytes,
        "file_count": r.file_count,
        "view_count": r.view_count,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "viewers": viewer_emails,
        "url_path": f"{get_settings().app.web_prefix}/r/{r.slug}/",
    }


def _user_record(db, email: str) -> User:
    """Re-hydrate the current user from DB. Falls back to a transient User
    object so admin-but-not-yet-row scenarios still work (mirrors require_user)."""
    user = db.get(User, email)
    if user is None:
        user = User(email=email, role="user")
    return user


def _audit(db, action: str, *, resource: str | None = None, metadata: dict | None = None) -> None:
    u = current_user()
    log_event(
        db, action=action, actor_type="token", user_email=u.email,
        token_prefix=None, resource=resource,
        ip=None, user_agent="mcp",
        metadata=metadata or {},
    )


# ---------- tools ----------


@mcp.tool()
def list_my_reports(filter: str = "all", limit: int = 50) -> list[dict[str, Any]]:
    """List reports visible to the caller.

    filter:
      - "mine"   — reports the caller owns
      - "shared" — reports shared with the caller (viewer or visibility=internal, not own)
      - "all"    — all visible (default)
    limit: max rows (clamped to 200).
    """
    if filter not in {"all", "mine", "shared"}:
        raise ValueError("filter must be 'all', 'mine', or 'shared'")
    limit = max(1, min(int(limit), 200))

    u = current_user()
    db = SessionLocal()
    try:
        user = _user_record(db, u.email)
        rows = list(db.scalars(select(Report).order_by(Report.updated_at.desc())).all())
        out: list[dict[str, Any]] = []
        for r in rows:
            if not _can_read(user, r, db, role=u.role):
                continue
            is_owner = r.owner_email == user.email
            if filter == "mine" and not is_owner:
                continue
            if filter == "shared" and is_owner:
                continue
            out.append(_serialize_report(r))
            if len(out) >= limit:
                break
        return out
    finally:
        db.close()


@mcp.tool()
def search_reports(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Substring search across title / description / slug / tags.

    Case-insensitive. Returns reports the caller has read access to, ranked
    by recency (no relevance scoring — FTS is deferred).
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("query must not be empty")
    limit = max(1, min(int(limit), 100))

    u = current_user()
    db = SessionLocal()
    try:
        user = _user_record(db, u.email)
        like = f"%{q.lower()}%"
        stmt = (
            select(Report)
            .where(
                or_(
                    Report.title.ilike(like),
                    Report.description.ilike(like),
                    Report.slug.ilike(like),
                    Report.tags.ilike(like),  # JSON-encoded but substring still works
                )
            )
            .order_by(Report.updated_at.desc())
        )
        rows = list(db.scalars(stmt).all())
        out: list[dict[str, Any]] = []
        for r in rows:
            if not _can_read(user, r, db, role=u.role):
                continue
            out.append(_serialize_report(r))
            if len(out) >= limit:
                break
        return out
    finally:
        db.close()


@mcp.tool()
def get_report_metadata(slug: str) -> dict[str, Any]:
    """Return full metadata for a report (including viewers if owner/admin).

    Raises if the slug doesn't exist or the caller lacks read permission.
    """
    u = current_user()
    db = SessionLocal()
    try:
        user = _user_record(db, u.email)
        report = db.get(Report, slug)
        if report is None:
            raise ValueError(f"report not found: {slug}")
        if not _can_read(user, report, db, role=u.role):
            raise PermissionError(f"no access to report: {slug}")

        viewer_emails: list[str] | None = None
        if u.role == "admin" or report.owner_email == user.email:
            viewer_emails = [v.user_email for v in report.viewers]

        return _serialize_report(report, viewer_emails=viewer_emails)
    finally:
        db.close()


@mcp.tool()
def fetch_report(slug: str, path: str | None = None, max_bytes: int = 200_000) -> dict[str, Any]:
    """Read a file from a report and return its text content.

    path: relative path inside the report dir; defaults to the report's
          entry_point (typically index.html).
    max_bytes: truncate response above this size (default 200KB).

    Returns {path, content_type, content, truncated, size_bytes}.
    Binary files are rejected — use the URL_path for those.
    """
    u = current_user()
    settings = get_settings()
    max_bytes = max(1024, min(int(max_bytes), 1_000_000))

    db = SessionLocal()
    try:
        user = _user_record(db, u.email)
        report = db.get(Report, slug)
        if report is None:
            raise ValueError(f"report not found: {slug}")
        if not _can_read(user, report, db, role=u.role):
            raise PermissionError(f"no access to report: {slug}")

        rel = (path or report.entry_point or "index.html").lstrip("/")
        base = settings.reports_dir_path / slug
        target = (base / rel).resolve()
        # path traversal guard
        if not str(target).startswith(str(base.resolve())):
            raise ValueError("path escapes report directory")
        if not target.exists() or not target.is_file():
            raise ValueError(f"file not found: {rel}")

        size = target.stat().st_size
        raw = target.read_bytes()[:max_bytes]
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(
                f"file is not utf-8 text: {rel}; binary files served at /r/{slug}/{rel}"
            )

        # best-effort content type
        suffix = Path(rel).suffix.lower()
        content_type = {
            ".html": "text/html", ".htm": "text/html",
            ".txt": "text/plain", ".md": "text/markdown",
            ".css": "text/css", ".js": "application/javascript",
            ".json": "application/json", ".csv": "text/csv",
            ".svg": "image/svg+xml",
        }.get(suffix, "text/plain")

        _audit(db, action="report.mcp_fetch", resource=slug, metadata={"path": rel})

        return {
            "slug": slug,
            "path": rel,
            "content_type": content_type,
            "content": content,
            "truncated": size > max_bytes,
            "size_bytes": size,
        }
    finally:
        db.close()


@mcp.tool()
def recent_activity(days: int = 7, limit: int = 50) -> list[dict[str, Any]]:
    """Audit events visible to the caller.

    Admins see all events; non-admins see only events whose resource is a
    report they own (or that they performed themselves).
    """
    days = max(1, min(int(days), 90))
    limit = max(1, min(int(limit), 200))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    u = current_user()
    db = SessionLocal()
    try:
        user = _user_record(db, u.email)
        stmt = (
            select(AuditLog)
            .where(AuditLog.timestamp >= cutoff)
            .order_by(AuditLog.id.desc())
            .limit(500)  # over-fetch then filter
        )
        rows = list(db.scalars(stmt).all())

        if u.role != "admin":
            owned_slugs = {
                r.slug for r in db.scalars(
                    select(Report).where(Report.owner_email == user.email)
                ).all()
            }

            def visible(ev: AuditLog) -> bool:
                if ev.user_email == user.email:
                    return True
                if ev.action.startswith("report.") and ev.resource in owned_slugs:
                    return True
                return False

            rows = [r for r in rows if visible(r)]

        out = []
        for r in rows[:limit]:
            out.append({
                "id": r.id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "action": r.action,
                "actor_type": r.actor_type,
                "user_email": r.user_email,
                "resource": r.resource,
                "ip": r.ip,
            })
        return out
    finally:
        db.close()
