"""MCP tools exposed by the server.

Each module here exposes a tool body (a callable ``(args) -> result``)
that :mod:`localmcptools.server` registers with the
:class:`ToolExecutionService`.

The envelope (ToolResponse / ToolMeta / ToolError) is in :mod:`._common`.
The single audit + envelope chokepoint is in
:mod:`localmcptools.execution.service`.
"""

from __future__ import annotations

from . import (
    diagnostics,
    environment,
    fs,
    output,
    process,
    runtime,
    vscode,
    workspace,
)

__all__ = [
    "diagnostics",
    "environment",
    "fs",
    "output",
    "process",
    "runtime",
    "vscode",
    "workspace",
]
