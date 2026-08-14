from __future__ import annotations

from pathlib import Path

import pytest

from localmcptools.execution.service import ToolExecutionService
from localmcptools.persistence import db
from localmcptools.policy.approval import approve
from localmcptools.process.manager import ManagedProcess
from localmcptools.tools.process import (
    process_find_by_port,
    process_kill_not_exposed,
    process_start_dev_server,
)
from localmcptools.tools.shell import shell_run_command
from localmcptools.workspaces.registry import register


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "audit.sqlite"
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "audit_db_path", lambda: database)
    db.init_db(database)
    with db.connection(database) as conn:
        item = register(root, conn=conn)
    return database, item


def test_start_is_denied_for_observe(workspace) -> None:
    database, item = workspace
    wrapper = ToolExecutionService(audit_path=database).register(
        "process.start_dev_server",
        process_start_dev_server,
        param_names=("workspace_id", "preset", "args"),
    )
    response = wrapper(workspace_id=item.id, preset="node-vite", args=[])
    assert response["error"]["code"] == "insufficient_capability"


def test_unknown_preset_is_reported_after_profile_gate(workspace) -> None:
    database, item = workspace
    with db.connection(database) as conn:
        conn.execute("UPDATE workspaces SET profile='managed_process' WHERE id=?", (item.id,))
    wrapper = ToolExecutionService(audit_path=database).register(
        "process.start_dev_server",
        process_start_dev_server,
        param_names=("workspace_id", "preset", "args"),
    )
    response = wrapper(workspace_id=item.id, preset="raw-shell", args=[])
    assert response["error"]["code"] == "unknown_preset"


def test_approved_start_returns_managed_contract(workspace, monkeypatch) -> None:
    database, item = workspace
    with db.connection(database) as conn:
        conn.execute("UPDATE workspaces SET profile='managed_process' WHERE id=?", (item.id,))
    fake = ManagedProcess(
        id="mp-contract",
        workspace_id=item.id,
        preset="node-vite",
        command="npx vite",
        cwd=item.canonical_root,
        pid=4242,
        log_handle="art://2026-08-07/calls/fake.log",
        port=None,
        started_at=1,
        persistent=False,
        status="running",
        exit_code=None,
        finished_at=None,
    )
    monkeypatch.setattr(
        "localmcptools.tools.process.background.start_dev_server", lambda **kwargs: fake
    )
    wrapper = ToolExecutionService(audit_path=database).register(
        "process.start_dev_server",
        process_start_dev_server,
        param_names=("workspace_id", "preset", "args", "approval_id"),
    )
    pending = wrapper(workspace_id=item.id, preset="node-vite", args=[])
    approval_id = pending["error"]["approval_id"]
    with db.connection(database) as conn:
        approve(approval_id, conn=conn)
    response = wrapper(workspace_id=item.id, preset="node-vite", args=[], approval_id=approval_id)
    assert response["ok"] is True
    assert response["data"] == {
        "id": "mp-contract",
        "pid": 4242,
        "command_resolved": "npx vite",
        "log_handle": fake.log_handle,
        "port": None,
    }
    with db.connection(database) as conn:
        audit = conn.execute(
            "SELECT approval_id FROM calls WHERE id=?", (response["meta"]["audit_id"],)
        ).fetchone()
    assert audit["approval_id"] == approval_id


def test_long_shell_timeout_redirects_to_managed_process(workspace) -> None:
    database, item = workspace
    with db.connection(database) as conn:
        conn.execute("UPDATE workspaces SET profile='workspace_exec' WHERE id=?", (item.id,))
    wrapper = ToolExecutionService(audit_path=database).register(
        "shell.run_command",
        shell_run_command,
        param_names=("workspace_id", "cmd", "timeout_ms"),
    )
    response = wrapper(workspace_id=item.id, cmd="python -m uvicorn app:app", timeout_ms=60_000)
    assert response["error"]["code"] == "use_start_dev_server"
    assert "python-uvicorn" in response["meta"]["next_actions"][0]


def test_missing_port_is_typed(monkeypatch) -> None:
    monkeypatch.setattr("localmcptools.tools.process.ports.find_by_port", lambda port: None)
    wrapper = ToolExecutionService().register(
        "process.find_by_port", process_find_by_port, param_names=("port",)
    )
    assert wrapper(port=65000)["error"]["code"] == "port_not_found"


def test_manually_wired_arbitrary_kill_is_not_exposed() -> None:
    wrapper = ToolExecutionService().register(
        "process.kill", process_kill_not_exposed, param_names=("pid",)
    )
    assert wrapper(pid=1234)["error"]["code"] == "not_exposed"
