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

# Schema v3 - policy-and-safety: one-shot approvals and deny-rule telemetry.
APPROVALS_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS approvals (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL,
    requested_capability TEXT NOT NULL,
    action_digest       TEXT NOT NULL,
    status              TEXT NOT NULL,
    requested_at        INTEGER NOT NULL,
    expires_at          INTEGER NOT NULL,
    approved_at         INTEGER,
    consumed_at         INTEGER
);
"""

APPROVALS_INDEX_V3 = (
    "CREATE INDEX IF NOT EXISTS idx_approvals_workspace ON approvals(workspace_id);",
    "CREATE INDEX IF NOT EXISTS idx_approvals_status_expiry ON approvals(status, expires_at);",
)

RULE_HIT_STATS_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS rule_hit_stats (
    rule_id             TEXT PRIMARY KEY,
    hit_count           INTEGER NOT NULL,
    last_hit_at         INTEGER NOT NULL,
    last_hit_cmd        TEXT NOT NULL
);
"""

BACKGROUND_PROCESSES_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS background_processes (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL,
    preset              TEXT NOT NULL,
    command             TEXT NOT NULL,
    cwd                 TEXT NOT NULL,
    pid                 INTEGER NOT NULL,
    log_handle          TEXT NOT NULL,
    port                INTEGER,
    started_at          INTEGER NOT NULL,
    persistent          INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL,
    exit_code           INTEGER,
    finished_at         INTEGER
);
"""

BACKGROUND_PROCESSES_INDEXES_V4 = (
    "CREATE INDEX IF NOT EXISTS idx_background_started ON background_processes(started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_background_workspace ON background_processes(workspace_id);",
    "CREATE INDEX IF NOT EXISTS idx_background_pid ON background_processes(pid);",
)

# Schema v4 (bumped again by change-6): authorized-windows table. The
# earlier ``background_processes`` migration is still v4; this addition
# keeps the schema version at 5 because we introduce a new table rather
# than mutate an existing one. A future change that needs to alter an
# existing table will bump to 6.
AUTHORIZED_WINDOWS_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS authorized_windows (
    id          TEXT PRIMARY KEY,
    process     TEXT NOT NULL,
    pid         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    hwnd        INTEGER NOT NULL,
    issued_at   INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
"""

AUTHORIZED_WINDOWS_INDEXES_V5 = (
    "CREATE INDEX IF NOT EXISTS idx_authorized_windows_expiry ON authorized_windows(expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_authorized_windows_revoked ON authorized_windows(revoked);",
)

CURRENT_SCHEMA_VERSION = 5


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

    if current < 3:
        _log.info("applying schema migration -> v3 (approvals + rule hit stats)")
        conn.execute(APPROVALS_SCHEMA_V3)
        for ddl in APPROVALS_INDEX_V3:
            conn.execute(ddl)
        conn.execute(RULE_HIT_STATS_SCHEMA_V3)
        _write_schema_version(conn, 3)
        current = 3

    if current < 4:
        _log.info("applying schema migration -> v4 (managed processes + streaming artifacts)")
        conn.execute(BACKGROUND_PROCESSES_SCHEMA_V4)
        for ddl in BACKGROUND_PROCESSES_INDEXES_V4:
            conn.execute(ddl)
        artifact_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(artifacts)").fetchall()
        }
        if "sealed" not in artifact_columns:
            conn.execute(
                "ALTER TABLE artifacts ADD COLUMN sealed INTEGER NOT NULL DEFAULT 1"
            )
        _write_schema_version(conn, 4)
        current = 4

    if current < 5:
        _log.info("applying schema migration -> v5 (authorized windows)")
        conn.execute(AUTHORIZED_WINDOWS_SCHEMA_V5)
        for ddl in AUTHORIZED_WINDOWS_INDEXES_V5:
            conn.execute(ddl)
        _write_schema_version(conn, 5)
        current = 5

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
