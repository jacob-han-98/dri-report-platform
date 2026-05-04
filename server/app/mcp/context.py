"""Per-request user context for MCP tools.

The MCP tool functions are decoupled from FastAPI's request object — they're
called by the FastMCP runtime which doesn't expose `Request`. We bridge with
a ContextVar populated by an ASGI middleware reading X-User-Email / X-User-Role
(injected by Caddy's forward_auth → /auth/check).
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class McpUser:
    email: str
    role: str  # 'admin' or 'user'


_current: ContextVar[McpUser | None] = ContextVar("mcp_current_user", default=None)


def set_user(user: McpUser | None) -> object:
    """Returns a token that can be passed to reset()."""
    return _current.set(user)


def reset(token: object) -> None:
    _current.reset(token)  # type: ignore[arg-type]


def current() -> McpUser:
    user = _current.get()
    if user is None:
        raise PermissionError("no MCP user in context")
    return user
