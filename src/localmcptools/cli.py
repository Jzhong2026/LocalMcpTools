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

from .config.paths import server_json_path
from .persistence.db import is_initialised
from .server import run_stdio

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

    sub.add_parser(
        "start",
        help=(
            "Start the MCP server on stdio. Blocks until the parent "
            "agent process exits. This is what codebuddy and Copilot "
            "launch via their mcp.json files."
        ),
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

    sub.add_parser(
        "install",
        help=(
            "Print the mcp.json snippet for an agent. SPIKE STUB — "
            "returns not_implemented until change-7 (packaging) lands."
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
        return _cmd_start()
    if sub == "stop":
        return _cmd_stop()
    if sub == "status":
        return _cmd_status()
    if sub == "install":
        return _cmd_stub("install")

    # argparse should have caught anything else.
    parser.print_help()
    return EXIT_USAGE


def _cmd_start() -> int:
    """Launch the stdio server.

    We deliberately don't print anything on stdout — the MCP protocol
    owns that stream. Anything we want to say lands on stderr.
    """
    # Print readiness on stderr so an operator can confirm boot
    # without corrupting the MCP wire format.
    print(
        f"[localmcptools] starting stdio server "
        f"(audit db initialised={is_initialised()})",
        file=sys.stderr,
    )
    state_path = server_json_path()
    state_path.write_text(json.dumps({
        "pid": os.getpid(), "started_at": int(time.time() * 1000), "transport": "stdio"
    }), encoding="utf-8")
    try:
        run_stdio()
    except KeyboardInterrupt:
        # Parent (agent) sent SIGINT — that's a clean shutdown.
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
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"], check=False,
                capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                return EXIT_ERROR
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        return EXIT_ERROR
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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
