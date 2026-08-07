"""Policy gates for the controlled shell tool (no real command execution)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from localmcptools.execution.service import ToolExecutionService
from localmcptools.persistence import db
from localmcptools.policy.approval import approve, request
from localmcptools.tools.shell import shell_run_command
from localmcptools.workspaces.registry import Workspace, register

ShellWrapper = tuple[Path, Workspace, Callable[..., dict[str, Any]]]


@pytest.fixture
def shell_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ShellWrapper:
    database = tmp_path / "audit.sqlite"
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(db, "audit_db_path", lambda: database)
    db.init_db(database)
    with db.connection(database) as conn:
        workspace = register(root, conn=conn)
    service = ToolExecutionService(audit_path=database)
    return database, workspace, service.register("shell.run_command", shell_run_command, param_names=("workspace_id", "cmd", "approval_id"))


def test_observe_workspace_is_denied_before_requesting_approval(shell_wrapper: ShellWrapper) -> None:
    _database, workspace, wrapper = shell_wrapper
    response = wrapper(workspace_id=workspace.id, cmd="Write-Output hi")
    assert response["error"]["code"] == "insufficient_capability"


def test_workspace_exec_without_approval_returns_pending_request(shell_wrapper: ShellWrapper) -> None:
    database, workspace, wrapper = shell_wrapper
    with db.connection(database) as conn:
        conn.execute("UPDATE workspaces SET profile = 'workspace_exec' WHERE id = ?", (workspace.id,))
    response = wrapper(workspace_id=workspace.id, cmd="Write-Output hi")
    assert response["error"]["code"] == "approval_required"
    approval_id = response["error"]["approval_id"]
    with db.connection(database) as conn:
        assert conn.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()["status"] == "pending"


def test_rule_rejection_does_not_consume_approved_request(shell_wrapper: ShellWrapper) -> None:
    database, workspace, wrapper = shell_wrapper
    command = "Format-Volume -DriveLetter C"
    with db.connection(database) as conn:
        conn.execute("UPDATE workspaces SET profile = 'workspace_exec' WHERE id = ?", (workspace.id,))
        approval = request(workspace.id, "workspace_exec:shell.run_command", {"workspace_id": workspace.id, "cmd": command}, profile="workspace_exec", conn=conn)
        approve(approval.id, conn=conn)
    response = wrapper(workspace_id=workspace.id, cmd=command, approval_id=approval.id)
    assert response["error"]["code"] == "denied_by_rule"
    with db.connection(database) as conn:
        assert conn.execute("SELECT status FROM approvals WHERE id = ?", (approval.id,)).fetchone()["status"] == "approved"
