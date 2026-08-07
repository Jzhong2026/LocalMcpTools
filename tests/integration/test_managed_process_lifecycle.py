from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from localmcptools.execution import background
from localmcptools.persistence import artifacts, db
from localmcptools.process import manager, ports
from localmcptools.process.presets import Preset
from localmcptools.tools.process import process_get_status


@pytest.fixture
def lifecycle_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database = tmp_path / "audit.sqlite"
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "audit_db_path", lambda: database)
    db.init_db(database)
    yield database
    background.shutdown_runtime()


def _wait_until(predicate, timeout: float = 5.0):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("condition did not become true before timeout")


def test_start_log_port_status_and_stop(lifecycle_db: Path, tmp_path: Path) -> None:
    code = (
        "import socket,time; "
        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); "
        "print('http://127.0.0.1:%d' % s.getsockname()[1], flush=True); "
        "time.sleep(30)"
    )
    preset = Preset(
        "integration-python", (sys.executable,), (), (),
        __import__("re").compile(r"http://127\.0\.0\.1:(\d+)")
    )
    started = time.monotonic()
    item = background.start_dev_server(
        workspace_id="ws", cwd=str(tmp_path), preset=preset,
        argv=(sys.executable, "-u", "-c", code),
    )
    assert time.monotonic() - started < 1.0
    running = _wait_until(lambda: manager.find_by_id(item.id).port)
    refreshed = manager.find_by_id(item.id)
    assert refreshed.status == "running"
    assert artifacts.tail(item.log_handle, 1) == [f"http://127.0.0.1:{running}"]
    listener = _wait_until(lambda: ports.find_by_port(int(running)))
    assert listener.managed_id == item.id
    assert process_get_status({"id": item.id}).data["status"] == "running"
    stopped = background.stop(item.id, graceful=True)
    assert stopped.status == "exited"
    exited_status = process_get_status({"id": item.id}).data
    assert exited_status["status"] == "exited"
    assert exited_status["exit_code"] is not None
    _wait_until(lambda: not psutil.pid_exists(item.pid))
    assert artifacts.lookup(item.log_handle).sealed is True


def test_reconcile_marks_missing_pid_exited(lifecycle_db: Path) -> None:
    handle = artifacts.create_stream(call_id="orphan")
    item = manager.create_row(
        workspace_id="ws", preset="node-vite", command="npx vite", cwd="C:\\repo",
        pid=2_147_000_000, log_handle=handle,
    )
    assert background.reconcile_once() == 1
    assert manager.find_by_id(item.id).status == "exited"
    assert manager.find_by_id(item.id).exit_code is None
    assert artifacts.lookup(handle).sealed is True


@pytest.mark.parametrize("port", [8000, 8765])
def test_http_server_demo_end_to_end(
    lifecycle_db: Path, tmp_path: Path, port: int,
) -> None:
    if ports.find_by_port(port) is not None:
        pytest.skip(f"acceptance port {port} is already occupied by an external process")
    preset = Preset(
        "python-uvicorn", (sys.executable, "-m", "http.server"), (), (),
        __import__("re").compile(r"port (\d+)")
    )
    item = background.start_dev_server(
        workspace_id="ws", cwd=str(tmp_path), preset=preset,
        argv=(
            sys.executable, "-u", "-m", "http.server", str(port),
            "--bind", "127.0.0.1",
        ),
    )
    listener = _wait_until(lambda: ports.find_by_port(port))
    assert listener.managed_id == item.id
    assert background.stop(item.id, graceful=False).status == "exited"
    _wait_until(lambda: ports.find_by_port(port) is None)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_closing_job_object_kills_child_within_two_seconds(lifecycle_db: Path) -> None:
    import win32api

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    job = background._make_job()
    background._attach(child.pid, job)
    win32api.CloseHandle(job)
    child.wait(timeout=2)
    assert child.returncode is not None
