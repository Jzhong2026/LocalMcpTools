"""Workspace registry — register / list / resolve / canonicalize.

Public API:

- :class:`Workspace` — value object returned by :func:`register`.
- :func:`register(path, profile='observe')` — registers a directory and
  returns a :class:`Workspace`. Idempotent: registering the same
  canonical root twice returns the *same* :class:`Workspace`.
- :func:`resolve(workspace_id)` — looks up by id; raises
  :class:`WorkspaceNotRegistered`.
- :func:`list_workspaces()` — every row, for the UI.
- :func:`canonicalize(path)` — pure helper, returns absolute resolved path.
- :exc:`InvalidPath`, :exc:`WorkspaceNotRegistered` — domain errors.

Path-escape rejection happens *before* :func:`os.path.realpath` is
called so a hostile agent can't probe whether arbitrary paths exist on
the host. The rules:

1. The path must be an absolute, non-empty string.
2. ``..`` segments are rejected outright (early reject per design.md).
3. ``os.path.realpath`` is then applied to canonicalise symlinks etc.
4. The resolved path must exist and be a directory.

Two inputs that canonicalise to the same absolute path share one row.
This is the idempotency guarantee agents rely on.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..persistence.db import get_connection

_log = logging.getLogger(__name__)


# --- Exceptions -----------------------------------------------------------


class InvalidPath(ValueError):
    """A path was rejected by the registry (escape attempt, not a dir, etc.).

    Maps to the ``invalid_path`` error code in the tool envelope.
    """


class WorkspaceNotRegistered(LookupError):
    """Caller referenced a ``workspace_id`` that does not exist.

    Maps to the ``workspace_not_registered`` error code.
    """


# --- Value object ----------------------------------------------------------


@dataclass(frozen=True)
class Workspace:
    """A registered directory that tools may operate within.

    Frozen so it can be hashed / compared safely and never mutated
    after creation.
    """

    id: str
    canonical_root: str
    registered_at: int  # unix ms
    profile: str = "observe"
    notes: str | None = None

    def contains(self, path: str | os.PathLike[str]) -> bool:
        """True iff ``path`` resolves to something inside ``canonical_root``.

        The comparison is done on ``os.path.realpath`` of both sides.
        ``Path(__file__).resolve()`` style inputs are supported.
        """
        try:
            resolved = os.path.realpath(os.fspath(path))
        except (OSError, ValueError):
            return False
        root = self.canonical_root
        # Windows is case-insensitive; we lower both sides for the
        # comparison so /Foo and /foo are the same root.
        if os.name == "nt":
            resolved_c = os.path.normcase(resolved)
            root_c = os.path.normcase(root)
        else:
            resolved_c = resolved
            root_c = root
        # Trailing separator matters: realpath returns no trailing sep on
        # the root, so we append one to avoid /foobar matching /foo.
        root_prefix = root_c if root_c.endswith(os.sep) else root_c + os.sep
        return resolved_c == root_c or resolved_c.startswith(root_prefix)


# --- Path safety ----------------------------------------------------------


# A ``..`` segment — backslash or forward-slash separated. Match only when
# the segment is exactly ``..`` (not ``..foo`` or `..1`).
_DOT_DOT_SEGMENT_RE = re.compile(r"(?:^|[/\\])\.\.(?:[/\\]|$)")


def _looks_like_path_escape(path: str) -> bool:
    """Cheap pre-flight check before invoking ``os.path.realpath``.

    We reject any path containing a ``..`` segment anywhere. The point
    is to fail fast — a hostile agent probing the registry shouldn't
    learn anything about whether arbitrary paths exist on disk.
    """
    return bool(_DOT_DOT_SEGMENT_RE.search(path))


def canonicalize(path: str | os.PathLike[str]) -> str:
    """Return the absolute resolved form of ``path``.

    Raises :class:`InvalidPath` if the path is empty, not absolute, or
    contains a ``..`` segment. The returned string is the result of
    ``os.path.realpath`` and is suitable for storing / comparing.
    """
    text = os.fspath(path)
    if not text or not text.strip():
        raise InvalidPath("path is empty")
    # Order matters: check ``..`` *before* abspath, because abspath
    # silently collapses ``..`` segments and we'd never see them.
    if _looks_like_path_escape(text):
        raise InvalidPath(f"path contains '..' traversal: {text!r}")
    if not os.path.isabs(text):
        # We require an explicit absolute path; refusing "relative" is
        # the only way to guarantee the agent isn't accidentally
        # registering whatever the server process's cwd happens to be.
        raise InvalidPath(f"path is not absolute: {text!r}")
    text_abs = text
    try:
        real = os.path.realpath(text_abs)
    except (OSError, ValueError) as exc:
        raise InvalidPath(f"path could not be resolved: {text!r} ({exc})") from exc
    if not real:
        raise InvalidPath(f"path resolved to empty: {text!r}")
    # Belt + braces: even after realpath we re-check for ``..`` — older
    # Windows versions used to let UNC paths slip through.
    if _looks_like_path_escape(real):
        raise InvalidPath(f"path resolves outside a path: {real!r}")
    return real


def _require_directory(path: str) -> None:
    if not os.path.isdir(path):
        raise InvalidPath(f"path is not a directory: {path!r}")


# --- Registry operations --------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def register(
    path: str | os.PathLike[str],
    *,
    profile: str = "observe",
    notes: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> Workspace:
    """Register ``path`` and return the :class:`Workspace`.

    Idempotent: a second call with the same canonical root returns the
    existing row (and silently updates the notes / profile if they differ).

    Raises :class:`InvalidPath` for any safety-rejection reason.
    """
    canonical = canonicalize(path)
    _require_directory(canonical)

    new_id = uuid.uuid4().hex
    now = _now_ms()

    def _do(c: sqlite3.Connection) -> Workspace:
        existing = c.execute(
            "SELECT id, canonical_root, registered_at, profile, notes "
            "FROM workspaces WHERE canonical_root = ?",
            (canonical,),
        ).fetchone()
        if existing is not None:
            # Idempotent: keep the original id / registered_at. If the
            # caller wants to change profile / notes, we honour that.
            if (profile != existing["profile"]) or (notes != existing["notes"]):
                c.execute(
                    "UPDATE workspaces SET profile = ?, notes = ? WHERE id = ?",
                    (profile, notes, existing["id"]),
                )
            return Workspace(
                id=existing["id"],
                canonical_root=existing["canonical_root"],
                registered_at=existing["registered_at"],
                profile=profile if profile != existing["profile"] else existing["profile"],
                notes=notes if notes != existing["notes"] else existing["notes"],
            )
        c.execute(
            "INSERT INTO workspaces (id, canonical_root, registered_at, profile, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (new_id, canonical, now, profile, notes),
        )
        return Workspace(
            id=new_id,
            canonical_root=canonical,
            registered_at=now,
            profile=profile,
            notes=notes,
        )

    if conn is None:
        with get_connection() as c:
            return _do(c)
    return _do(conn)


def resolve(workspace_id: str, conn: sqlite3.Connection | None = None) -> Workspace:
    """Look up a workspace by id.

    Raises :class:`WorkspaceNotRegistered` if the id is unknown.
    """
    if not workspace_id:
        raise WorkspaceNotRegistered("workspace_id is empty")

    def _do(c: sqlite3.Connection) -> Workspace:
        row = c.execute(
            "SELECT id, canonical_root, registered_at, profile, notes "
            "FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise WorkspaceNotRegistered(
                f"workspace_id={workspace_id!r} is not registered"
            )
        return Workspace(
            id=row["id"],
            canonical_root=row["canonical_root"],
            registered_at=row["registered_at"],
            profile=row["profile"],
            notes=row["notes"],
        )

    if conn is None:
        with get_connection() as c:
            return _do(c)
    return _do(conn)


def list_workspaces(conn: sqlite3.Connection | None = None) -> list[Workspace]:
    """Return every registered workspace, ordered by registration time."""
    def _do(c: sqlite3.Connection) -> list[Workspace]:
        rows = c.execute(
            "SELECT id, canonical_root, registered_at, profile, notes "
            "FROM workspaces ORDER BY registered_at ASC"
        ).fetchall()
        return [
            Workspace(
                id=r["id"],
                canonical_root=r["canonical_root"],
                registered_at=r["registered_at"],
                profile=r["profile"],
                notes=r["notes"],
            )
            for r in rows
        ]

    if conn is None:
        with get_connection() as c:
            return _do(c)
    return _do(conn)


# --- Helper used by tools to verify a user-supplied path ------------------


def assert_inside_workspace(workspace: Workspace, path: str | os.PathLike[str]) -> str:
    """Resolve ``path`` and assert it's inside ``workspace``.

    Returns the canonical absolute path on success. Raises
    :class:`InvalidPath` otherwise.

    Use this in every tool that accepts a user-supplied file path so a
    single escape attempt fails uniformly across the tool surface.
    """
    real = canonicalize(path)
    if not workspace.contains(real):
        raise InvalidPath(
            f"path escapes workspace {workspace.id!r}: {real!r}"
        )
    return real


# Re-export Path under a stable name so callers don't have to import
# pathlib themselves.
__all__ = [
    "InvalidPath",
    "Path",
    "Workspace",
    "WorkspaceNotRegistered",
    "assert_inside_workspace",
    "canonicalize",
    "list_workspaces",
    "register",
    "resolve",
]
