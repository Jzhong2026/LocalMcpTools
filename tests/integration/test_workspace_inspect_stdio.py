"""End-to-end stdio tests for the change-2 tool surface.

Boots the LocalMcpTools server over stdio and exercises a handful of
tools to prove the round-trip works for the new read-only tool set.

We test the *new* tools (``environment.get``, ``workspace.register``,
``workspace.inspect``, ``workspace.search_text``, ``fs.read_range``,
``output.tail``) rather than the old ``workspace.inspect`` spike stub,
which has been replaced.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Tools that MUST be exposed by the server after change-2.
EXPECTED_TOOLS = {
    "environment.get",
    "workspace.register",
    "workspace.list",
    "workspace.inspect",
    "workspace.search_text",
    "fs.read_range",
    "fs.tail_log_file",
    "fs.grep_files",
    "output.tail",
    "output.read_range",
    "output.search",
    "process.start_dev_server",
    "process.get_status",
    "process.list_managed",
    "process.stop_managed",
    "process.list_listening_ports",
    "process.find_by_port",
}


@pytest.fixture
def server_params(tmp_path: Path) -> StdioServerParameters:
    env = os.environ.copy()
    env["LMCP_DATA_DIR"] = str(tmp_path)
    env["PYTHONUNBUFFERED"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "localmcptools"],
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
    )


@pytest.fixture
def fixture_workspace(tmp_path: Path) -> Path:
    """Create a tiny workspace with one Python file + one log file."""
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    (root / "app.py").write_text("TODO: replace this with real code\n")
    (root / "build.log").write_text(
        "\n".join(f"line {i}: status=ok" for i in range(50)) + "\n"
    )
    return root


# --- Tool list ------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_listed(
    tmp_path: Path, server_params: StdioServerParameters
) -> None:
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
    assert EXPECTED_TOOLS.issubset(names), (
        f"missing tools: {EXPECTED_TOOLS - names}"
    )
    assert "process.kill" not in names


# --- environment.get ------------------------------------------------------


@pytest.mark.asyncio
async def test_environment_get(
    tmp_path: Path, server_params: StdioServerParameters
) -> None:
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool("environment.get", arguments={})
    assert r.content
    body = json.loads(r.content[0].text)
    assert body["ok"] is True
    data = body["data"]
    assert "os" in data
    assert "powershell" in data
    assert "encoding" in data
    assert "user" in data
    assert "cwd" in data
    assert "machine" in data
    # REQ-ENV-1 spot checks.
    assert data["os"]["name"] in ("nt", "posix")
    assert isinstance(data["encoding"]["active_code_page"], (int, type(None)))
    # Audit row landed.
    audit_db = tmp_path / "audit.sqlite"
    assert audit_db.exists()
    conn = sqlite3.connect(audit_db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM calls WHERE tool = 'environment.get'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) >= 1
    assert rows[-1]["profile"] == "observe"


# --- workspace.register + inspect + search_text --------------------------


@pytest.mark.asyncio
async def test_workspace_register_inspect_search(
    tmp_path: Path,
    server_params: StdioServerParameters,
    fixture_workspace: Path,
) -> None:
    """End-to-end: register → inspect → search inside the workspace."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            reg = await session.call_tool(
                "workspace.register",
                arguments={"path": str(fixture_workspace)},
            )
            reg_body = json.loads(reg.content[0].text)
            assert reg_body["ok"] is True
            ws_id = reg_body["data"]["workspace_id"]
            assert ws_id

            ins = await session.call_tool(
                "workspace.inspect", arguments={"workspace_id": ws_id}
            )
            ins_body = json.loads(ins.content[0].text)
            assert ins_body["ok"] is True
            data = ins_body["data"]
            assert data["project_type"] == "python"
            assert data["git"]["status"] == "not_a_repo"
            assert "test" in data["presets_available"]
            assert "build" in data["presets_available"]

            # Search for "TODO" — must find it in app.py.
            s = await session.call_tool(
                "workspace.search_text",
                arguments={
                    "workspace_id": ws_id,
                    "pattern": "TODO",
                    "max_results": 10,
                },
            )
            s_body = json.loads(s.content[0].text)
            assert s_body["ok"] is True
            matches = s_body["data"]["matches"]
            assert any("TODO" in m["text"] for m in matches)


# --- fs.read_range --------------------------------------------------------


@pytest.mark.asyncio
async def test_fs_read_range(
    tmp_path: Path,
    server_params: StdioServerParameters,
    fixture_workspace: Path,
) -> None:
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            reg = await session.call_tool(
                "workspace.register",
                arguments={"path": str(fixture_workspace)},
            )
            ws_id = json.loads(reg.content[0].text)["data"]["workspace_id"]
            r = await session.call_tool(
                "fs.read_range",
                arguments={
                    "workspace_id": ws_id,
                    "path": "build.log",
                    "start_line": 0,
                    "end_line": 5,
                },
            )
            body = json.loads(r.content[0].text)
            assert body["ok"] is True
            lines = body["data"]["lines"]
            assert lines[0].startswith("line 0")
            assert len(lines) == 5


# --- output.tail (with artifact) -----------------------------------------


@pytest.mark.asyncio
async def test_output_tail_with_handle(
    tmp_path: Path,
    server_params: StdioServerParameters,
) -> None:
    """Write a 70KB content via the persistence layer, then page it.

    We exercise output.tail directly because no tool in change-2
    produces >64KB output (every other tool is small). The artifact
    module + output.tail are wired together by the audit pipeline.
    """
    from localmcptools.persistence import artifacts
    from localmcptools.persistence import db as dbmod

    dbmod.init_db(tmp_path / "audit.sqlite")
    # Write 70 KiB so the inline threshold is exceeded.
    content = ("x" * 70 + "\n") * 1024
    handle = artifacts.write(content, conn=dbmod.get_connection(tmp_path / "audit.sqlite"))

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool(
                "output.tail",
                arguments={"handle": handle, "n": 5},
            )
            body = json.loads(r.content[0].text)
            assert body["ok"] is True
            assert body["data"]["handle"] == handle
            assert len(body["data"]["lines"]) == 5


# --- workspace path-escape rejected --------------------------------------


@pytest.mark.asyncio
async def test_path_escape_rejected(
    tmp_path: Path,
    server_params: StdioServerParameters,
    fixture_workspace: Path,
) -> None:
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            reg = await session.call_tool(
                "workspace.register",
                arguments={"path": str(fixture_workspace)},
            )
            ws_id = json.loads(reg.content[0].text)["data"]["workspace_id"]
            r = await session.call_tool(
                "fs.read_range",
                arguments={
                    "workspace_id": ws_id,
                    # Path traversal attempt.
                    "path": str(fixture_workspace) + os.sep + ".." + os.sep + "etc" + os.sep + "passwd",
                    "start_line": 0,
                    "end_line": 1,
                },
            )
            body = json.loads(r.content[0].text)
            assert body["ok"] is False
            assert body["error"]["code"] == "invalid_path"
