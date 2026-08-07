"""Tests for :mod:`localmcptools.persistence.db` and :mod:`.audit`.

Covers the spike DoD bullets:

- insert then update; ``finished_at`` is set
- timestamps monotonic (``finished_at >= timestamp``)
- ``record_finish`` on a missing call_id raises (caller forgot ``record_start``)
"""

from __future__ import annotations

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


# --- change-2 / 1.4: audit extensions --------------------------------------


def test_record_start_with_client_instance_and_workspace(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    audit.record_start(
        call_id=call_id,
        tool="workspace.inspect",
        args_redacted="{}",
        run_id="r-1",
        profile="observe",
        policy_version="v2",
        client_instance="codebuddy-xyz",
        workspace_id="ws-abc",
        conn=None,
        path=fresh_db,
    )
    with db.connection(fresh_db) as conn:
        row = conn.execute(
            "SELECT client_instance, workspace_id, approval_id FROM calls WHERE id = ?",
            (call_id,),
        ).fetchone()
    assert row["client_instance"] == "codebuddy-xyz"
    assert row["workspace_id"] == "ws-abc"
    assert row["approval_id"] is None


def test_record_finish_sets_approval_id(fresh_db: Path) -> None:
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    audit.record_start(
        call_id=call_id,
        tool="workspace.inspect",
        args_redacted="{}",
        run_id="r-2",
        profile="observe",
        policy_version="v2",
        path=fresh_db,
    )
    audit.record_finish(
        call_id,
        ok=True,
        error_code=None,
        error_message=None,
        duration_ms=10,
        approval_id="appr-xyz-001",
        path=fresh_db,
    )
    with db.connection(fresh_db) as conn:
        row = conn.execute(
            "SELECT approval_id FROM calls WHERE id = ?", (call_id,)
        ).fetchone()
    assert row["approval_id"] == "appr-xyz-001"


def test_record_finish_log_path_accepts_artifact_handle(fresh_db: Path) -> None:
    """``log_path`` must be a valid ``art://...`` handle on success."""
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    audit.record_start(
        call_id=call_id,
        tool="workspace.inspect",
        args_redacted="{}",
        run_id="r-3",
        profile="observe",
        policy_version="v2",
        path=fresh_db,
    )
    handle = "art://2026-08-07/calls/abc123.log"
    audit.record_finish(
        call_id,
        ok=True,
        error_code=None,
        error_message=None,
        duration_ms=10,
        log_path=handle,
        path=fresh_db,
    )
    with db.connection(fresh_db) as conn:
        row = conn.execute(
            "SELECT log_path FROM calls WHERE id = ?", (call_id,)
        ).fetchone()
    assert row["log_path"] == handle


def test_record_finish_rejects_absolute_log_path(fresh_db: Path) -> None:
    """OpenSpec contract: absolute paths in ``log_path`` are rejected.

    A success row must never expose a host filesystem path; only an
    ``art://...`` handle. This pins the contract — if a future
    change accidentally widens the accepted forms, this test fires.
    """
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    audit.record_start(
        call_id=call_id,
        tool="workspace.inspect",
        args_redacted="{}",
        run_id="r-3b",
        profile="observe",
        policy_version="v2",
        path=fresh_db,
    )
    with pytest.raises(ValueError, match="art://"):
        audit.record_finish(
            call_id,
            ok=True,
            error_code=None,
            error_message=None,
            duration_ms=10,
            log_path=r"D:\AI\Projects\LocalMcpTools\secrets.log",
            path=fresh_db,
        )


def test_record_finish_rejects_malformed_handle(fresh_db: Path) -> None:
    """Anything that isn't a parseable ``art://...`` handle is rejected."""
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    audit.record_start(
        call_id=call_id,
        tool="workspace.inspect",
        args_redacted="{}",
        run_id="r-3c",
        profile="observe",
        policy_version="v2",
        path=fresh_db,
    )
    for bad in (
        "not-a-handle",
        "art://not-a-date/calls/x.log",
        "art://2026-08-07/notcalls/x.log",
        "art://2026-08-07/calls/x.txt",
        "",
    ):
        with pytest.raises(ValueError, match="art://"):
            audit.record_finish(
                call_id,
                ok=True,
                error_code=None,
                error_message=None,
                duration_ms=10,
                log_path=bad,
                path=fresh_db,
            )


def test_record_finish_failure_ignores_log_path(fresh_db: Path) -> None:
    """On ``ok=False`` the ``log_path`` is ignored — failure rows don't
    carry an artifact handle, so we don't validate the format either."""
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    audit.record_start(
        call_id=call_id,
        tool="workspace.inspect",
        args_redacted="{}",
        run_id="r-3d",
        profile="observe",
        policy_version="v2",
        path=fresh_db,
    )
    # A "weird" log_path is fine on the failure path; we don't persist
    # the success handle there anyway.
    audit.record_finish(
        call_id,
        ok=False,
        error_code="internal_error",
        error_message="boom",
        duration_ms=10,
        log_path=None,
        path=fresh_db,
    )
    with db.connection(fresh_db) as conn:
        row = conn.execute(
            "SELECT log_path, ok, status FROM calls WHERE id = ?", (call_id,)
        ).fetchone()
    assert row["ok"] == 0
    assert row["status"] == "failed"
    assert row["log_path"] is None


def test_full_field_round_trip(fresh_db: Path) -> None:
    """One end-to-end call that exercises every column populated by change-2."""
    db.init_db(fresh_db)
    call_id = str(uuid.uuid4())
    audit.record_start(
        call_id=call_id,
        tool="shell.run_command",  # future tool, just for the test
        args_redacted='{"command": "echo hi"}',
        run_id=str(uuid.uuid4()),
        profile="workspace_exec",  # OpenSpec-canonical profile name
        policy_version="v2",
        agent="codebuddy",
        client_instance="codebuddy-xyz",
        workspace_id="ws-abc",
        pid=12345,
        path=fresh_db,
    )
    audit.record_finish(
        call_id,
        ok=True,
        error_code=None,
        error_message=None,
        duration_ms=42,
        exit_code=0,
        stdout_bytes=8,
        stderr_bytes=0,
        log_path="art://2026-08-07/calls/" + call_id + ".log",
        approval_id="appr-test-1",
        path=fresh_db,
    )
    with db.connection(fresh_db) as conn:
        row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()

    # Every column populated.
    expected = {
        "tool": "shell.run_command",
        "agent": "codebuddy",
        "client_instance": "codebuddy-xyz",
        "workspace_id": "ws-abc",
        "profile": "workspace_exec",
        "policy_version": "v2",
        "approval_id": "appr-test-1",
        "ok": 1,
        "exit_code": 0,
        "stdout_bytes": 8,
        "stderr_bytes": 0,
        "duration_ms": 42,
        "status": "success",
        "pid": 12345,
        "log_path": "art://2026-08-07/calls/" + call_id + ".log",
    }
    for col, value in expected.items():
        assert row[col] == value, f"{col}: {row[col]!r} != {value!r}"
    # Timing invariant.
    assert row["finished_at"] >= row["timestamp"]
