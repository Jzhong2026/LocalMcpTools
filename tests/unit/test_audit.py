"""Tests for :mod:`localmcptools.persistence.db` and :mod:`.audit`.

Covers the spike DoD bullets:

- insert then update; ``finished_at`` is set
- timestamps monotonic (``finished_at >= timestamp``)
- ``record_finish`` on a missing call_id raises (caller forgot ``record_start``)
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from localmcptools.persistence import audit, db


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """Return a path to a not-yet-existing sqlite file inside tmp_path."""
    return tmp_path / "audit.sqlite"


def test_init_db_creates_calls_table(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    assert fresh_db.exists()
    assert db.is_initialised(fresh_db)


def test_init_db_is_idempotent(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    db.init_db(fresh_db)  # second call must not raise
    assert db.is_initialised(fresh_db)


def test_get_connection_uses_wal(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    with db.connection(fresh_db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


def test_schema_version_recorded(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        assert row["v"] == db.CURRENT_SCHEMA_VERSION


def test_record_start_inserts_running_row(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    # record_start manages its own connection if conn=None; ``path``
    # routes it to the temp database so we don't write to the user's
    # real %APPDATA% during a unit test.
    audit.record_start(
        call_id=call_id,
        tool="workspace.inspect",
        args_redacted={"placeholder": True},
        run_id="run-1",
        profile="observe",
        policy_version="spike-0",
        path=fresh_db,
    )
    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    assert row is not None
    assert row["tool"] == "workspace.inspect"
    assert row["profile"] == "observe"
    assert row["policy_version"] == "spike-0"
    assert row["status"] == audit.STATUS_RUNNING
    assert row["finished_at"] is None
    # Args redacted column is JSON-encoded.
    import json as _json
    assert _json.loads(row["args_redacted"]) == {"placeholder": True}


def test_record_finish_updates_row(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    with db.connection(fresh_db) as conn:
        audit.record_start(
            call_id=call_id,
            tool="workspace.inspect",
            args_redacted="{}",
            run_id="run-2",
            profile="observe",
            policy_version="spike-0",
            conn=conn,
        )
        ts_start = conn.execute(
            "SELECT timestamp FROM calls WHERE id = ?", (call_id,)
        ).fetchone()["timestamp"]
        audit.record_finish(
            call_id,
            ok=True,
            error_code=None,
            error_message=None,
            duration_ms=42,
            conn=conn,
        )

    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()

    assert row["ok"] == 1
    assert row["status"] == audit.STATUS_SUCCESS
    assert row["duration_ms"] == 42
    assert row["finished_at"] is not None
    # Monotonic.
    assert row["finished_at"] >= ts_start


def test_record_finish_marks_failed(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    with db.connection(fresh_db) as conn:
        audit.record_start(
            call_id=call_id,
            tool="workspace.inspect",
            args_redacted="{}",
            run_id="run-3",
            profile="observe",
            policy_version="spike-0",
            conn=conn,
        )
        audit.record_finish(
            call_id,
            ok=False,
            error_code="internal_error",
            error_message="boom",
            duration_ms=10,
            conn=conn,
        )
    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    assert row["ok"] == 0
    assert row["status"] == audit.STATUS_FAILED
    assert row["error_code"] == "internal_error"
    assert row["error_message"] == "boom"


def test_record_finish_invalid_args_status(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    with db.connection(fresh_db) as conn:
        audit.record_start(
            call_id=call_id,
            tool="workspace.inspect",
            args_redacted="{}",
            run_id="run-4",
            profile="observe",
            policy_version="spike-0",
            conn=conn,
        )
        audit.record_finish(
            call_id,
            ok=False,
            error_code="invalid_args",
            error_message="bad",
            duration_ms=1,
            conn=conn,
        )
    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    assert row["status"] == audit.STATUS_INVALID_ARGS


def test_record_finish_missing_call_id_raises(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    with db.connection(fresh_db) as conn:
        with pytest.raises(LookupError):
            audit.record_finish(
                str(uuid.uuid4()),  # never started
                ok=True,
                error_code=None,
                error_message=None,
                duration_ms=0,
                conn=conn,
            )


def test_indexes_exist(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    with db.connection(fresh_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='calls'"
        ).fetchall()
        names = {r["name"] for r in rows}
    assert "idx_calls_timestamp" in names
    assert "idx_calls_tool" in names