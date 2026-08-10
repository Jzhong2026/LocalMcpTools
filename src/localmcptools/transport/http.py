"""HTTP transport — Bearer (MCP) + Origin / CSRF (control plane).

Two enforcement surfaces:

- ``/mcp``      — Streamable HTTP MCP endpoint. Requires ``Authorization:
  Bearer <token>``. Origin / Host checks are *not* required here because
  the MCP protocol is JSON-RPC over HTTP, not a browser context; agents
  running over stdio can also reach this endpoint.

- ``/api/*``    — Control plane for the Angular SPA. Requires the
  ``Origin`` header to be in the configured allowlist, and unsafe methods
  (POST / PATCH / DELETE) additionally require ``X-LMCP-CSRF`` to match
  the ``lmcp_csrf`` cookie. The cookie is ``HttpOnly``; the SPA fetches
  ``/api/csrf-token`` once on bootstrap and adds the matching header on
  every subsequent unsafe request.

The csrf-token *is* the bearer secret — one 32-byte URL-safe random
token serves both purposes. This keeps the wire protocol simple and
removes a class of "token A vs token B mismatch" bugs.

The module is transport-only; it never reads the audit database or
calls tool logic. The control plane router (see ``control_api``) is
responsible for that.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Iterable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

_log = logging.getLogger(__name__)


# --- Pure helpers ---------------------------------------------------------


_UNSAFE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_unsafe_method(method: str) -> bool:
    """Return True for HTTP methods that need CSRF protection."""
    return method.upper() in _UNSAFE_METHODS


def origin_allowed(origin: str | None, allowlist: Iterable[str]) -> bool:
    """Return True if ``origin`` matches any entry in the allowlist.

    The comparison is exact-string; we do not parse the URL or strip
    default ports because the allowlist is operator-supplied and short.
    Adding a glob-style matcher is overkill for two entries.
    """
    if not origin:
        return False
    return origin in set(allowlist)


def generate_token() -> str:
    """Return a fresh URL-safe 32-byte random token."""
    return secrets.token_urlsafe(32)


# --- Bearer auth ----------------------------------------------------------


@dataclass(frozen=True)
class BearerAuth:
    """Bearer-token gate for ``/mcp``.

    The expected token is supplied once (when the server boots) and
    matched verbatim against the ``Authorization`` header.
    """

    expected: str

    def is_valid(self, authorization: str | None) -> bool:
        if not authorization:
            return False
        # Accept ``Bearer <token>`` (case-insensitive scheme).
        parts = authorization.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        # Constant-time compare prevents token-length side channels.
        return secrets.compare_digest(parts[1], self.expected)


# --- Security context + middleware ---------------------------------------


@dataclass(frozen=True)
class SecurityContext:
    """The trusted state set up at server boot.

    Bundles the bearer secret + Origin allowlist into one object so the
    control plane router can read it without reaching back into module
    globals. Tests construct their own :class:`SecurityContext` so the
    middleware logic stays decoupled from any concrete secret.
    """

    token: str
    origin_allowlist: tuple[str, ...]
    csrf_cookie_name: str = "lmcp_csrf"
    csrf_header_name: str = "x-lmcp-csrf"


class OriginCSRF(BaseHTTPMiddleware):
    """ASGI middleware enforcing Origin + CSRF for ``/api/*``.

    Paths starting with ``/api/csrf-token`` are exempt from the CSRF
    check — they exist specifically so the SPA can bootstrap itself.
    Every other ``/api/*`` route runs through the full pipeline:

    1. ``Origin`` header present + in allowlist
       (allowlist defaults to ``http://127.0.0.1`` / ``http://localhost``)
    2. For unsafe methods, the ``X-LMCP-CSRF`` header must match the
       ``lmcp_csrf`` cookie verbatim. Constant-time compare.
    3. ``GET / HEAD / OPTIONS`` skip step 2 (safe methods can't mutate).

    Failure responses are JSON with a stable shape the SPA can switch on.
    """

    def __init__(self, app: ASGIApp, *, context: SecurityContext) -> None:
        super().__init__(app)
        self._context = context

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        # /api/csrf-token bootstraps the SPA — exempt from CSRF check.
        # We still enforce Origin here; the SPA only fetches this once
        # and the allowlist is per-machine.
        origin = request.headers.get("origin")
        if not origin_allowed(origin, self._context.origin_allowlist):
            return JSONResponse(
                status_code=403,
                content={
                    "code": "origin_denied",
                    "message": "Origin header missing or not in allowlist",
                    "next_actions": [
                        "open the UI from http://127.0.0.1:<port> or http://localhost:<port>"
                    ],
                },
            )

        if path == "/api/csrf-token":
            return await call_next(request)

        if is_unsafe_method(request.method):
            cookie = request.cookies.get(self._context.csrf_cookie_name)
            header = request.headers.get(self._context.csrf_header_name)
            if not cookie or not header:
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "csrf_missing",
                        "message": "X-LMCP-CSRF header or lmcp_csrf cookie missing",
                    },
                )
            if not secrets.compare_digest(cookie, header):
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "csrf_mismatch",
                        "message": "X-LMCP-CSRF does not match cookie",
                    },
                )
        return await call_next(request)


# --- App assembly --------------------------------------------------------


def mount_app(
    *,
    app: FastAPI,
    context: SecurityContext,
    control_router: object | None,
    mcp_asgi_app: ASGIApp | None,
    static_dir: str | None,
    static_root_path: str = "/ui",
) -> FastAPI:
    """Mount control plane + MCP + UI static into one FastAPI app.

    - ``control_router`` is a :class:`fastapi.APIRouter` (or any object
      with ``.routes``). When ``None`` only static + MCP are mounted —
      useful for tests.
    - ``mcp_asgi_app`` is the result of
      ``FastMCP.streamable_http_app()``. When ``None`` only static +
      control plane are mounted.
    - ``static_dir`` is the on-disk directory holding the Angular
      ``index.html`` + bundle output. When ``None`` the static mount
      is skipped.

    The :class:`OriginCSRF` middleware is added to the app regardless;
    it short-circuits paths outside ``/api/*`` so the static + MCP
    mounts stay unaffected.
    """
    if control_router is not None:
        # ``include_router`` accepts any router-like object via duck
        # typing; FastAPI doesn't actually need a type annotation.
        app.include_router(control_router)  # type: ignore[arg-type]
    if mcp_asgi_app is not None:
        app.mount("/mcp", mcp_asgi_app)
    if static_dir:
        from fastapi.staticfiles import StaticFiles

        # ``html=True`` makes ``/ui/foo`` fall back to ``/ui/foo.html``
        # so the SPA's deep links work.
        app.mount(static_root_path, StaticFiles(directory=static_dir, html=True), name="ui")
    app.add_middleware(OriginCSRF, context=context)

    @app.get("/api/csrf-token")
    async def csrf_token_endpoint():
        """Bootstrap endpoint. Sets the cookie and echoes the token."""
        # The SPA calls this on first load. We set HttpOnly + SameSite=Lax
        # so the cookie is sent back automatically. The JS never needs to
        # read it; it just reads ``X-LMCP-CSRF`` from a sibling header.
        from fastapi.responses import Response

        response = Response(
            content=json_for_csrf(context.token),
            media_type="application/json",
        )
        response.set_cookie(
            key=context.csrf_cookie_name,
            value=context.token,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response

    return app


def json_for_csrf(token: str) -> str:
    """Serialize the CSRF token as JSON for the SPA bootstrap response."""
    import json

    return json.dumps({"csrf_token": token})


__all__ = [
    "BearerAuth",
    "OriginCSRF",
    "SecurityContext",
    "generate_token",
    "is_unsafe_method",
    "mount_app",
    "origin_allowed",
]