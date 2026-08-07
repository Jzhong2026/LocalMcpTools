"""Workspace capability profiles.

Profiles are authority owned by the server, never by an MCP tool argument.
This module deliberately exposes only a read operation during the first
policy slice. Profile elevation will be performed by the approval workflow,
not by a caller choosing a more powerful value.
"""

from __future__ import annotations

import sqlite3
from enum import StrEnum

from ..persistence import db
from ..workspaces.registry import WorkspaceNotRegistered


class Profile(StrEnum):
    """The complete, closed set of workspace capability profiles."""

    OBSERVE = "observe"
    WORKSPACE_EXEC = "workspace_exec"
    MANAGED_PROCESS = "managed_process"
    INTERACTIVE_UI = "interactive_ui"


DEFAULT_PROFILE = Profile.OBSERVE


class ProfileChangeForbidden(PermissionError):
    """Raised when a tool attempts to select or raise a workspace profile."""


def current(workspace_id: str, *, conn: sqlite3.Connection | None = None) -> Profile:
    """Return the authority-selected profile for ``workspace_id``.

    A malformed database value fails closed rather than becoming an implicit
    capability grant.
    """
    if not workspace_id:
        raise WorkspaceNotRegistered("workspace_id is empty")

    def _read(connection: sqlite3.Connection) -> Profile:
        row = connection.execute(
            "SELECT profile FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            raise WorkspaceNotRegistered(f"workspace_id={workspace_id!r} is not registered")
        try:
            return Profile(row["profile"])
        except ValueError as exc:
            raise RuntimeError(f"workspace_id={workspace_id!r} has invalid profile") from exc

    if conn is not None:
        return _read(conn)
    db.init_db()
    with db.connection() as connection:
        return _read(connection)


def set_current_from_tool(*_args: object, **_kwargs: object) -> None:
    """Refuse profile changes originating in tool logic."""
    raise ProfileChangeForbidden(
        "workspace profiles are changed only by the approval authority"
    )


__all__ = [
    "DEFAULT_PROFILE",
    "Profile",
    "ProfileChangeForbidden",
    "current",
    "set_current_from_tool",
]
