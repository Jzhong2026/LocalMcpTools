"""Boot e2e tests for LocalMcpTools.

Spawns a real ``python -m localmcptools`` process over stdio and over
HTTP and asserts the **server contract**:

* stdio initialize() round-trips
* HTTP /api/status returns 200 with the right shape
* server.json is written, parsed, and cleaned up on shutdown
* a fresh ``audit.sqlite`` is created with schema_version >= 5

These tests are the foundation for every other e2e test in the
suite — if boot fails, nothing else can work.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from ._clients import list_tool_names
from .conftest import REPO_ROOT, _python_executable

pytestmark = pytest.mark.e2e
# asyncio_mode = "auto" in pyproject.toml means async functions are
# automatically picked up. We don't apply the module-level asyncio
# marker so that synchronous helpers don't get falsely flagged.


# ---------------------------------------------------------------------------
# stdio boot
# ---------------------------------------------------------------------------


async def test_stdio_initializes_and_lists_tools(live_server_params) -> None:
    """Boot stdio, run initialize(), assert a tool list comes back."""
    async with stdio_client(live_server_params.params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await list_tool_names(session)

    # Must contain the original observe surface + all newer tools
    assert "environment.get" in tools, "missing core tool environment.get"
    assert "workspace.inspect" in tools, "missing core tool workspace.inspect"
    assert "output.tail" in tools, "missing core tool output.tail"
    # And a few representative tools from later changes
    assert "shell.run_command" in tools, "missing shell.run_command"
    assert "process.start_dev_server" in tools, "missing process.start_dev_server"
    # Anti-contract: things we explicitly do not expose
    assert "process.kill" not in tools, "process.kill should not be exposed"


async def test_stdio_audit_db_created_on_first_call(live_server_params, audit_db) -> None:
    """First call should populate audit.sqlite with one row."""
    async with stdio_client(live_server_params.params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("environment.get", arguments={})

    rows = audit_db.execute("SELECT * FROM calls").fetchall()
    assert len(rows) == 1, f"expected 1 audit row, got {len(rows)}"
    row = rows[0]
    assert row["tool"] == "environment.get"
    assert row["ok"] == 1
    # The audit row PK is the audit_id (returned to the caller in
    # ``meta.audit_id``); we don't store it as a separate column.
    assert row["id"], "row.id (audit_id) missing"
    assert row["run_id"], "run_id missing"
    assert row["profile"], "profile missing"
    assert row["duration_ms"] >= 0


async def test_stdio_cold_boot_schema_v5(live_server_params) -> None:
    """Fresh data dir → audit.sqlite must be created at schema_version 5."""
    db_file = live_server_params.data_dir / "audit.sqlite"
    # Pre-boot: the DB should not exist yet
    assert not db_file.exists(), "test bug: data dir is not fresh"

    async with stdio_client(live_server_params.params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("environment.get", arguments={})

    assert db_file.exists(), "server didn't create audit.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        # The codebase persists schema via its ``schema_version`` row,
        # not ``PRAGMA user_version``.
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        v = row["v"] if row else 0
        # 5 is the schema for authorized_windows + ui_automation support
        assert v >= 5, f"schema_version too low: {v} (expected >= 5)"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HTTP boot (synchronous — uses subprocess + polling)
# ---------------------------------------------------------------------------


def _spawn_http(data_dir: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["LMCP_DATA_DIR"] = str(data_dir)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [
            _python_executable(),
            "-m",
            "localmcptools",
            "start",
            "--http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def test_http_boots_and_writes_server_json(tmp_path: Path) -> None:
    """Start in HTTP mode → server.json appears with valid token + csrf."""
    from .conftest import _free_port

    port = _free_port()
    proc = _spawn_http(tmp_path, port)
    server_json = tmp_path / "server.json"
    try:
        # Wait up to 15 s for the server-side write (with csrf_token).
        # The cli writes a stub first; ``run_http`` overwrites it ~200 ms later.
        deadline = time.monotonic() + 15
        meta = None
        while time.monotonic() < deadline:
            if server_json.exists():
                try:
                    candidate = json.loads(server_json.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    candidate = None
                if candidate and candidate.get("csrf_token"):
                    meta = candidate
                    break
            if proc.poll() is not None:
                out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
                pytest.fail(f"server exited early: code={proc.returncode}\n{out}")
            time.sleep(0.1)
        assert meta is not None, "server.json never grew a csrf_token within 15 s"

        assert meta["host"] == "127.0.0.1"
        assert meta["port"] == port
        # `meta["pid"]` is the server's own view; we only assert it is
        # alive on the system. (Windows ``Popen.pid`` can disagree with
        # the child's ``os.getpid()`` due to handle inheritance in 3.14.)
        assert isinstance(meta["pid"], int) and meta["pid"] > 0
        assert meta["csrf_token"], "csrf_token missing"
        assert "started_at" in meta
        assert meta["transport"] == "http"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def test_http_status_endpoint_responds_200(live_server_http) -> None:
    """After boot, GET /api/status returns 200 with Origin."""
    r = httpx.get(
        f"{live_server_http.base_url}/api/status",
        headers={"Origin": live_server_http.origin},
        timeout=5,
    )
    assert r.status_code == 200, f"status={r.status_code} body={r.text}"
    body = r.json()
    # /api/status returns its dict directly (no universal envelope).
    assert "server" in body
    assert body["server"]["transport"] == "http"
    assert body["server"]["audit_db_initialised"] is True
    assert body["server"]["uptime_ms"] > 0, "uptime should be set after start"
    assert "config" in body
    assert "data_dir" in body


def test_http_rejects_missing_origin(live_server_http) -> None:
    """/api/* without Origin → 403."""
    r = httpx.get(
        f"{live_server_http.base_url}/api/status",
        timeout=5,
    )
    assert r.status_code == 403, f"expected 403 got {r.status_code}"


def test_http_rejects_wrong_origin(live_server_http) -> None:
    """Origin not in allowlist → 403."""
    r = httpx.get(
        f"{live_server_http.base_url}/api/status",
        headers={"Origin": "http://evil.example.com"},
        timeout=5,
    )
    assert r.status_code == 403, f"expected 403 got {r.status_code}"


def test_shutdown_frees_port_and_removes_server_json(tmp_path: Path) -> None:
    """``localmcptools stop`` terminates the server and removes server.json."""
    from .conftest import _free_port

    port = _free_port()
    env = os.environ.copy()
    env["LMCP_DATA_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")

    proc = _spawn_http(tmp_path, port)
    server_json = tmp_path / "server.json"
    try:
        # Wait for boot
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if server_json.exists():
                break
            time.sleep(0.1)
        assert server_json.exists(), "server didn't write server.json"

        # Run `localmcptools stop`
        stop = subprocess.run(
            [_python_executable(), "-m", "localmcptools", "stop"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            timeout=15,
        )
        assert stop.returncode == 0, f"stop failed: {stop.stderr.decode('utf-8', 'replace')}"

        # Give the OS a beat to release the socket
        time.sleep(0.5)

        # Port should be free
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            try:
                s.connect(("127.0.0.1", port))
                pytest.fail(f"port {port} still bound after stop")
            except (ConnectionRefusedError, socket.timeout, OSError):
                pass  # expected

        # server.json should be gone
        assert not server_json.exists(), "server.json was not removed by stop"

        # The server process should be exited
        assert proc.poll() is not None, "server process still alive after stop"
    finally:
        # Best-effort cleanup if the test failed before stop completed
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)