"""Approval lifecycle, action binding and schema-v3 persistence tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from localmcptools.persistence import db
from localmcptools.policy.approval import (
    ApprovalDigestMismatch,
    ApprovalExpired,
    ApprovalNotApproved,
    approve,
    consume,
    expire_due,
    request,
)
from localmcptools.policy.digest import digest_for


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "audit.sqlite"
    db.init_db(path)
    return path


def test_digest_is_stable_across_key_order_and_ignores_retry_handle() -> None:
    one = digest_for(
        "shell.run_command", {"cmd": "echo hi", "timeout_ms": 1}, "ws", "workspace_exec"
    )
    two = digest_for(
        "shell.run_command",
        {"timeout_ms": 1, "approval_id": "a", "cmd": "echo hi"},
        "ws",
        "workspace_exec",
    )
    assert one == two


def test_approved_matching_action_is_consumed_once(database: Path) -> None:
    with db.connection(database) as conn:
        item = request(
            "ws",
            "workspace_exec:shell.run_command",
            {"cmd": "echo hi"},
            profile="workspace_exec",
            conn=conn,
        )
        assert approve(item.id, conn=conn)
        assert consume(item.id, item.action_digest, conn=conn)
        with pytest.raises(ApprovalNotApproved):
            consume(item.id, item.action_digest, conn=conn)
        row = conn.execute(
            "SELECT status, consumed_at FROM approvals WHERE id = ?", (item.id,)
        ).fetchone()
    assert row["status"] == "consumed"
    assert row["consumed_at"] is not None


def test_digest_mismatch_does_not_consume_approval(database: Path) -> None:
    with db.connection(database) as conn:
        item = request(
            "ws",
            "workspace_exec:shell.run_command",
            {"cmd": "echo hi"},
            profile="workspace_exec",
            conn=conn,
        )
        approve(item.id, conn=conn)
        with pytest.raises(ApprovalDigestMismatch):
            consume(item.id, "different", conn=conn)
        status = conn.execute("SELECT status FROM approvals WHERE id = ?", (item.id,)).fetchone()[
            "status"
        ]
    assert status == "approved"


def test_expired_approval_is_marked_expired(database: Path) -> None:
    with db.connection(database) as conn:
        item = request(
            "ws",
            "workspace_exec:shell.run_command",
            {"cmd": "echo hi"},
            profile="workspace_exec",
            ttl_ms=1,
            conn=conn,
        )
        time.sleep(0.01)
        with pytest.raises(ApprovalExpired):
            approve(item.id, conn=conn)
        assert expire_due(conn=conn) == 0
        status = conn.execute("SELECT status FROM approvals WHERE id = ?", (item.id,)).fetchone()[
            "status"
        ]
    assert status == "expired"


def test_schema_v3_contains_approval_and_rule_tables(database: Path) -> None:
    with db.connection(database) as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"approvals", "rule_hit_stats"}.issubset(tables)
