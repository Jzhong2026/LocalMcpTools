"""Hard-coded default configuration.

The shape of this dict is frozen for the spike — every later change that
needs a new field must extend the ``Config`` model in :mod:`.settings`
*and* add the default here, so a fresh install keeps working.

The values intentionally match :ref:`openspec/changes/bootstrap-mcp-server/design.md`.
"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "server": {
        "host": "127.0.0.1",
        "log_level": "INFO",
    },
    "security": {
        # Spike-only: stdio transport. Streamable HTTP is gated behind
        # change-5 (angular-ui-foundation) and must not be enabled here.
        "transport_mode": "stdio",
        "http_shared_mode_enabled": False,
        # Sensitive values are redacted before they touch the audit log
        # or any artifact. This is a hard invariant for the spike.
        "redact_before_persist": True,
    },
    "workspaces": {
        # Default capability profile. ``observe`` is read-only; any
        # side-effect must require an explicit profile escalation plus
        # an approval. See openspec/changes/policy-and-safety/.
        "default_profile": "observe",
        # Workspace-id -> allowed environment variable names. Empty by default.
        "env_allowlists": {},
    },
    "audit": {
        # Spike retention: 7 days. Real production retention is decided
        # later (likely 30-90 days) once the UI exposes it.
        "retention_days": 7,
        "cleanup_interval_hours": 6,
    },
}


def get_defaults() -> dict[str, Any]:
    """Return a *copy* of the defaults so callers cannot mutate the module constant."""
    import copy

    return copy.deepcopy(DEFAULTS)
