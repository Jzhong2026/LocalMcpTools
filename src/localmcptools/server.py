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
import time
from pathlib import Path
from typing import cast

from mcp.server.fastmcp import FastMCP

from .execution.service import (
    DEFAULT_POLICY_VERSION,
    DEFAULT_PROFILE,
    ToolExecutionService,
)
from .persistence.db import init_db
from .tools import (
    diagnostics,
    environment,
    fs,
    output,
    process,
    runtime,
    shell,
    vscode,
    workspace,
)

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
    mcp = _build_fast_mcp(service)
    mcp._lmcp_execution_service = service  # type: ignore[attr-defined]
    return mcp


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

    wrappers["workspace.git_status"] = service.register(
        "workspace.git_status", workspace.workspace_git_status,
        title="Get Git status", description="Read-only Git status for a registered workspace.",
        param_names=("workspace_id",),
    )
    for name, logic, title in (
        ("workspace.run_test", workspace.workspace_run_test, "Run workspace tests"),
        ("workspace.build", workspace.workspace_build, "Build workspace"),
        ("workspace.lint", workspace.workspace_lint, "Lint workspace"),
    ):
        wrappers[name] = service.register(
            name, logic, title=title,
            description="Run the resolved semantic preset after human approval.",
            param_names=("workspace_id", "filter", "timeout_ms", "approval_id"),
        )

    wrappers["shell.run_command"] = service.register(
        "shell.run_command",
        shell.shell_run_command,
        title="Run controlled PowerShell command",
        description=(
            "Run a PowerShell command in a workspace after capability and "
            "one-time human approval checks. The caller's cwd is ignored."
        ),
        param_names=("workspace_id", "cmd", "timeout_ms", "env", "approval_id", "cwd"),
    )

    wrappers["process.start_dev_server"] = service.register(
        "process.start_dev_server", process.process_start_dev_server,
        title="Start managed development server",
        description="Start an approved preset inside a registered workspace and bind its lifecycle to this MCP server.",
        param_names=("workspace_id", "preset", "args", "approval_id"),
    )
    wrappers["process.get_status"] = service.register(
        "process.get_status", process.process_get_status,
        title="Get managed process status",
        description="Return status, duration, exit code, port and recent output for a managed process.",
        param_names=("id",),
    )
    wrappers["process.list_managed"] = service.register(
        "process.list_managed", process.process_list_managed,
        title="List managed processes",
        description="List lifecycle-bound processes, optionally filtered by workspace.",
        param_names=("workspace_id",),
    )
    wrappers["process.stop_managed"] = service.register(
        "process.stop_managed", process.process_stop_managed,
        title="Stop managed process",
        description="Stop a managed process tree after one-time human approval.",
        param_names=("id", "graceful", "approval_id"),
    )
    wrappers["process.list_listening_ports"] = service.register(
        "process.list_listening_ports", process.process_list_listening_ports,
        title="List TCP listeners",
        description="Read-only list of TCP listening sockets and their managed-process association.",
        param_names=(),
    )
    wrappers["process.find_by_port"] = service.register(
        "process.find_by_port", process.process_find_by_port,
        title="Find TCP listener by port",
        description="Return the listener bound to a TCP port without exposing arbitrary PID termination.",
        param_names=("port",),
    )

    wrappers["runtime.detect_runtime"] = service.register(
        "runtime.detect_runtime", runtime.runtime_detect_runtime,
        title="Detect runtimes on PATH",
        description="List python / node / dotnet / npm copies on PATH with version and is_default marker.",
        param_names=("workspace_id",),
    )
    wrappers["runtime.get_env"] = service.register(
        "runtime.get_env", runtime.runtime_get_env,
        title="Get one environment variable",
        description="Return a single env var with redacted value and process/user/system source.",
        param_names=("name",),
    )
    wrappers["runtime.list_path"] = service.register(
        "runtime.list_path", runtime.runtime_list_path,
        title="List PATH entries",
        description="Return every directory on PATH with exists/is_file and project-aware runtime marker.",
        param_names=("workspace_id",),
    )

    wrappers["vscode.get_problems"] = service.register(
        "vscode.get_problems", vscode.vscode_get_problems,
        title="Read VS Code Problems panel",
        description="Read-only fetch of the current VS Code Problems list; returns vscode_not_running if VS Code is offline.",
        param_names=("severity", "workspace_path"),
    )
    wrappers["vscode.get_installed_extensions"] = service.register(
        "vscode.get_installed_extensions", vscode.vscode_get_installed_extensions,
        title="List VS Code extensions",
        description="Read installed extensions.json and return id/name/version/is_active per extension.",
        param_names=(),
    )
    wrappers["vscode.get_logs"] = service.register(
        "vscode.get_logs", vscode.vscode_get_logs,
        title="Tail VS Code log channel",
        description="Tail a named VS Code output channel log; read-only against %APPDATA%\\Code\\logs.",
        param_names=("channel", "n"),
    )
    wrappers["vscode.get_debug_sessions"] = service.register(
        "vscode.get_debug_sessions", vscode.vscode_get_debug_sessions,
        title="List VS Code debug sessions",
        description="Read debug.sessions from VS Code state; empty when nothing is running.",
        param_names=(),
    )

    wrappers["diagnostics.collect"] = service.register(
        "diagnostics.collect", diagnostics.diagnostics_collect,
        title="Aggregate diagnostics snapshot",
        description="One call fans out to runtime / git / problems / ports / recent failures.",
        param_names=("workspace_id", "depth", "limit"),
    )
    wrappers["diagnostics.explain_failure"] = service.register(
        "diagnostics.explain_failure", diagnostics.diagnostics_explain_failure,
        title="Explain a single run",
        description="Classify a prior run_id, pull key evidence, and emit next_actions.",
        param_names=("run_id", "row"),
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
    from .execution.background import shutdown_runtime, start_runtime

    server = build_server()
    start_runtime()
    try:
        server.run(transport="stdio")
    finally:
        service = cast(
            ToolExecutionService,
            server._lmcp_execution_service,  # type: ignore[attr-defined]
        )
        service.begin_shutdown(5.0)
        shutdown_runtime()


# --- HTTP / shared-mode entry point ---------------------------------------


def run_http(
    *,
    host: str = "127.0.0.1",
    port: int = 7890,
    auto_open_browser: bool = False,
) -> None:
    """Boot the server in HTTP shared mode.

    Brings up the FastAPI app with the control plane, MCP endpoint, and
    static UI mount. Blocks until the operator hits ``POST /api/shutdown``
    or sends Ctrl+C.

    The bearer / CSRF token is generated once and persisted in
    ``server.json`` alongside the bound port so other processes (the UI,
    agents over HTTP) can pick it up.
    """
    import json
    import os
    import webbrowser
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from .config.paths import server_json_path
    from .execution.background import shutdown_runtime, start_runtime
    from .transport import SecurityContext, mount_app

    # Refuse to bind anywhere other than loopback. OpenSpec invariant.
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"server.host must be loopback, got {host!r}")

    # Check for stale server.json — refuse to overwrite a live instance.
    state = server_json_path()
    if state.exists():
        try:
            existing = json.loads(state.read_text(encoding="utf-8"))
            existing_pid = int(existing.get("pid") or 0)
            if existing_pid and existing_pid != os.getpid() and _pid_alive(existing_pid):
                raise RuntimeError(
                    f"another server appears to be running at port "
                    f"{existing.get('port')} (pid={existing_pid})"
                )
        except (OSError, ValueError):
            pass

    # Bring up the FastMCP server (so its tools are registered) but DO
    # NOT call ``server.run`` — we mount its ASGI app into FastAPI.
    fastmcp = build_server()
    start_runtime()

    # Allocate the bearer / CSRF token once; both share it.
    from .transport.http import generate_token

    token = generate_token()
    context = SecurityContext(
        token=token,
        origin_allowlist=("http://127.0.0.1", "http://localhost"),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Stash the uvicorn server so ``/api/shutdown`` can poke it.
        uvicorn_server = getattr(app.state, "uvicorn_server", None)
        yield

    app = FastAPI(title="LocalMcpTools", lifespan=lifespan)
    static_dir = _ui_assets_dir()
    mount_app(
        app=app,
        context=context,
        control_router=_build_control_router(),
        mcp_asgi_app=fastmcp.streamable_http_app(),
        static_dir=static_dir,
    )

    # Persist server.json BEFORE binding so the UI / external agents can
    # read the URL even during the boot window.
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": host,
                "port": port,
                "started_at": int(time.time() * 1000),
                "csrf_token": token,
                "transport": "http",
            }
        ),
        encoding="utf-8",
    )

    if auto_open_browser:
        try:
            webbrowser.open(f"http://{host}:{port}/ui/")
        except OSError as exc:  # noqa: BLE001
            _log.warning("could not open browser: %s", exc)

    import uvicorn

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # Make the uvicorn server reachable from the request handler so
    # ``/api/shutdown`` can stop the loop.
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    try:
        server.run()
    finally:
        service = cast(
            ToolExecutionService,
            fastmcp._lmcp_execution_service,  # type: ignore[attr-defined]
        )
        service.begin_shutdown(5.0)
        shutdown_runtime()
        try:
            state.unlink(missing_ok=True)
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    """Best-effort check whether ``pid`` is still running (Windows-aware)."""
    try:
        import psutil

        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except (OSError, ImportError, ValueError):
        return False


def _ui_assets_dir() -> str | None:
    """Return the path to the built Angular bundle, or None when absent.

    The bundle lives at ``src/localmcptools/ui_assets`` and is produced
    by ``scripts/build_frontend.bat``. If the SPA has never been built
    we return ``None`` and skip the static mount — the FastAPI app
    stays useful for the control plane + MCP endpoint.
    """
    import os

    here = os.path.dirname(__file__)
    candidate = os.path.join(here, "ui_assets")
    if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "index.html")):
        return candidate
    return None


def _build_control_router() -> object:
    """Lazily build the control plane router.

    Importing :mod:`localmcptools.control_api` at module-load time pulls
    FastAPI / Pydantic into a process that may only need stdio; this
    deferred import keeps the stdio path lean.
    """
    from .control_api import router

    return router


__all__ = ["SERVER_NAME", "build_server", "run_http", "run_stdio"]
