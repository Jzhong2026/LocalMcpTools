"""HTTP transport layer for LocalMcpTools shared mode.

Exports:

- :class:`BearerAuth`     — validate ``Authorization: Bearer <token>`` on
  ``/mcp`` (the MCP endpoint itself).
- :class:`OriginCSRF`     — single ASGI middleware that enforces
  ``Origin`` allowlist + ``X-LMCP-CSRF`` / ``lmcp_csrf`` cookie match
  for ``/api/*`` routes.
- :func:`mount_app`       — wire the control plane + MCP endpoint +
  UI static assets into one FastAPI app, plus Origin/CSRF/Bearer.

The split between the two classes exists so the tests can exercise the
pure decision logic without booting a server.
"""

from __future__ import annotations

from .http import (
    BearerAuth,
    OriginCSRF,
    SecurityContext,
    generate_token,
    is_unsafe_method,
    mount_app,
    origin_allowed,
)

__all__ = [
    "BearerAuth",
    "OriginCSRF",
    "SecurityContext",
    "generate_token",
    "is_unsafe_method",
    "mount_app",
    "origin_allowed",
]