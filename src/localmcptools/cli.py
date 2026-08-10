"""Command-line interface for LocalMcpTools.

For the spike the only real subcommand is ``start``. ``stop`` and ``status``
are stubs that report *not* implemented yet — they get filled in by change-4
(managed-process-and-ports) when we add the long-running server model.

Exit codes follow the usual conventions:

- 0: success
- 1: internal / unexpected error
- 2: invalid usage (argparse)
- 3: not implemented (stub subcommand reached during the spike)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .config.paths import server_json_path
from .persistence.db import is_initialised
from .server import run_http, run_stdio

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_IMPLEMENTED = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="localmcptools",
        description=(
            "LocalMcpTools — local MCP toolset for VS Code agents. "
            "The default subcommand is 'start' so that "
            "'python -m localmcptools' launches the stdio server directly."
        ),
    )
    sub = p.add_subparsers(dest="subcommand")

    start_p = sub.add_parser(
        "start",
        help=(
            "Start the MCP server. Default mode is stdio (used by "
            "codebuddy and Copilot). Pass --http to launch the shared "
            "control plane + UI instead."
        ),
    )
    start_p.add_argument(
        "--http",
        action="store_true",
        help="Boot the HTTP control plane + MCP endpoint + UI instead of stdio.",
    )
    start_p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for --http mode (loopback only; default: 127.0.0.1).",
    )
    start_p.add_argument(
        "--port",
        type=int,
        default=7890,
        help="Bind port for --http mode (default: 7890).",
    )
    start_p.add_argument(
        "--auto-open-browser",
        action="store_true",
        help="Open the SPA in the default browser after boot.",
    )

    sub.add_parser(
        "stop",
        help=(
            "Stop a running server. SPIKE STUB — returns "
            "not_implemented until change-4 lands."
        ),
    )

    sub.add_parser(
        "status",
        help=(
            "Report server status. SPIKE STUB — returns "
            "not_implemented until change-4 lands."
        ),
    )

    install_p = sub.add_parser(
        "install",
        help=(
            "Install a user-level Windows scheduled task that boots the "
            "server on logon. Idempotent; falls back to a Startup-folder "
            "shortcut if the scheduled task path fails."
        ),
    )
    install_p.add_argument(
        "--method",
        choices=("scheduled_task", "startup_folder"),
        default="scheduled_task",
        help="install method (default: scheduled_task)",
    )

    sub.add_parser(
        "uninstall",
        help=(
            "Remove the user-level Windows scheduled task (or the "
            "Startup-folder shortcut) created by `install`. Never "
            "removes the data directory."
        ),
    )

    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Registered as ``localmcptools`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # never pollute the stdio MCP stream
    )

    sub = args.subcommand or "start"  # default for `python -m localmcptools`

    if sub == "start":
        return _cmd_start(
            http=getattr(args, "http", False),
            host=getattr(args, "host", "127.0.0.1"),
            port=getattr(args, "port", 7890),
            auto_open_browser=getattr(args, "auto_open_browser", False),
        )
    if sub == "stop":
        return _cmd_stop()
    if sub == "status":
        return _cmd_status()
    if sub == "install":
        return _cmd_install(getattr(args, "method", "scheduled_task"))
    if sub == "uninstall":
        return _cmd_uninstall()

    # argparse should have caught anything else.
    parser.print_help()
    return EXIT_USAGE


def _cmd_start(
    *,
    http: bool = False,
    host: str = "127.0.0.1",
    port: int = 7890,
    auto_open_browser: bool = False,
) -> int:
    """Launch the server.

    We deliberately don't print anything on stdout — the MCP protocol
    owns that stream. Anything we want to say lands on stderr.
    """
    # Print readiness on stderr so an operator can confirm boot
    # without corrupting the MCP wire format.
    mode = "http" if http else "stdio"
    print(
        f"[localmcptools] starting {mode} server "
        f"(audit db initialised={is_initialised()})",
        file=sys.stderr,
    )
    state_path = server_json_path()
    state_path.write_text(json.dumps({
        "pid": os.getpid(), "started_at": int(time.time() * 1000), "transport": mode,
        "host": host, "port": port,
    }), encoding="utf-8")
    try:
        if http:
            run_http(host=host, port=port, auto_open_browser=auto_open_browser)
        else:
            run_stdio()
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("pid") == os.getpid():
                state_path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
    return EXIT_OK


def _read_server_pid() -> int | None:
    try:
        value = json.loads(server_json_path().read_text(encoding="utf-8")).get("pid")
        return value if isinstance(value, int) and value > 0 else None
    except (OSError, json.JSONDecodeError):
        return None


def _cmd_status() -> int:
    pid = _read_server_pid()
    if pid is None:
        print("[localmcptools] server is not running", file=sys.stderr)
        return EXIT_ERROR
    try:
        os.kill(pid, 0)
    except OSError:
        server_json_path().unlink(missing_ok=True)
        print(f"[localmcptools] stale server metadata removed (pid={pid})", file=sys.stderr)
        return EXIT_ERROR
    print(f"[localmcptools] server is running (pid={pid})", file=sys.stderr)
    return EXIT_OK


def _cmd_stop() -> int:
    pid = _read_server_pid()
    if pid is None:
        print("[localmcptools] server is not running", file=sys.stderr)
        return EXIT_ERROR
    try:
        if os.name == "nt":
            # ``/T`` walks the process tree (uvicorn worker children),
            # ``/F`` forces termination when a graceful kill is rejected
            # (common on Windows when the python process is in a JOB
            # object or holds a console handle).
            result = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                check=False,
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", "replace").strip()
                print(
                    f"[localmcptools] taskkill failed (pid={pid}): {stderr}",
                    file=sys.stderr,
                )
                return EXIT_ERROR
        else:
            import signal

            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[localmcptools] stop failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    # The server's own finally block removes server.json on a graceful
    # exit, but ``/F`` skips cleanup. Clear it ourselves so subsequent
    # ``start`` calls don't trip the "another server appears to be
    # running" guard.
    server_json_path().unlink(missing_ok=True)
    return EXIT_OK


def _cmd_stub(name: str) -> int:
    """Handle a not-yet-implemented subcommand.

    Returns ``EXIT_NOT_IMPLEMENTED`` (3) so callers can distinguish
    "not built yet" from "tried to run and crashed".
    """
    print(
        f"[localmcptools] '{name}' is not implemented yet for the spike. "
        f"See openspec/changes/managed-process-and-ports/ for the roadmap.",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


# --- Install / uninstall (change-7: packaging) --------------------------


_INSTALL_TASK_NAME = "LocalMcpTools"


def _project_root() -> Path:
    """Return the project root (where ``pyproject.toml`` lives)."""
    # ``cli.py`` is at ``src/localmcptools/cli.py``; project root is two
    # levels up from there. ``__file__`` is reliable because ``python -m
    # localmcptools`` resolves the package source location.
    here = Path(__file__).resolve()
    return here.parent.parent.parent


def _python_executable() -> str:
    """Best-effort: prefer the interpreter that's running us right now."""
    import sys

    return sys.executable or "python"


def _powershell_exe() -> str:
    """Resolve a PowerShell binary; defaults to ``powershell.exe``."""
    import shutil

    return (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or shutil.which("pwsh")
        or "powershell.exe"
    )


def _cmd_install(method: str) -> int:
    """``localmcptools install [--method ...]``.

    On Windows we shell out to the corresponding PowerShell script.
    On non-Windows hosts we exit with ``EXIT_NOT_IMPLEMENTED`` —
    autostart is a Windows-only feature per OpenSpec.
    """
    if os.name != "nt":
        print(
            "[localmcptools] install is only implemented on Windows.",
            file=sys.stderr,
        )
        return EXIT_NOT_IMPLEMENTED
    if method == "scheduled_task":
        script = _project_root() / "scripts" / "install_windows_task.ps1"
    else:
        script = _project_root() / "scripts" / "install_startup_folder.ps1"
    if not script.is_file():
        print(
            f"[localmcptools] install script not found at {script}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    command = [
        _powershell_exe(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", str(script),
        "-ProjectRoot", str(_project_root()),
        "-PythonExecutable", _python_executable(),
    ]
    result = subprocess.run(command, check=False)
    return result.returncode


def _cmd_uninstall() -> int:
    """Remove the user-level scheduled task / Startup-folder shortcut."""
    if os.name != "nt":
        print(
            "[localmcptools] uninstall is only implemented on Windows.",
            file=sys.stderr,
        )
        return EXIT_NOT_IMPLEMENTED
    script = _project_root() / "scripts" / "uninstall_windows_task.ps1"
    if not script.is_file():
        print(
            f"[localmcptools] uninstall script not found at {script}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    command = [
        _powershell_exe(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", str(script),
    ]
    result = subprocess.run(command, check=False)
    return result.returncode


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
