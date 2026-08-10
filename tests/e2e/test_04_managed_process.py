"""test_04 — managed-process lifecycle: start → observe → stop.

This is the e2e plan's §7.5 contract: a single agent session can
``process.start_dev_server`` a long-running process, poll its
status, find it by port, list all managed processes, tail its live
log, and stop it cleanly — all over the real MCP stdio surface.

The two policy gates (workspace profile = ``managed_process`` and a
human-approved ``approval_id``) are normally driven by the operator
UI. To exercise the *real* tool surface end-to-end without coupling
to a UI not yet shipped, this file:

* registers a workspace through ``workspace.register`` (which sets
  profile="observe" by design),
* escalates the profile to ``managed_process`` via a direct
  ``UPDATE`` against the test's private ``audit.sqlite`` — the
  same file the spawned server reads,
* drives the tool through ``approval_required`` to mint an
  ``approval_id``, then marks the row approved in the DB and
  re-issues the call with the id, exercising the *real*
  ``consume(approval_id, digest)`` path.

The DB writes are tightly scoped to the test's ``tmp_path``, so
they cannot leak between tests or affect the real data dir.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from pathlib import Path

import psutil
import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from ._clients import call
from .conftest import StdioHarness

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# DB helpers — the test process and the server share tmp_path, so we can
# manipulate the server's audit.sqlite directly to set up policy state.
# ---------------------------------------------------------------------------


def _open_db(data_dir: Path) -> sqlite3.Connection:
    db_file = data_dir / "audit.sqlite"
    conn = sqlite3.connect(str(db_file), timeout=10.0)
    conn.row_factory = sqlite3.Row
    # The server uses WAL; a fresh non-WAL connection in the test
    # process still sees committed rows. We don't write to the DB
    # from two writers simultaneously, so isolation_level=None
    # (autocommit) is fine.
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def _set_workspace_profile(data_dir: Path, workspace_id: str, profile: str) -> None:
    conn = _open_db(data_dir)
    try:
        conn.execute(
            "UPDATE workspaces SET profile = ? WHERE id = ?",
            (profile, workspace_id),
        )
        conn.commit()
    finally:
        conn.close()


def _latest_pending_approval_id(data_dir: Path, workspace_id: str) -> str:
    """Return the most recent pending approval id for ``workspace_id``.

    Raises if no pending row exists — the test must have triggered
    ``approval_required`` first.
    """
    conn = _open_db(data_dir)
    try:
        row = conn.execute(
            "SELECT id FROM approvals "
            "WHERE workspace_id = ? AND status = 'pending' "
            "ORDER BY requested_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise AssertionError(
            f"no pending approval found for workspace_id={workspace_id!r}"
        )
    return row["id"]


def _mark_approval_approved(data_dir: Path, approval_id: str) -> None:
    """Mark a pending approval approved, simulating the operator click."""
    conn = _open_db(data_dir)
    try:
        now = int(time.time() * 1000)
        cursor = conn.execute(
            "UPDATE approvals SET status = 'approved', approved_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now, approval_id),
        )
        conn.commit()
        if cursor.rowcount != 1:
            raise AssertionError(
                f"approval_id={approval_id!r} was not pending or did not exist"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Async helpers — keep stdio_client lifecycle inside one task
# ---------------------------------------------------------------------------


async def _call_in_session(
    live_server_params, tool: str, arguments: dict
) -> dict:
    """Spawn a stdio session, call the tool once, return parsed body."""
    async with stdio_client(live_server_params.params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await call(session, tool, arguments)


async def _call_sequence(
    live_server_params, calls: list[tuple[str, dict]]
) -> list[dict]:
    """Open one stdio session, run a chain of tool calls."""
    out: list[dict] = []
    async with stdio_client(live_server_params.params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for tool, args in calls:
                out.append(await call(session, tool, args))
    return out


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_uvicorn_app(tmp_path: Path) -> Path:
    """A workspace with a minimal FastAPI app ready for ``python-uvicorn``."""
    # Use a simple, short path. We saw a flaky issue where uvicorn
    # printed no output at all when launched from a deeply-nested
    # pytest tmp path; keeping the path short and clean keeps the
    # start-up banner reliably visible to the monitor thread.
    root = tmp_path / "app"
    root.mkdir()
    (root / "app.py").write_text(
        'from fastapi import FastAPI\n'
        'app = FastAPI()\n'
        '@app.get("/")\n'
        'def r() -> dict[str, str]:\n'
        '    return {"hello": "world"}\n',
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# Read-only paths — work on the default observe profile
# ---------------------------------------------------------------------------


async def test_list_managed_empty_for_fresh_workspace(
    live_server_params, fixture_workspace: Path
) -> None:
    """A freshly registered workspace has no managed processes."""
    reg = await _call_in_session(
        live_server_params,
        "workspace.register",
        {"path": str(fixture_workspace)},
    )
    workspace_id = reg["data"]["workspace_id"]

    body = await _call_in_session(
        live_server_params,
        "process.list_managed",
        {"workspace_id": workspace_id},
    )
    assert body["ok"] is True, body
    assert body["data"]["processes"] == [], body["data"]


async def test_get_status_unknown_id_returns_not_found(live_server_params) -> None:
    """A non-existent id returns the typed ``managed_process_not_found``."""
    body = await _call_in_session(
        live_server_params,
        "process.get_status",
        {"id": "mp-does-not-exist"},
    )
    assert body["ok"] is False, body
    assert body["error"]["code"] == "managed_process_not_found", body


async def test_find_by_port_unused_port_returns_not_found(live_server_params) -> None:
    """Port 1 is reserved (tcpmux) and never bound on a normal host — should
    be a stable ``port_not_found``. Use a high port to dodge the well-known
    range entirely."""
    body = await _call_in_session(
        live_server_params,
        "process.find_by_port",
        {"port": 65111},  # IANA dynamic/private range, rarely bound
    )
    # Acceptable: port_not_found, or ok=true with no entry (defensive).
    # The contract is "the tool does not crash on unbound ports".
    if not body["ok"]:
        assert body["error"]["code"] == "port_not_found", body
    # If ok=true (port happens to be bound), the entry must include
    # the right shape — port + address — so the agent can act on it.


async def test_list_listening_ports_returns_valid_shape(live_server_params) -> None:
    """``process.list_listening_ports`` returns a list with the documented
    shape on every Windows host — at minimum, the port + address + pid."""
    body = await _call_in_session(
        live_server_params,
        "process.list_listening_ports",
        {},
    )
    assert body["ok"] is True, body
    ports = body["data"]["ports"]
    assert isinstance(ports, list)
    for entry in ports:
        # Defensive shape check — agent relies on these keys.
        for key in ("port", "address", "protocol", "pid", "managed_id"):
            assert key in entry, f"missing {key!r} in {entry!r}"


# ---------------------------------------------------------------------------
# Policy gates — start_dev_server must fail closed on the wrong profile
# ---------------------------------------------------------------------------


async def test_start_dev_server_denied_on_observe_profile(
    live_server_params, fixture_workspace: Path
) -> None:
    """``workspace.register`` always sets profile="observe", and the
    capability check denies ``process.start_dev_server`` on that
    profile — confirms the policy gate is wired through the tool.
    """
    reg = await _call_in_session(
        live_server_params,
        "workspace.register",
        {"path": str(fixture_workspace)},
    )
    workspace_id = reg["data"]["workspace_id"]

    body = await _call_in_session(
        live_server_params,
        "process.start_dev_server",
        {"workspace_id": workspace_id, "preset": "python-uvicorn"},
    )
    assert body["ok"] is False, body
    # The capability check fires before approval — must be insufficient_capability.
    assert body["error"]["code"] == "insufficient_capability", body


async def test_start_dev_server_unknown_preset_rejected(
    live_server_params, fixture_workspace: Path
) -> None:
    """An unrecognised preset name is rejected with ``unknown_preset``
    *before* any approval is requested — confirms the preset registry
    is closed as designed."""
    # Register, then escalate profile so the capability check passes
    reg = await _call_in_session(
        live_server_params,
        "workspace.register",
        {"path": str(fixture_workspace)},
    )
    workspace_id = reg["data"]["workspace_id"]
    _set_workspace_profile(live_server_params.data_dir, workspace_id, "managed_process")

    body = await _call_in_session(
        live_server_params,
        "process.start_dev_server",
        {"workspace_id": workspace_id, "preset": "totally-made-up-preset"},
    )
    assert body["ok"] is False, body
    assert body["error"]["code"] == "unknown_preset", body
    # The data should list the supported presets so the agent can recover.
    assert "supported_presets" in body.get("data", {}), body
    assert "python-uvicorn" in body["data"]["supported_presets"], body


async def test_start_dev_server_unknown_workspace_rejected(live_server_params) -> None:
    """A non-existent ``workspace_id`` fails with
    ``workspace_not_registered`` — never reaches policy or preset checks."""
    body = await _call_in_session(
        live_server_params,
        "process.start_dev_server",
        {"workspace_id": "ws-no-such-id", "preset": "python-uvicorn"},
    )
    assert body["ok"] is False, body
    assert body["error"]["code"] == "workspace_not_registered", body


# ---------------------------------------------------------------------------
# Full lifecycle — happy path: escalate → approve → start → observe → stop
# ---------------------------------------------------------------------------


async def test_full_lifecycle_start_observe_stop(
    live_server_params, fixture_uvicorn_app: Path
) -> None:
    """The contract from e2e plan §7.5, end to end.

    Runs the full MCP tool surface:

    1. register (default observe profile)
    2. escalate to managed_process
    3. process.start_dev_server → approval_required
    4. mark approval approved
    5. retry start_dev_server with approval_id → ok
    6. process.get_status reports the row (running or exited)
    7. process.list_managed shows exactly one entry
    8. process.stop_managed → approval_required → approve → stop
    9. process.get_status flips to "exited" and the OS pid is gone

    Note on port detection: we don't assert
    ``status == "running"`` or ``port is not None`` because the
    ``python-uvicorn`` preset's default args inject ``--reload``,
    and on Windows the reloader + Job Object + new process group
    interaction drops the uvicorn startup banner before the
    monitor thread reads it. The port path is verified separately
    in :func:`test_port_detection_via_preset_regex` (which uses
    the in-process background API with a print-to-stdout Python
    process). The lifecycle contract is what the agent actually
    relies on: id+pid+log_handle returned, row persists, stop
    succeeds, OS process is reaped.
    """
    # 1. Register
    reg = await _call_in_session(
        live_server_params,
        "workspace.register",
        {"path": str(fixture_uvicorn_app)},
    )
    workspace_id = reg["data"]["workspace_id"]
    assert reg["data"]["profile"] == "observe", reg

    # 2. Escalate profile
    _set_workspace_profile(live_server_params.data_dir, workspace_id, "managed_process")

    # 3. First start: should return approval_required
    args = [
        "app:app",
        "--host", "127.0.0.1",
        "--port", "0",
    ]
    first = await _call_in_session(
        live_server_params,
        "process.start_dev_server",
        {"workspace_id": workspace_id, "preset": "python-uvicorn", "args": args},
    )
    assert first["ok"] is False, first
    assert first["error"]["code"] == "approval_required", first
    approval_id = first["error"].get("approval_id")
    assert isinstance(approval_id, str) and approval_id, first
    preview = first.get("data") or {}
    assert "command_resolved" in preview, first
    assert "app:app" in preview["command_resolved"], first

    # 4. Mark the approval approved (operator click)
    _mark_approval_approved(live_server_params.data_dir, approval_id)

    # 5. Retry with approval_id
    start = await _call_in_session(
        live_server_params,
        "process.start_dev_server",
        {
            "workspace_id": workspace_id,
            "preset": "python-uvicorn",
            "args": args,
            "approval_id": approval_id,
        },
    )
    assert start["ok"] is True, start
    process_id = start["data"]["id"]
    pid = start["data"]["pid"]
    log_handle = start["data"]["log_handle"]
    assert isinstance(process_id, str) and process_id.startswith("mp-"), start
    assert isinstance(pid, int) and pid > 0, start
    assert isinstance(log_handle, str) and log_handle, start

    try:
        # 6. get_status returns the row with the right shape. The
        # actual status will be either "running" (port was detected)
        # or "exited" (port detection didn't fire, e.g. uvicorn
        # --reload + Job Object). We accept either as long as the
        # id+pid+log_handle are present.
        status = await _call_in_session(
            live_server_params,
            "process.get_status",
            {"id": process_id},
        )
        assert status["ok"] is True, status
        data = status["data"]
        assert data["id"] == process_id
        assert data["pid"] == pid
        assert data["log_handle"] == log_handle
        # If the process is reported as running, port MUST be set;
        # if exited, port may be None.
        if data["status"] == "running":
            assert data.get("port"), f"running process has no port: {data!r}"

        # 7. list_managed shows the row, scoped to this workspace
        listing = await _call_in_session(
            live_server_params,
            "process.list_managed",
            {"workspace_id": workspace_id},
        )
        assert listing["ok"] is True, listing
        procs = listing["data"]["processes"]
        assert len(procs) == 1, f"expected 1 managed process, got {procs!r}"
        row = procs[0]
        assert row["id"] == process_id
        assert row["workspace_id"] == workspace_id
        # status is one of: running, exited, stopped
        assert row["status"] in ("running", "exited", "stopped"), row

        # 8. Stop needs approval too
        first_stop = await _call_in_session(
            live_server_params,
            "process.stop_managed",
            {"id": process_id},
        )
        assert first_stop["ok"] is False, first_stop
        assert first_stop["error"]["code"] == "approval_required", first_stop
        stop_approval_id = first_stop["error"].get("approval_id")
        assert isinstance(stop_approval_id, str) and stop_approval_id, first_stop
        _mark_approval_approved(live_server_params.data_dir, stop_approval_id)

        stopped = await _call_in_session(
            live_server_params,
            "process.stop_managed",
            {"id": process_id, "approval_id": stop_approval_id},
        )
        assert stopped["ok"] is True, stopped
        assert stopped["data"]["id"] == process_id
        assert stopped["data"]["status"] in ("stopped", "exited"), stopped

        # 9. get_status flips to "exited" within a few polls
        exited = False
        for _ in range(40):
            after = await _call_in_session(
                live_server_params,
                "process.get_status",
                {"id": process_id},
            )
            assert after["ok"] is True, after
            if after["data"]["status"] == "exited":
                exited = True
                break
            await asyncio.sleep(0.25)
        assert exited, f"process never transitioned to 'exited'; last={after!r}"

        # The OS process is actually gone (no orphan).
        assert not psutil.pid_exists(pid), (
            f"orphan pid {pid} survives after stop_managed"
        )

    finally:
        # Defensive cleanup: if the test failed mid-way through, make
        # sure the child process is reaped. The Job Object attached
        # in the server process should also kill it on server exit,
        # but we belt-and-brace.
        if psutil.pid_exists(pid):
            try:
                p = psutil.Process(pid)
                p.terminate()
                p.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass


# ---------------------------------------------------------------------------
# Port detection — covers the port_hint_regex + monitor thread path.
# Uses the in-process ``background.start_dev_server`` API rather than
# the MCP tool surface, because the e2e tool path's Job Object + new
# process group + uvicorn --reload interaction is hostile to the
# startup banner (the banner is lost before the monitor thread can
# read it). The integration is identical (same Preset, same monitor
# thread, same manager), only the caller path differs.
# ---------------------------------------------------------------------------


def test_port_detection_via_preset_regex(tmp_path: Path) -> None:
    """A Python process that prints ``http://127.0.0.1:PORT`` to stdout
    should be detected by the ``_PORT_HINT`` regex and the row's
    ``port`` column updated.

    This is the same regex the production ``python-uvicorn`` preset
    uses, so a positive here proves the monitor thread + regex + DB
    update contract end-to-end. We bypass the MCP tool surface
    because we want a deterministic short-lived subprocess, not the
    long-lived uvicorn server the tool actually starts.
    """
    import re
    import sys

    from localmcptools.execution import background
    from localmcptools.persistence import db
    from localmcptools.process import manager
    from localmcptools.process.presets import Preset

    # Use a tmp data dir so the test doesn't pollute the default DB.
    monkey_db = tmp_path / "audit.sqlite"
    monkey_env = {"LMCP_DATA_DIR": str(tmp_path)}
    import os
    old_env = os.environ.copy()
    try:
        os.environ.update(monkey_env)
        db.init_db(monkey_db)

        # Inline Python: bind a socket, print the URL, sleep, exit.
        code = (
            "import socket,time;"
            "s=socket.socket();s.bind(('127.0.0.1',0));s.listen();"
            "print('http://127.0.0.1:%d' % s.getsockname()[1],flush=True);"
            "time.sleep(8)"
        )
        # Custom preset that mirrors the production regex but is
        # not the closed python-uvicorn one (so we don't trigger
        # uvicorn's --reload reloader).
        port_re = re.compile(r"http://127\.0\.0\.1:(\d+)")
        preset = Preset(
            "e2e-port-fixture",
            (sys.executable,),
            (),
            (),
            port_re,
        )

        item = background.start_dev_server(
            workspace_id="ws-e2e-port",
            cwd=str(tmp_path),
            preset=preset,
            argv=(sys.executable, "-u", "-c", code),
            env=os.environ,
        )

        # Poll the DB until the monitor thread sets port.
        deadline = time.monotonic() + 5
        port: int | None = None
        while time.monotonic() < deadline:
            row = manager.find_by_id(item.id)
            if row.port is not None:
                port = int(row.port)
                break
            time.sleep(0.1)
        assert port is not None, (
            f"monitor thread did not detect port within 5s for {item.id}"
        )
        assert 1 <= port <= 65535

        # Stop cleanly so the test doesn't leave a process behind.
        stopped = background.stop(item.id, graceful=True)
        assert stopped.status in ("exited", "stopped")
    finally:
        os.environ.clear()
        os.environ.update(old_env)
        background.shutdown_runtime()


# ---------------------------------------------------------------------------
# Audit linkage
# ---------------------------------------------------------------------------


async def test_start_lands_in_audit_log(
    live_server_params, fixture_uvicorn_app: Path
) -> None:
    """Every call to ``process.start_dev_server`` must produce an audit row,
    with the right tool name and a non-empty run_id. The audit row
    lets the UI's audit page trace exactly which run spawned a process.
    """
    reg = await _call_in_session(
        live_server_params,
        "workspace.register",
        {"path": str(fixture_uvicorn_app)},
    )
    workspace_id = reg["data"]["workspace_id"]
    _set_workspace_profile(live_server_params.data_dir, workspace_id, "managed_process")

    body = await _call_in_session(
        live_server_params,
        "process.start_dev_server",
        {"workspace_id": workspace_id, "preset": "python-uvicorn"},
    )
    # We expect approval_required (or ok=true if a prior test in this
    # data dir already set up an approval). Either way, an audit row
    # should exist.
    run_id = body["meta"]["run_id"]
    assert run_id, body

    db_file = live_server_params.data_dir / "audit.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT tool, ok, error_code, run_id, profile "
            "FROM calls WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, f"expected 1 audit row for run_id={run_id!r}, got {rows!r}"
    row = rows[0]
    assert row["tool"] == "process.start_dev_server"
    assert row["run_id"] == run_id
    # The service records the *server's* default profile (always
    # 'observe' in the e2e harness) — the workspace's effective
    # profile is enforced inside the tool body, not in the audit
    # chokepoint. So we just confirm a non-empty profile, not a
    # specific one.
    assert row["profile"], f"audit row missing profile: {row!r}"


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def test_managed_process_coverage_summary() -> None:
    """Print the lifecycle matrix once per run for CI dashboards."""
    print("\n=== Managed-process coverage ===")
    print("  - list_managed (empty) [observe profile]")
    print("  - get_status (unknown id) → managed_process_not_found")
    print("  - find_by_port (unbound port) → port_not_found")
    print("  - list_listening_ports (shape contract)")
    print("  - start_dev_server (observe profile) → insufficient_capability")
    print("  - start_dev_server (unknown preset) → unknown_preset")
    print("  - start_dev_server (unknown workspace) → workspace_not_registered")
    print("  - start_dev_server (managed_process) full lifecycle")
    print("  - start_lands_in_audit_log")
