"""FastMCP assembly.

Tools are registered via :class:`ToolExecutionService`, which owns
audit + envelope. This module only:

1. Builds the :class:`ToolExecutionService`.
2. Registers every tool body exported by :mod:`localmcptools.tools`.
3. Hands the resulting FastMCP instance to the caller (stdio loop or
   HTTP server). The transport is not chosen here.

The change-2 (core-shell-and-audit) tool surface is:

- ``environment.get``           — host context (read-only)
- ``workspace.register``        — register a directory
- ``workspace.list``            — enumerate registered workspaces
- ``workspace.inspect``         — project type / Git / presets / runtimes
- ``workspace.search_text``     — workspace-scoped grep
- ``fs.read_range``             — lines [start, end) inside workspace
- ``fs.tail_log_file``          — last N lines of a file
- ``fs.grep_files``             — workspace-scoped regex with include_glob
- ``output.tail``               — tail an artifact handle
- ``output.read_range``         — read a range from an artifact
- ``output.search``             — regex search inside an artifact
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .execution.service import (
    DEFAULT_POLICY_VERSION,
    DEFAULT_PROFILE,
    ToolExecutionService,
)
from .persistence.db import init_db
from .tools import environment, fs, output, workspace

_log = logging.getLogger(__name__)

SERVER_NAME = "LocalMcpTools"


# --- Build ----------------------------------------------------------------


def build_server(
    *,
    audit_path: Path | None = None,
    profile: str = DEFAULT_PROFILE,
    policy_version: str = DEFAULT_POLICY_VERSION,
) -> FastMCP:
    """Return a configured FastMCP instance.

    ``audit_path`` and ``profile``/``policy_version`` are exposed for
    tests; production callers pass nothing and inherit the defaults.
    """
    init_db(audit_path)
    service = ToolExecutionService(
        audit_path=audit_path,
        profile=profile,
        policy_version=policy_version,
    )
    _register_tools(service)
    return _build_fast_mcp(service)


# --- Tool registration ----------------------------------------------------


def _register_tools(service: ToolExecutionService) -> None:
    """Register every tool exposed by :mod:`localmcptools.tools`.

    Each registration returns a FastMCP-friendly wrapper. We capture
    the wrappers into a local map so the FastMCP layer can register
    them with the correct metadata.
    """
    wrappers: dict[str, object] = {}

    wrappers["environment.get"] = service.register(
        "environment.get",
        environment.environment_get,
        title="Get host environment",
        description=(
            "Return host context (OS, PowerShell version, console "
            "encoding, user, working directory) for the agent to "
            "reason about. Pure read-only; no inputs."
        ),
        param_names=(),
    )

    wrappers["workspace.register"] = service.register(
        "workspace.register",
        workspace.workspace_register,
        title="Register workspace",
        description=(
            "Register an absolute directory as a workspace. Returns "
            "`workspace_id` and `canonical_root`. Idempotent."
        ),
        param_names=("path", "notes"),
    )

    wrappers["workspace.list"] = service.register(
        "workspace.list",
        workspace.workspace_list,
        title="List registered workspaces",
        description=(
            "Return every registered workspace for enumeration."
        ),
        param_names=(),
    )

    wrappers["workspace.inspect"] = service.register(
        "workspace.inspect",
        workspace.workspace_inspect,
        title="Inspect workspace",
        description=(
            "Return project type, Git status, available presets, "
            "runtimes, and missing runtimes for the given workspace."
        ),
        param_names=("workspace_id",),
    )

    wrappers["workspace.search_text"] = service.register(
        "workspace.search_text",
        workspace.workspace_search_text,
        title="Workspace-scoped search",
        description=(
            "Search inside a workspace with a regex. Matches outside "
            "the workspace are excluded; binary files are skipped."
        ),
        param_names=("workspace_id", "pattern", "max_results", "include_glob"),
    )

    wrappers["fs.read_range"] = service.register(
        "fs.read_range",
        fs.fs_read_range,
        title="Read file range",
        description=(
            "Read lines [start_line, end_line) from a file inside a "
            "registered workspace."
        ),
        param_names=("workspace_id", "path", "start_line", "end_line"),
    )

    wrappers["fs.tail_log_file"] = service.register(
        "fs.tail_log_file",
        fs.fs_tail_log_file,
        title="Tail log file",
        description=(
            "Return the last N lines of a file inside a registered "
            "workspace. Files larger than 5 MiB are streamed."
        ),
        param_names=("workspace_id", "path", "n"),
    )

    wrappers["fs.grep_files"] = service.register(
        "fs.grep_files",
        fs.fs_grep_files,
        title="Workspace-scoped grep",
        description=(
            "Regex search across all files in a workspace. Default "
            "exclude directories: node_modules, .git, dist, bin, obj, "
            ".venv, __pycache__, .angular."
        ),
        param_names=("workspace_id", "pattern", "include_glob", "max_results"),
    )

    wrappers["output.tail"] = service.register(
        "output.tail",
        output.output_tail,
        title="Tail artifact",
        description=(
            "Return the last N lines of an artifact identified by "
            "`handle`."
        ),
        param_names=("handle", "n"),
    )

    wrappers["output.read_range"] = service.register(
        "output.read_range",
        output.output_read_range,
        title="Read artifact range",
        description=(
            "Read lines [start_line, end_line) from an artifact "
            "identified by `handle`."
        ),
        param_names=("handle", "start_line", "end_line"),
    )

    wrappers["output.search"] = service.register(
        "output.search",
        output.output_search,
        title="Search artifact",
        description=(
            "Regex search inside an artifact identified by `handle`."
        ),
        param_names=("handle", "pattern", "max_results"),
    )

    # Attach the wrappers to the service so _build_fast_mcp can pull
    # them out and register with the MCP server.
    service._wrappers = wrappers  # type: ignore[attr-defined]
    _log.debug("registered tools: %s", sorted(wrappers))


def _build_fast_mcp(service: ToolExecutionService) -> FastMCP:
    """Create a FastMCP instance and register every wrapper with it."""
    mcp = FastMCP(SERVER_NAME)
    wrappers: dict[str, object] = getattr(service, "_wrappers", {})
    for tool_name, wrapper in wrappers.items():
        reg = service.get_registration(tool_name)
        # FastMCP.tool returns a decorator; we already have a wrapper
        # so we re-decorate by calling the decorator on it. The MCP
        # SDK accepts a function and reads its docstring / name.
        mcp.tool(name=reg.tool, title=reg.title, description=reg.description)(
            wrapper  # type: ignore[arg-type]
        )
    return mcp


# --- Stdio entry point ----------------------------------------------------


def run_stdio() -> None:
    """Boot the server in stdio mode. Blocks until the parent exits."""
    server = build_server()
    server.run(transport="stdio")


__all__ = ["SERVER_NAME", "build_server", "run_stdio"]