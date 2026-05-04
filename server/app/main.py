"""FastAPI entrypoint for Hybe Reports Platform server."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.admin import router as admin_router
from app.api.reports import router as reports_router
from app.api.tokens import router as tokens_router
from app.auth.check import router as auth_check_router
from app.auth.csrf import (
    CSRF_COOKIE,
    CSRF_FIELD,
    CSRF_HEADER,
    ensure_token_on_request,
    set_csrf_cookie,
    validate as validate_csrf,
)
from app.auth.routes import router as auth_routes_router
from app.config import get_settings
from app.mcp.context import McpUser, reset as reset_mcp_user, set_user as set_mcp_user
from app.mcp.server import mcp as mcp_server
from app.web.routes import router as web_router


def _configure_logging() -> None:
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    settings = get_settings()
    log = structlog.get_logger()
    log.info(
        "startup",
        reports_dir=str(settings.reports_dir_path),
        db_path=str(settings.db_path),
        dev_bypass=bool(settings.dev.bypass_auth_email),
    )
    # FastMCP's streamable_http session manager needs its own task group
    # initialized. Mounting alone doesn't propagate the inner lifespan, so
    # we drive it here. The session_manager refuses re-.run() calls, so on
    # a second lifespan (e.g. a fresh TestClient in tests) we skip starting
    # it and just yield — MCP routes won't work in that test, which is fine
    # for tests not covering MCP.
    if getattr(mcp_server.session_manager, "_has_started", False):
        yield
    else:
        async with mcp_server.session_manager.run():
            yield
    log.info("shutdown")


_settings_for_init = get_settings()
app = FastAPI(
    title="Hybe Reports Platform",
    lifespan=lifespan,
    root_path=_settings_for_init.app.web_prefix,
)


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# CSRF check skipped for these path prefixes:
# - /auth/check is internal forward_auth (no body)
# - /auth/callback is the OIDC redirect from Google (cross-origin by design)
# - /api/* and /mcp/* use Bearer auth (no automatic cookie attachment)
CSRF_SKIP_PREFIXES = ("/auth/check", "/auth/callback", "/api/", "/mcp/")


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        skip = (
            method in SAFE_METHODS
            or any(path.startswith(p) for p in CSRF_SKIP_PREFIXES)
            or request.headers.get("authorization", "").lower().startswith("bearer ")
        )

        # Mint or read the token BEFORE downstream handlers run, so templates
        # rendered during this request see the cookie value via request.cookies.
        token, is_new = ensure_token_on_request(request)

        if not skip:
            submitted = request.headers.get(CSRF_HEADER)
            if not submitted and "form" in request.headers.get("content-type", "").lower():
                # buffer body once, then replay it for the downstream handler.
                body = await request.body()
                form = await request.form()
                submitted = form.get(CSRF_FIELD)

                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}

                request._receive = receive  # type: ignore[attr-defined]

            if not validate_csrf(request, submitted if isinstance(submitted, str) else None):
                return JSONResponse(
                    {"detail": "csrf token missing or invalid"}, status_code=403
                )

        response: Response = await call_next(request)
        if is_new:
            set_csrf_cookie(response, token)
        return response


# Authlib needs Starlette session storage for OAuth state/nonce. We use a
# separate signed cookie ("hybe_oidc_state") with a short lifetime; the
# user-facing session lives in our own `hybe_session` cookie (auth/session.py).
_settings = get_settings()
app.add_middleware(CSRFMiddleware)
_oidc_cookie_path = _settings.app.web_prefix or "/"
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.app.secret_key,
    session_cookie="hybe_oidc_state",
    max_age=600,  # 10 min — only needs to live for the OAuth round-trip
    same_site="lax",
    https_only=True,
    path=_oidc_cookie_path,
)

app.include_router(auth_check_router)
app.include_router(auth_routes_router)
app.include_router(reports_router)
app.include_router(tokens_router)
app.include_router(admin_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------- MCP (Streamable HTTP) ----------
#
# Caddy's forward_auth has already validated the Bearer token and set
# X-User-Email / X-User-Role headers. We bridge those into the ContextVar
# so MCP tool functions can read the caller without touching FastAPI Request.

def _mcp_auth_bridge(inner_app):
    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            return await inner_app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        email = headers.get("x-user-email")
        role = headers.get("x-user-role", "user")
        if not email:
            response = JSONResponse(
                {"detail": "missing X-User-Email"}, status_code=401
            )
            return await response(scope, receive, send)
        token = set_mcp_user(McpUser(email=email, role=role))
        try:
            await inner_app(scope, receive, send)
        finally:
            reset_mcp_user(token)
    return wrapped


app.mount("/mcp", _mcp_auth_bridge(mcp_server.streamable_http_app()))


# Web (Jinja2) — register last so /api/* and /auth/* take precedence.
app.include_router(web_router)
