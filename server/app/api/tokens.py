"""/api/tokens — issue, list, revoke."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.reports import _actor_type, _token_prefix, require_user
from app.audit import log_event
from app.auth.tokens import issue_token, revoke_token
from app.db import get_db
from app.models import ApiToken, User

router = APIRouter(prefix="/api")
log = structlog.get_logger()


class IssueIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int | None = None
    scopes: list[str] | None = None


class IssueOut(BaseModel):
    token: str  # plaintext, returned ONCE
    prefix: str
    name: str
    expires_at: datetime


class TokenListItem(BaseModel):
    prefix: str
    name: str
    scopes: list[str] | None
    expires_at: datetime
    last_used_at: datetime | None
    last_used_ip: str | None
    revoked_at: datetime | None
    created_at: datetime


@router.post("/tokens", status_code=201)
def post_token(
    body: IssueIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> IssueOut:
    try:
        issued = issue_token(
            db,
            user=user,
            name=body.name,
            expires_in_days=body.expires_in_days,
            scopes=body.scopes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    log.info("token.issued", user=user.email, prefix=issued.prefix, name=body.name)
    log_event(
        db,
        action="token.issue",
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=issued.prefix,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"name": body.name},
    )

    return IssueOut(
        token=issued.plaintext,
        prefix=issued.prefix,
        name=body.name,
        expires_at=issued.expires_at,
    )


@router.get("/tokens")
def list_tokens(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> list[TokenListItem]:
    import json as _json

    rows = (
        db.query(ApiToken)
        .filter(ApiToken.user_email == user.email)
        .order_by(ApiToken.created_at.desc())
        .all()
    )
    return [
        TokenListItem(
            prefix=r.prefix,
            name=r.name,
            scopes=_json.loads(r.scopes) if r.scopes else None,
            expires_at=r.expires_at,
            last_used_at=r.last_used_at,
            last_used_ip=r.last_used_ip,
            revoked_at=r.revoked_at,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.delete("/tokens/{prefix}", status_code=204)
def delete_token(
    prefix: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
) -> None:
    revoked = revoke_token(db, prefix=prefix, user=user)
    if not revoked:
        raise HTTPException(status_code=404, detail="token not found or already revoked")
    log.info("token.revoked", user=user.email, prefix=prefix)
    log_event(
        db,
        action="token.revoke",
        actor_type=_actor_type(request),
        user_email=user.email,
        token_prefix=_token_prefix(request),
        resource=prefix,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
