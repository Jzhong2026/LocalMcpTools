"""End-to-end test: drive the LocalMcpTools server over stdio.

This is the spike's *acceptance* test. It must prove:

1. The MCP SDK can handshake against our server over stdio.
2. ``tools/list`` returns exactly one tool (``workspace.inspect``).
3. ``tools/call`` for ``workspace.inspect`` returns the spike payload
   wrapped in the standard envelope.
4. The call lands in ``audit.sqlite`` with the correct shape.

We use the official MCP Python client (``mcp.client.stdio.stdio_client``
+ ``mcp.ClientSession``) to drive the conversation. Hand-rolling the
JSON-RPC framing is brittle on Windows (named-pipe buffering + the
``notifications/initialized`` race turned the manual version into a
flaky mess). The client is what codebuddy and Copilot themselves use,
so the test is also closer to production.
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


@pytest.fixture
def server_params(tmp_path: Path) -> StdioServerParameters:
    """Server parameters that point LMCP_DATA_DIR at tmp_path."""
    env = os.environ.copy()
    env["LMCP_DATA_DIR"] = str(tmp_path)
    env["PYTHONUNBUFFERED"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "localmcptools"],
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
    )


@pytest.mark.asyncio
async def test_stdio_round_trip(tmp_path: Path, server_params: StdioServerParameters) -> None:
    """Boot the server and exercise workspace.inspect end-to-end."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Handshake.
            init_result = await session.initialize()
            assert init_result.serverInfo.name == "LocalMcpTools"

            # tools/list must return exactly one tool.
            tools = await session.list_tools()
            assert len(tools.tools) == 1
            assert tools.tools[0].name == "workspace.inspect"
            assert "workspace" in (tools.tools[0].description or "").lower()

            # Call it.
            call_result = await session.call_tool(
                "workspace.inspect",
                arguments={"placeholder": True},
            )
            assert call_result.content
            text_block = call_result.content[0]
            assert text_block.type == "text"
            body = json.loads(text_block.text)

            # Envelope shape — matches REQ-WI-2 from the openspec spec.
            assert body["ok"] is True
            assert body["data"]["build"] == "spike-0"
            assert isinstance(body["data"]["pid"], int)
            assert body["meta"]["tool"] == "workspace.inspect"
            assert body["meta"]["duration_ms"] >= 0
            # audit_id and run_id look like UUIDs (36 chars, dashes).
            assert len(body["meta"]["audit_id"]) == 36
            assert len(body["meta"]["run_id"]) == 36

    # ---- audit row landed in the tmp db ----
    audit_db = tmp_path / "audit.sqlite"
    assert audit_db.exists(), "audit.sqlite should have been created"
    conn = sqlite3.connect(audit_db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM calls WHERE tool = ? ORDER BY timestamp",
            ("workspace.inspect",),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) >= 1, "no audit row recorded"
    row = rows[-1]
    assert row["tool"] == "workspace.inspect"
    assert row["profile"] == "observe"
    assert row["policy_version"] == "spike-0"
    assert row["status"] == "success"
    assert row["ok"] == 1
    assert row["finished_at"] is not None
    assert row["finished_at"] >= row["timestamp"]
    # Args were persisted as JSON.
    args = json.loads(row["args_redacted"])
    assert args == {"placeholder": True}


@pytest.mark.asyncio
async def test_unknown_tool_returns_jsonrpc_error(
    tmp_path: Path, server_params: StdioServerParameters
) -> None:
    """Sending a call for a tool that doesn't exist must produce a clean error.

    FastMCP surfaces unknown-tool calls via the MCP protocol-level
    error (``isError=True`` on the ``CallToolResult``). What we care
    about for the spike is that the server *doesn't silently invent*
    a tool and that the error text does not leak a Python traceback.
    """
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("no.such.tool", arguments={})
            assert result.isError is True
            # Surface the content so test failures show what the server said.
            content_text = " ".join(
                getattr(block, "text", "") for block in result.content
            )
            assert content_text  # non-empty
            # No Python internals leaking.
            lowered = content_text.lower()
            for leak in ("traceback", "exception ", "builtins"):
                assert leak not in lowered, f"unknown-tool error leaks {leak!r}: {content_text!r}"