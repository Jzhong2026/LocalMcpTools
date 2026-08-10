"""Helper clients for the e2e suite.

These wrap the low-level MCP / httpx calls into one-liners so the
test files themselves stay focused on the assertions.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import Any

from mcp import ClientSession

# Re-export the raw clients for tests that need full control
from mcp.client.stdio import stdio_client  # noqa: F401


async def list_tool_names(session: ClientSession) -> set[str]:
    """Return the set of tool names advertised by the server."""
    result = await session.list_tools()
    return {t.name for t in result.tools}


async def call(
    session: ClientSession, tool: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call a tool and parse its envelope into a dict.

    Raises ``AssertionError`` if the call returned no content blocks.
    """
    arguments = arguments or {}
    result = await session.call_tool(tool, arguments=arguments)
    assert result.content, f"tool {tool} returned no content"
    text = result.content[0].text
    return json.loads(text)


async def assert_envelope_shape(
    session: ClientSession, body: dict[str, Any], *, tool: str
) -> None:
    """Verify the universal envelope contract on a single response."""
    assert isinstance(body, dict), f"{tool}: body is not a dict"
    assert "ok" in body, f"{tool}: missing 'ok'"
    assert "meta" in body, f"{tool}: missing 'meta'"
    assert "data" in body, f"{tool}: missing 'data'"
    meta = body["meta"]
    assert meta.get("tool") == tool, f"{tool}: meta.tool mismatch ({meta.get('tool')})"
    assert meta.get("run_id"), f"{tool}: missing meta.run_id"
    assert meta.get("audit_id"), f"{tool}: missing meta.audit_id"
    assert meta.get("profile"), f"{tool}: missing meta.profile"
    assert "next_actions" in meta, f"{tool}: missing meta.next_actions"


async def ok(
    session: ClientSession, tool: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call a tool, assert ok=true, return the parsed body."""
    body = await call(session, tool, arguments)
    await assert_envelope_shape(session, body, tool=tool)
    assert body["ok"] is True, f"{tool}: ok=false ({body.get('error', {})})"
    return body