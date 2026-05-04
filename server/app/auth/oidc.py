"""Google OIDC client (Authlib)."""
from __future__ import annotations

from authlib.integrations.starlette_client import OAuth

from app.config import get_settings

GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"


def _build_oauth() -> OAuth:
    settings = get_settings()
    oauth = OAuth()
    client_kwargs = {"scope": "openid email profile"}
    # If allowed_domain set, hint to the consent screen + we still verify hd in callback.
    authorize_params = {}
    if settings.google.allowed_domain:
        authorize_params["hd"] = settings.google.allowed_domain
    oauth.register(
        name="google",
        client_id=settings.google.client_id,
        client_secret=settings.google.client_secret,
        server_metadata_url=GOOGLE_DISCOVERY,
        client_kwargs=client_kwargs,
        authorize_params=authorize_params or None,
    )
    return oauth


# Lazily evaluated singleton — settings may not be fully loaded at import time during tests
_oauth: OAuth | None = None


def get_oauth() -> OAuth:
    global _oauth
    if _oauth is None:
        _oauth = _build_oauth()
    return _oauth


def is_configured() -> bool:
    s = get_settings()
    return bool(s.google.client_id and s.google.client_secret)
