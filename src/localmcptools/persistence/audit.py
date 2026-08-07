"""Audit log recorder for the spike.

Two operations only:

- :func:`record_start` — insert a row with ``status='running'``.
- :func:`record_finish` — update that row with the result.

Anything fancier (artifacts, approvals, runs) lands in later changes with
its own module. ``audit.py`` is intentionally small so the spike proves
the round-trip without committing to a permanent shape.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import artifacts
from . import db
from .db import get_connection

_log = logging.getLogger(__name__)


# --- Status constants ------------------------------------------------------

STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_INVALID_ARGS = "invalid_args"


# --- Public API ------------------------------------------------------------


def record_start(
    call_id: str,
    tool: str,
    args_redacted: dict[str, Any] | str,
    run_id: str,
    profile: str,
    policy_version: str,
    *,
    agent: str | None = None,
    client_instance: str | None = None,
    workspace_id: str | None = None,
    pid: int | None = None,
    conn: sqlite3.Connection | None = None,
    path: "Path | None" = None,
) -> None:
    """Insert a ``running`` row.

    ``args_redacted`` may be either a dict (will be JSON-encoded) or an
    already-encoded string. The spike enforces redaction at higher layers;
    the audit module just stores whatever it is given. **Do not** pass
    raw secrets here — there is no second layer of defence by design.

    ``path`` mirrors :func:`db.get_connection` — it lets tests target a
    temp database. Production callers pass ``None`` and get the default
    ``%APPDATA%\\LocalMcpTools\\audit.sqlite``.
    """
    if isinstance(args_redacted, dict):
        args_json = json.dumps(args_redacted, ensure_ascii=False)
    else:
        args_json = args_redacted

    payload = (
        call_id,
        _now_ms(),
        agent,
        client_instance,
        tool,
        workspace_id,
        profile,
        policy_version,
        None,  # approval_id — spike
        run_id,
        args_json,
        # ok / error / status — set on finish
        0,  # ok default (overwritten on finish)
        None,  # error_code
        None,  # error_message
        None,  # blocked_by
        None,  # severity
        None,  # exit_code
        None,  # stdout_bytes
        None,  # stderr_bytes
        0,  # duration_ms — placeholder, computed on finish
        None,  # log_path
        STATUS_RUNNING,
        pid,
        None,  # finished_at — set on finish
    )

    sql = (
        "INSERT INTO calls ("
        "id, timestamp, agent, client_instance, tool, workspace_id, profile, "
        "policy_version, approval_id, run_id, args_redacted, ok, error_code, "
        "error_message, blocked_by, severity, exit_code, stdout_bytes, "
        "stderr_bytes, duration_ms, log_path, status, pid, finished_at"
        ") VALUES ("
        "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?"
        ")"
    )

    def _do(c: sqlite3.Connection) -> None:
        c.execute(sql, payload)

    if conn is None:
        # Lazily ensure the schema exists; first-ever call on a fresh
        # machine would otherwise crash with "no such table: calls".
        db.init_db(path)
        with get_connection(path) as c:
            _do(c)
    else:
        _do(conn)


def record_finish(
    call_id: str,
    *,
    ok: bool,
    error_code: str | None,
    error_message: str | None,
    duration_ms: int,
    exit_code: int | None = None,
    stdout_bytes: int | None = None,
    stderr_bytes: int | None = None,
    log_path: str | None = None,
    blocked_by: str | None = None,
    severity: str | None = None,
    approval_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    path: "Path | None" = None,
) -> None:
    """Update the row created by :func:`record_start`.

    ``status`` is derived: ``success`` if ``ok``, otherwise mapped from
    ``error_code`` (``invalid_args`` stays as ``invalid_args``; everything
    else becomes ``failed``).

    Contract on ``log_path``:

    - When ``ok=True`` and ``log_path`` is non-empty, it **must** be an
      artifact handle shaped ``art://YYYY-MM-DD/calls/<id>.log``. Absolute
      filesystem paths and any other string are rejected with
      :class:`ValueError`. This is the OpenSpec contract that the
      agent only ever sees opaque handles; the on-disk path stays
      internal to :mod:`localmcptools.persistence.artifacts`.
    - When ``ok=False``, ``log_path`` is ignored (failure rows don't
      carry an artifact handle). Tests that exercise the failure path
      may pass ``None``.
    """
    # Validate the handle contract before touching the DB. A bad
    # ``log_path`` on the success path is a programmer error, not a
    # runtime condition we want to persist.
    #
    # ``None`` is allowed (the call simply didn't produce an artifact,
    # which is fine for tools whose output stays inline). Any *string*
    # is required to be a parseable ``art://...`` handle — including
    # the empty string, which is not a valid handle and would
    # otherwise silently persist as a 4-byte garbage row.
    if ok and log_path is not None:
        try:
            artifacts.parse_handle(log_path)
        except artifacts.ArtifactNotFound as exc:
            raise ValueError(
                f"record_finish: log_path must be an 'art://...' handle "
                f"on success, got {log_path!r} ({exc})"
            ) from exc

    status = _status_from(ok, error_code)
    finished_at = _now_ms()

    fields = {
        "ok": 1 if ok else 0,
        "error_code": error_code,
        "error_message": error_message,
        "blocked_by": blocked_by,
        "severity": severity,
        "exit_code": exit_code,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
        "duration_ms": max(0, int(duration_ms)),
        "log_path": log_path,
        "approval_id": approval_id,
        "status": status,
        "finished_at": finished_at,
    }

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params: list[Any] = list(fields.values()) + [call_id]
    sql = f"UPDATE calls SET {set_clause} WHERE id = ?"

    def _do(c: sqlite3.Connection) -> None:
        cur = c.execute(sql, params)
        if cur.rowcount == 0:
            # Refuse to silently invent a row; the caller should have
            # called ``record_start`` first.
            raise LookupError(f"record_finish: no audit row for call_id={call_id!r}")

    if conn is None:
        # Defensive: also init here, in case record_finish is somehow
        # called without record_start having touched the DB yet.
        db.init_db(path)
        with get_connection(path) as c:
            _do(c)
    else:
        _do(conn)


# --- Helpers ---------------------------------------------------------------


def _now_ms() -> int:
    """Unix milliseconds. Monotonic enough for the spike."""
    return int(time.time() * 1000)


def _status_from(ok: bool, error_code: str | None) -> str:
    if ok:
        return STATUS_SUCCESS
    if error_code == "invalid_args":
        return STATUS_INVALID_ARGS
    return STATUS_FAILED