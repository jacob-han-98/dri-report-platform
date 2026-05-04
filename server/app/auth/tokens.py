"""Personal Access Token (PAT) issuance + verification.

Plaintext format: `<prefix><32 url-safe chars>` where prefix is config.token.prefix
                  (default 'hybe_pat_'). Stored as SHA256 hash.
prefix column = first 16 chars of plaintext (for display / lookup hint).
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiToken, User

PREFIX_DISPLAY_LEN = 16
SECRET_LEN = 32  # url-safe chars after the configured prefix


@dataclass
class IssuedToken:
    plaintext: str
    prefix: str
    expires_at: datetime


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def issue_token(
    db: Session,
    *,
    user: User,
    name: str,
    expires_in_days: int | None = None,
    scopes: list[str] | None = None,
) -> IssuedToken:
    settings = get_settings()
    days = expires_in_days or settings.token.default_expiry_days
    if days <= 0 or days > settings.token.max_expiry_days:
        raise ValueError(
            f"expires_in_days must be in 1..{settings.token.max_expiry_days}"
        )

    secret = secrets.token_urlsafe(SECRET_LEN)
    plaintext = f"{settings.token.prefix}{secret}"
    prefix = plaintext[:PREFIX_DISPLAY_LEN]
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)

    import json as _json

    token = ApiToken(
        token_hash=_hash(plaintext),
        prefix=prefix,
        user_email=user.email,
        name=name,
        scopes=_json.dumps(scopes) if scopes else None,
        expires_at=expires_at,
    )
    db.add(token)
    db.commit()
    return IssuedToken(plaintext=plaintext, prefix=prefix, expires_at=expires_at)


def verify_token(
    db: Session, plaintext: str, *, ip: str | None = None
) -> User | None:
    """Return the User if `plaintext` is a valid, non-expired, non-revoked token."""
    settings = get_settings()
    if not plaintext.startswith(settings.token.prefix):
        return None

    token = db.get(ApiToken, _hash(plaintext))
    if token is None:
        return None
    if token.revoked_at is not None:
        return None

    expires = token.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        return None

    # update last_used (best-effort; caller has its own commit lifecycle)
    token.last_used_at = datetime.now(timezone.utc)
    if ip:
        token.last_used_ip = ip
    db.commit()

    return db.get(User, token.user_email)


def revoke_token(db: Session, *, prefix: str, user: User) -> bool:
    """Revoke a token owned by `user`. Returns True if a row was revoked."""
    token = (
        db.query(ApiToken)
        .filter(ApiToken.prefix == prefix, ApiToken.user_email == user.email)
        .one_or_none()
    )
    if token is None or token.revoked_at is not None:
        return False
    token.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True
