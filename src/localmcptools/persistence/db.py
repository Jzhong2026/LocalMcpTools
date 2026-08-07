"""SQLite + WAL init + migrations.

This module owns:

- The connection factory :func:`get_connection`.
- The :func:`init_db` migration runner (idempotent; safe to call on every boot).
- The current schema (``CALLS_SCHEMA_V1``).

Every consumer that needs SQLite goes through :func:`get_connection` so we
can monkeypatch it in tests without touching the filesystem.

Why WAL: readers never block writers, and the audit log is mostly reads
(UI/inspection) with sparse writes (one per tool call). Per the openspec
design, journal_mode is set on every connection.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..config.paths import audit_db_path

_log = logging.getLogger(__name__)


# --- Schema ----------------------------------------------------------------

# Schema version 1: just the ``calls`` table.
# This DDL is frozen for the spike. Additions (artifacts, approvals, runs)
# land in later changes with a new ``SCHEMA_V2`` and a migration block in
# :func:`_migrate`.
CALLS_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS calls (
    id              TEXT PRIMARY KEY,
    timestamp       INTEGER NOT NULL,
    agent           TEXT,
    client_instance TEXT,
    tool            TEXT NOT NULL,
    workspace_id    TEXT,
    profile         TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    approval_id     TEXT,
    run_id          TEXT NOT NULL,
    args_redacted   TEXT NOT NULL,
    ok              INTEGER NOT NULL,
    error_code      TEXT,
    error_message   TEXT,
    blocked_by      TEXT,
    severity        TEXT,
    exit_code       INTEGER,
    stdout_bytes    INTEGER,
    stderr_bytes    INTEGER,
    duration_ms     INTEGER NOT NULL,
    log_path        TEXT,
    status          TEXT NOT NULL,
    pid             INTEGER,
    finished_at     INTEGER
);
"""

CALLS_INDEXES_V1 = (
    "CREATE INDEX IF NOT EXISTS idx_calls_timestamp ON calls(timestamp DESC);",
    "CREATE INDEX IF NOT EXISTS idx_calls_tool ON calls(tool);",
)

# Schema v2 — change-2 (core-shell-and-audit) adds the workspace
# registry and the artifact directory. The DDL is split per version
# so older databases migrate cleanly.
WORKSPACES_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS workspaces (
    id              TEXT PRIMARY KEY,
    canonical_root  TEXT NOT NULL,
    registered_at   INTEGER NOT NULL,
    profile         TEXT NOT NULL DEFAULT 'observe',
    notes           TEXT
);
"""

WORKSPACES_INDEX_V2 = (
    "CREATE INDEX IF NOT EXISTS idx_workspaces_canonical ON workspaces(canonical_root);",
)

ARTIFACTS_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS artifacts (
    handle          TEXT PRIMARY KEY,
    path            TEXT NOT NULL,
    call_id         TEXT NOT NULL,
    bytes_total     INTEGER NOT NULL,
    line_count      INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    expires_at      INTEGER,
    sensitive       INTEGER NOT NULL DEFAULT 0
);
"""

ARTIFACTS_INDEX_V2 = (
    "CREATE INDEX IF NOT EXISTS idx_artifacts_call ON artifacts(call_id);",
)

SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""

CURRENT_SCHEMA_VERSION = 2


# --- Connection factory ---------------------------------------------------


def _resolve_path(path: Path | None) -> Path:
    return path if path is not None else audit_db_path()


def get_connection(path: Path | None = None) -> sqlite3.Connection:
    """Open (and initialise) a connection to the audit database.

    The connection has ``row_factory=sqlite3.Row`` so callers can use
    column names. WAL is set on every connection — this is harmless if
    already set and protects against the file being opened by a process
    that doesn't run :func:`init_db` first.
    """
    db_path = _resolve_path(path)
    # Ensure parent dir exists; the spike is stdio so we expect a single
    # process per machine, but a second writer is still possible (UI).
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(db_path),
        timeout=10.0,
        isolation_level=None,  # autocommit; explicit BEGINs in callers
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    # WAL must come first; older connections on the same file might be in
    # rollback-journal mode and we don't want to silently lose that.
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Context-manager wrapper around :func:`get_connection` that closes on exit."""
    conn = get_connection(path)
    try:
        yield conn
    finally:
        conn.close()


# --- Migration runner ------------------------------------------------------


def init_db(path: Path | None = None) -> None:
    """Idempotent migration runner.

    Safe to call on every boot. Brings the schema to
    :data:`CURRENT_SCHEMA_VERSION`. Migrations are append-only — never
    rewrite a past migration; add a new one.
    """
    with connection(path) as conn:
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA_VERSION_TABLE)

    current = _read_schema_version(conn)

    if current < 1:
        _log.info("applying schema migration -> v1 (calls table)")
        conn.execute(CALLS_SCHEMA_V1)
        for ddl in CALLS_INDEXES_V1:
            conn.execute(ddl)
        _write_schema_version(conn, 1)
        current = 1

    if current < 2:
        _log.info("applying schema migration -> v2 (workspaces + artifacts)")
        conn.execute(WORKSPACES_SCHEMA_V2)
        for ddl in WORKSPACES_INDEX_V2:
            conn.execute(ddl)
        conn.execute(ARTIFACTS_SCHEMA_V2)
        for ddl in ARTIFACTS_INDEX_V2:
            conn.execute(ddl)
        _write_schema_version(conn, 2)
        current = 2

    if current != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"schema migration incomplete: have {current}, "
            f"expected {CURRENT_SCHEMA_VERSION}"
        )


def _read_schema_version(conn: sqlite3.Connection) -> int:
    """Return the *highest* recorded schema version, or 0 if none.

    We use ``MAX`` rather than ``LIMIT 1`` because migrations can
    insert a new row with the new version while leaving the old one
    in place if the upsert is ever bypassed (older builds used
    ``INSERT`` directly). The highest value is always the right one
    to drive the next ``if current < N:`` check.
    """
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def _write_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
        (version,),
    )


# --- Diagnostics ----------------------------------------------------------


def is_initialised(path: Path | None = None) -> bool:
    """True if the database file exists *and* the calls table is present.

    Used by integration tests and the eventual ``localmcptools status``
    CLI subcommand.
    """
    db_path = _resolve_path(path)
    if not db_path.exists():
        return False
    try:
        with connection(db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='calls'"
            ).fetchone()
            return row is not None
    except sqlite3.DatabaseError:
        return False
