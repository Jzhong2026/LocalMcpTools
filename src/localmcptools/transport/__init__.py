"""HTTP transport layer for LocalMcpTools shared mode.

Exports:

- :class:`BearerAuth`     — validate ``Authorization: Bearer <mcp token>``
  on ``/mcp`` only. The MCP bearer does not authorize ``/api/*``.
- :class:`OriginCSRF`     — Host allowlist, Origin allowlist, control-plane
  session cookie, and per-session ``X-LMCP-CSRF`` for unsafe ``/api/*``.
- :func:`mount_app`       — wire the control plane + MCP endpoint +
  UI static assets into one FastAPI app.

The MCP bearer and the control-plane admin secret are different values.
"""

from __future__ import annotations

from .http import (
    BearerAuth,
    OriginCSRF,
    SecurityContext,
    generate_token,
    host_allowed,
    is_unsafe_method,
    mount_app,
    origin_allowed,
)

__all__ = [
    "BearerAuth",
    "OriginCSRF",
    "SecurityContext",
    "generate_token",
    "host_allowed",
    "is_unsafe_method",
    "mount_app",
    "origin_allowed",
]
