"""Audit log helper. Spec §4.audit_logs."""
from __future__ import annotations

import json as _json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def log_event(
    db: Session,
    *,
    action: str,
    actor_type: str,  # 'session' | 'token' | 'mcp' | 'system'
    user_email: str | None = None,
    token_prefix: str | None = None,
    resource: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            user_email=user_email,
            actor_type=actor_type,
            token_prefix=token_prefix,
            action=action,
            resource=resource,
            ip=ip,
            user_agent=user_agent,
            metadata_json=_json.dumps(metadata) if metadata else None,
        )
    )
    db.commit()
