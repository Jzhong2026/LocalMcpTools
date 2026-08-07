"""FastMCP assembly for the spike.

Only one tool is registered: ``workspace.inspect``. The transport is stdio;
no HTTP listener is created (per openspec/changes/bootstrap-mcp-server).

Real registration with audit wrapping lives in :func:`_register_tools`.
Each tool function is registered as a raw callable that, when invoked,
delegates to :func:`localmcptools.tools.workspace.invoke` so the audit
row is always written.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from .tools import workspace

_log = logging.getLogger(__name__)

SERVER_NAME = "LocalMcpTools"


def build_server() -> FastMCP:
    """Return a configured FastMCP instance.

    The instance is not started; the caller chooses the transport.
    Splitting build from run lets tests introspect the registered tools.
    """
    mcp = FastMCP(SERVER_NAME)
    _register_tools(mcp)
    return mcp


def _register_tools(mcp: FastMCP) -> None:
    """Register the spike's only tool.

    The decorator wraps :func:`workspace.invoke` so every call is
    audit-recorded and envelope-wrapped automatically. Tool authors
    in later changes just need to expose a ``(args) -> result`` callable.
    """

    @mcp.tool(
        name="workspace.inspect",
        title="Inspect workspace (spike)",
        description=(
            "Workspace inspection spike stub. Confirms the MCP stdio "
            "round-trip end-to-end. Returns the server pid and build "
            "label so the caller can verify the call landed. Accepts "
            "an optional 'placeholder' field and ignores it."
        ),
    )
    def workspace_inspect(placeholder: bool = True) -> dict[str, object]:
        """Spike stub. See module docstring for details."""
        args = {"placeholder": placeholder}
        return workspace.invoke("workspace.inspect", args, workspace.inspect_tool)

    # Keep a reference so tests can introspect.
    _log.debug("registered tool: workspace.inspect")


def run_stdio() -> None:
    """Boot the server in stdio mode. Blocks until the parent process exits."""
    server = build_server()
    # FastMCP.run handles its own event loop.
    server.run(transport="stdio")
