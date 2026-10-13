"""Human approval and profile grants go through the control plane or CLI.

These tests do not UPDATE the database directly. The agent-facing MCP
tools stay unable to approve themselves.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from localmcptools.cli import main
from localmcptools.control_api import router
from localmcptools.execution.runner import RunResult
from localmcptools.execution.service import ToolExecutionService
from localmcptools.persistence import db
from localmcptools.server import _register_tools
from localmcptools.tools.process import process_start_dev_server
from localmcptools.tools.shell import shell_run_command
from localmcptools.tools.ui import ui_click_element
from localmcptools.tools.workspace import workspace_register, workspace_run_test
from localmcptools.transport import SecurityContext, mount_app
from localmcptools.ui.windows import authorize


@pytest.fixture
def control_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "audit.sqlite")
    app = FastAPI()
    ctx = SecurityContext(
        token="test-token",
        origin_allowlist=("http://127.0.0.1", "http://localhost"),
    )
    mount_app(app=app, context=ctx, control_router=router, mcp_asgi_app=None, static_dir=None)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": "http://127.0.0.1"},
    )


async def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    await client.get("/api/csrf-token")
    cookie = client.cookies.get("lmcp_csrf")
    assert cookie
    return {"X-LMCP-CSRF": cookie}


def _shell(database: Path):
    return ToolExecutionService(audit_path=database).register(
        "shell.run_command",
        shell_run_command,
        param_names=("workspace_id", "cmd", "approval_id"),
    )


async def _fake_run(*_args: Any, **_kwargs: Any) -> RunResult:
    return RunResult(
        argv=("python",),
        exit_code=0,
        output="stdout: ok\n",
        stdout_bytes=3,
        stderr_bytes=0,
        timed_out=False,
    )


@pytest.mark.asyncio
async def test_http_profile_and_approval_let_shell_run(
    control_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("localmcptools.tools.shell.run", _fake_run)
    root = tmp_path / "project"
    root.mkdir()
    registered = workspace_register({"path": str(root)})
    workspace_id = registered["workspace_id"]
    assert registered["profile"] == "observe"
    database = tmp_path / "audit.sqlite"
    shell = _shell(database)

    denied = shell(workspace_id=workspace_id, cmd="Write-Output hi")
    assert denied["error"]["code"] == "insufficient_capability"

    async with _client(control_app) as client:
        headers = await _csrf(client)
        raised = await client.post(
            f"/api/workspaces/{workspace_id}/profile",
            json={"profile": "workspace_exec"},
            headers=headers,
        )
        assert raised.status_code == 200
        assert raised.json()["profile"] == "workspace_exec"

        pending = shell(workspace_id=workspace_id, cmd="Write-Output hi")
        assert pending["error"]["code"] == "approval_required"
        approval_id = pending["error"]["approval_id"]

        listed = await client.get("/api/approvals", params={"status": "pending"})
        assert listed.status_code == 200
        body = listed.json()["approvals"]
        match = next(item for item in body if item["id"] == approval_id)
        assert "Write-Output hi" in json.dumps(match["preview"])

        approved = await client.post(f"/api/approvals/{approval_id}/approve", headers=headers)
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

    again = workspace_register({"path": str(root), "profile": "observe"})
    assert again["profile"] == "workspace_exec"
    assert again["workspace_id"] == workspace_id

    ran = shell(workspace_id=workspace_id, cmd="Write-Output hi", approval_id=approval_id)
    assert ran["error"] is None, ran
    assert ran["data"]["exit_code"] == 0


@pytest.mark.asyncio
async def test_cli_profile_and_approval_let_shell_run(
    control_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("localmcptools.tools.shell.run", _fake_run)
    root = tmp_path / "project"
    root.mkdir()
    workspace_id = workspace_register({"path": str(root)})["workspace_id"]
    shell = _shell(tmp_path / "audit.sqlite")

    assert main(["set-profile", workspace_id, "workspace_exec"]) == 0
    pending = shell(workspace_id=workspace_id, cmd="Write-Output hi")
    approval_id = pending["error"]["approval_id"]
    assert main(["approvals", "--status", "pending"]) == 0
    assert main(["approve", approval_id]) == 0
    ran = shell(workspace_id=workspace_id, cmd="Write-Output hi", approval_id=approval_id)
    assert ran["error"] is None, ran


@pytest.mark.asyncio
async def test_http_approval_runs_workspace_preset(
    control_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a real approval, the preset tool must execute, not NameError."""
    monkeypatch.setattr("localmcptools.tools.workspace.run", _fake_run)
    root = tmp_path / "project"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    workspace_id = workspace_register({"path": str(root)})["workspace_id"]
    run_test = ToolExecutionService(audit_path=tmp_path / "audit.sqlite").register(
        "workspace.run_test", workspace_run_test, param_names=("workspace_id", "approval_id")
    )
    async with _client(control_app) as client:
        headers = await _csrf(client)
        raised = await client.post(
            f"/api/workspaces/{workspace_id}/profile",
            json={"profile": "workspace_exec"},
            headers=headers,
        )
        assert raised.status_code == 200
        pending = run_test(workspace_id=workspace_id)
        assert pending["error"]["code"] == "approval_required"
        approval_id = pending["error"]["approval_id"]
        approved = await client.post(f"/api/approvals/{approval_id}/approve", headers=headers)
        assert approved.status_code == 200
    ran = run_test(workspace_id=workspace_id, approval_id=approval_id)
    assert ran["error"] is None, ran
    assert ran["data"]["exit_code"] == 0


@pytest.mark.asyncio
async def test_disabling_a_rule_applies_to_shell_run_command(
    control_app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    workspace_id = workspace_register({"path": str(root)})["workspace_id"]
    shell = _shell(tmp_path / "audit.sqlite")
    command = "Format-Volume -DriveLetter C"

    async with _client(control_app) as client:
        headers = await _csrf(client)
        await client.post(
            f"/api/workspaces/{workspace_id}/profile",
            json={"profile": "workspace_exec"},
            headers=headers,
        )
        blocked = shell(workspace_id=workspace_id, cmd=command)
        assert blocked["error"]["code"] == "denied_by_rule"

        toggled = await client.patch(
            "/api/rules/block-format-volume",
            json={"enabled": False},
            headers=headers,
        )
        assert toggled.status_code == 200
        pending = shell(workspace_id=workspace_id, cmd=command)
        assert pending["error"]["code"] == "approval_required"

        await client.patch(
            "/api/rules/block-format-volume",
            json={"enabled": True},
            headers=headers,
        )
        blocked_again = shell(workspace_id=workspace_id, cmd=command)
        assert blocked_again["error"]["code"] == "denied_by_rule"


@pytest.mark.asyncio
async def test_profile_grant_opens_preset_process_and_ui_gates(
    control_app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    workspace_id = workspace_register({"path": str(root)})["workspace_id"]
    database = tmp_path / "audit.sqlite"
    service = ToolExecutionService(audit_path=database)
    run_test = service.register(
        "workspace.run_test", workspace_run_test, param_names=("workspace_id", "approval_id")
    )
    start = service.register(
        "process.start_dev_server",
        process_start_dev_server,
        param_names=("workspace_id", "preset", "args", "approval_id"),
    )
    click = service.register(
        "ui.click_element",
        ui_click_element,
        param_names=("workspace_id", "window_id", "x", "y", "verify_with", "approval_id"),
    )
    window = authorize(hwnd=1, process="notepad", pid=1, title="notes")

    assert run_test(workspace_id=workspace_id)["error"]["code"] == "insufficient_capability"
    assert (
        start(workspace_id=workspace_id, preset="node-vite", args=[])["error"]["code"]
        == "insufficient_capability"
    )
    assert (
        click(
            workspace_id=workspace_id,
            window_id=window.id,
            x=1,
            y=1,
            verify_with=[{"kind": "ocr", "expected": "ok"}],
        )["error"]["code"]
        == "insufficient_capability"
    )

    async with _client(control_app) as client:
        headers = await _csrf(client)
        for profile, call in (
            ("workspace_exec", lambda: run_test(workspace_id=workspace_id)),
            (
                "managed_process",
                lambda: start(workspace_id=workspace_id, preset="node-vite", args=[]),
            ),
            (
                "interactive_ui",
                lambda: click(
                    workspace_id=workspace_id,
                    window_id=window.id,
                    x=1,
                    y=1,
                    verify_with=[{"kind": "ocr", "expected": "ok"}],
                ),
            ),
        ):
            response = await client.post(
                f"/api/workspaces/{workspace_id}/profile",
                json={"profile": profile},
                headers=headers,
            )
            assert response.status_code == 200
            assert call()["error"]["code"] == "approval_required"


def test_mcp_tools_cannot_approve_or_set_a_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    database = tmp_path / "audit.sqlite"
    db.init_db(database)
    service = ToolExecutionService(audit_path=database)
    _register_tools(service)
    names = set(service.tool_names())
    assert "shell.run_command" in names
    assert not any("approv" in name or "profile" in name for name in names)


@pytest.mark.asyncio
async def test_profile_and_approval_reject_unknown_targets(
    control_app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    workspace_id = workspace_register({"path": str(root)})["workspace_id"]
    async with _client(control_app) as client:
        headers = await _csrf(client)
        missing = await client.post(
            "/api/workspaces/missing/profile",
            json={"profile": "workspace_exec"},
            headers=headers,
        )
        assert missing.status_code == 404
        bad = await client.post(
            f"/api/workspaces/{workspace_id}/profile",
            json={"profile": "admin"},
            headers=headers,
        )
        assert bad.status_code == 400
        unknown = await client.post("/api/approvals/missing/approve", headers=headers)
        assert unknown.status_code == 404
