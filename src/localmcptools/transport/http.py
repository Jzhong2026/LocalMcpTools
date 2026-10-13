"""HTTP transport — separate MCP bearer and control-plane session.

Two secrets, two surfaces:

- ``/mcp`` uses ``mcp_bearer`` (``Authorization: Bearer``). That secret
  never authorizes ``/api/*``.
- ``/api/*`` requires a control-plane session. A session is created only
  by presenting the admin secret (``POST /api/login``) or a one-time
  login nonce (``GET /api/login?token=``). The session id lives in an
  HttpOnly ``lmcp_session`` cookie. Unsafe methods also send
  ``X-LMCP-CSRF``, which is a per-session value and is not either secret.

Host is checked on ``/api``, ``/mcp``, and ``/ui``. Loopback is always
allowed; extra tunnel hostnames come from ``allowed_hosts``. Origin is
checked when the client sends one. A forged Origin, a matching
cookie/header pair, or the MCP bearer alone cannot read or mutate the
control plane.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

_log = logging.getLogger(__name__)

SESSION_COOKIE = "lmcp_session"
CSRF_HEADER = "x-lmcp-csrf"
_UNSAFE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})
_LOGIN_PATH = "/api/login"


def is_unsafe_method(method: str) -> bool:
    """Return True for HTTP methods that need a CSRF header."""
    return method.upper() in _UNSAFE_METHODS


def const_eq(left: str, right: str) -> bool:
    """Compare two strings without leaking their lengths or contents."""
    left_digest = hashlib.sha256(left.encode("utf-8")).digest()
    right_digest = hashlib.sha256(right.encode("utf-8")).digest()
    return secrets.compare_digest(left_digest, right_digest)


def hostname_of(value: str) -> str:
    """Return the hostname from a Host header or ``host[:port]`` value."""
    text = value.strip().lower().rstrip(".")
    if text.startswith("["):
        end = text.find("]")
        return text[1:end] if end != -1 else text
    if text.count(":") == 1:
        return text.split(":", 1)[0]
    return text


def host_allowed(host_header: str | None, extra: Iterable[str]) -> bool:
    """Allow loopback and operator-configured tunnel hostnames."""
    if not host_header:
        return False
    hostname = hostname_of(host_header)
    if hostname in _LOOPBACK_HOSTS:
        return True
    return hostname in {hostname_of(item) for item in extra if item}


def origin_allowed(origin: str | None, allowlist: Iterable[str]) -> bool:
    """Allow exact operator origins, plus loopback on any port."""
    if not origin:
        return False
    if origin in set(allowlist):
        return True
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    return parsed.hostname.lower() in _LOOPBACK_HOSTS


def generate_token() -> str:
    """Return a fresh URL-safe 32-byte random token."""
    return secrets.token_urlsafe(32)


def _unauthorized(message: str = "control-plane session required") -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"code": "unauthorized", "message": message},
    )


def _cookie_secure(request: Request) -> bool:
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _set_session_cookie(response: Response, request: Request, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(request),
        path="/",
    )


@dataclass
class SessionStore:
    """Server-side sessions and single-use login nonces."""

    _sessions: dict[str, str] = field(default_factory=dict)
    _nonces: set[str] = field(default_factory=set)

    def issue_login_nonce(self) -> str:
        nonce = generate_token()
        self._nonces.add(nonce)
        return nonce

    def open_session(
        self,
        presented: str,
        *,
        admin_secret: str,
        mcp_bearer: str,
        allow_admin_secret: bool = True,
    ) -> tuple[str, str] | None:
        """Open a session from the admin secret or a one-time nonce.

        The MCP bearer is compared and then rejected, so possessing it
        cannot mint a control-plane session even if a caller also sends
        a lookalike cookie.
        """
        is_mcp = const_eq(presented, mcp_bearer)
        is_admin = const_eq(presented, admin_secret) if allow_admin_secret else False
        matched_nonce = False
        for nonce in list(self._nonces):
            if const_eq(presented, nonce):
                matched_nonce = True
        if is_mcp or not (is_admin or matched_nonce):
            return None
        if matched_nonce:
            self._nonces = {nonce for nonce in self._nonces if not const_eq(presented, nonce)}
        session_id = generate_token()
        csrf = generate_token()
        self._sessions[session_id] = csrf
        return session_id, csrf

    def csrf_for(self, session_id: str | None) -> str | None:
        if not session_id:
            return None
        return self._sessions.get(session_id)


@dataclass
class BearerAuth:
    """Bearer-token gate for ``/mcp`` only."""

    expected: str

    def is_valid(self, authorization: str | None) -> bool:
        if not authorization:
            return False
        parts = authorization.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        return const_eq(parts[1], self.expected)


@dataclass
class SecurityContext:
    """Secrets and allowlists for one server process."""

    mcp_bearer: str
    admin_secret: str
    origin_allowlist: tuple[str, ...] = ("http://127.0.0.1", "http://localhost")
    allowed_hosts: tuple[str, ...] = ()
    sessions: SessionStore = field(default_factory=SessionStore)

    def __post_init__(self) -> None:
        if const_eq(self.mcp_bearer, self.admin_secret):
            raise ValueError("mcp_bearer and admin_secret must be different secrets")


class OriginCSRF(BaseHTTPMiddleware):
    """Host, origin, session, and CSRF checks for the HTTP surfaces."""

    def __init__(self, app: ASGIApp, *, context: SecurityContext) -> None:
        super().__init__(app)
        self._context = context

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if path.startswith("/api/") or path.startswith("/mcp") or path.startswith("/ui"):
            if not host_allowed(request.headers.get("host"), self._context.allowed_hosts):
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "host_denied",
                        "message": "Host header is not loopback or an allowed tunnel hostname",
                    },
                )
        if path.startswith("/mcp"):
            return await self._check_mcp_bearer(request, call_next)
        if not path.startswith("/api/"):
            return await call_next(request)

        origin = request.headers.get("origin")
        if origin is not None and not origin_allowed(origin, self._context.origin_allowlist):
            return JSONResponse(
                status_code=403,
                content={
                    "code": "origin_denied",
                    "message": "Origin header is not in the allowlist",
                },
            )
        if path == _LOGIN_PATH:
            return await call_next(request)

        csrf = self._context.sessions.csrf_for(request.cookies.get(SESSION_COOKIE))
        if csrf is None:
            return _unauthorized()
        if is_unsafe_method(request.method):
            header = request.headers.get(CSRF_HEADER)
            if not header:
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "csrf_missing",
                        "message": "X-LMCP-CSRF header required",
                    },
                )
            if not const_eq(header, csrf):
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "csrf_mismatch",
                        "message": "X-LMCP-CSRF does not match the control-plane session",
                    },
                )
        return await call_next(request)

    async def _check_mcp_bearer(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        auth = BearerAuth(expected=self._context.mcp_bearer)
        if not auth.is_valid(request.headers.get("authorization")):
            return JSONResponse(
                status_code=401,
                content={
                    "code": "unauthorized",
                    "message": "Authorization: Bearer <mcp token> required",
                },
            )
        return await call_next(request)


def mount_app(
    *,
    app: FastAPI,
    context: SecurityContext,
    control_router: object | None,
    mcp_asgi_app: ASGIApp | None,
    static_dir: str | None,
    static_root_path: str = "/ui",
) -> FastAPI:
    """Mount control plane + MCP + UI and attach the security middleware."""
    app.state.security = context
    if control_router is not None:
        app.include_router(control_router)  # type: ignore[arg-type]
    if mcp_asgi_app is not None:
        app.mount("/mcp", mcp_asgi_app)
    if static_dir:
        from fastapi.staticfiles import StaticFiles

        app.mount(static_root_path, StaticFiles(directory=static_dir, html=True), name="ui")
    app.add_middleware(OriginCSRF, context=context)

    @app.post("/api/login")
    async def login_post(request: Request) -> Response:
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        presented = str(body.get("token") or "") if isinstance(body, dict) else ""
        opened = context.sessions.open_session(
            presented,
            admin_secret=context.admin_secret,
            mcp_bearer=context.mcp_bearer,
            allow_admin_secret=True,
        )
        if opened is None:
            return _unauthorized("login rejected")
        session_id, csrf = opened
        response = JSONResponse({"ok": True, "csrf_token": csrf})
        _set_session_cookie(response, request, session_id)
        return response

    @app.get("/api/login")
    async def login_get(request: Request, token: str = "") -> Response:
        opened = context.sessions.open_session(
            token,
            admin_secret=context.admin_secret,
            mcp_bearer=context.mcp_bearer,
            allow_admin_secret=False,
        )
        if opened is None:
            return _unauthorized("login rejected")
        session_id, _csrf = opened
        response = RedirectResponse(url="/ui/", status_code=302)
        _set_session_cookie(response, request, session_id)
        return response

    @app.get("/api/csrf-token")
    async def csrf_token_endpoint(request: Request) -> JSONResponse:
        csrf = context.sessions.csrf_for(request.cookies.get(SESSION_COOKIE))
        if csrf is None:
            return _unauthorized()
        return JSONResponse({"csrf_token": csrf})

    return app


__all__ = [
    "BearerAuth",
    "OriginCSRF",
    "SESSION_COOKIE",
    "SecurityContext",
    "SessionStore",
    "const_eq",
    "generate_token",
    "host_allowed",
    "is_unsafe_method",
    "mount_app",
    "origin_allowed",
]
