"""Lifecycle-bound background subprocesses with Windows Job Object ownership."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

import psutil

from ..config.settings import load_settings
from ..persistence import artifacts
from ..process import manager
from ..process.presets import Preset

if os.name == "nt":  # pragma: win32 cover
    import win32api
    import win32con
    import win32job


_lock = threading.RLock()
_processes: dict[str, subprocess.Popen[bytes]] = {}
_jobs: dict[str, Any] = {}
_stop_reconciler = threading.Event()
_reconciler: threading.Thread | None = None


def _make_job() -> Any:
    """Create a kill-on-close Job Object, or a no-op token off Windows."""
    if os.name != "nt":
        return None
    handle = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(
        handle, win32job.JobObjectExtendedLimitInformation
    )
    info["BasicLimitInformation"]["LimitFlags"] |= (
        win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    win32job.SetInformationJobObject(
        handle, win32job.JobObjectExtendedLimitInformation, info
    )
    return handle


def _attach(pid: int, job_handle: Any) -> None:
    """Assign a child PID to the owning Job Object."""
    if os.name != "nt" or job_handle is None:
        return
    access = win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE
    process_handle = win32api.OpenProcess(access, False, pid)
    try:
        win32job.AssignProcessToJobObject(job_handle, process_handle)
    finally:
        win32api.CloseHandle(process_handle)


def start_dev_server(
    *, workspace_id: str, cwd: str, preset: Preset, argv: Sequence[str],
    env: Mapping[str, str] | None = None,
) -> manager.ManagedProcess:
    """Spawn, bind, persist, and monitor one approved development server."""
    settings = load_settings().get("process", {})
    maximum = int(settings.get("managed_max_concurrent", 4))
    with _lock:
        live_count = sum(proc.poll() is None for proc in _processes.values())
        if live_count >= maximum:
            raise RuntimeError(f"managed process concurrency limit reached ({maximum})")

    stream_id = f"bg-{int(time.time() * 1000)}-{os.getpid()}"
    log_handle = artifacts.create_stream(call_id=stream_id)
    job = _make_job()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    command = subprocess.list2cmdline(list(argv))
    try:
        process = subprocess.Popen(
            list(argv), cwd=cwd, env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=creationflags, start_new_session=os.name != "nt",
        )
        _attach(process.pid, job)
    except Exception:
        _close_job(job)
        artifacts.seal(log_handle)
        raise

    item = manager.create_row(
        workspace_id=workspace_id, preset=preset.name, command=command, cwd=cwd,
        pid=process.pid, log_handle=log_handle,
    )
    with _lock:
        _processes[item.id] = process
        _jobs[item.id] = job
    threading.Thread(
        target=_monitor_process, args=(item, process, preset),
        name=f"lmcp-log-{item.id}", daemon=True,
    ).start()
    return item


def stop(process_id: str, *, graceful: bool = True, grace_seconds: float = 5.0) -> manager.ManagedProcess:
    """Stop only a process registered and owned by this server."""
    item = manager.find_by_id(process_id)
    if item.status != "running":
        return item
    with _lock:
        process = _processes.get(process_id)
    if os.name == "nt":
        command = ["taskkill", "/PID", str(item.pid), "/T"]
        if not graceful:
            command.append("/F")
        subprocess.run(command, check=False, capture_output=True, timeout=max(grace_seconds, 1))
        if graceful and _pid_alive(item.pid):
            deadline = time.monotonic() + grace_seconds
            while time.monotonic() < deadline and _pid_alive(item.pid):
                time.sleep(0.05)
            if _pid_alive(item.pid):
                subprocess.run(
                    ["taskkill", "/PID", str(item.pid), "/T", "/F"],
                    check=False, capture_output=True, timeout=max(grace_seconds, 1),
                )
    elif process is not None:
        process.terminate() if graceful else process.kill()
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
    if process is not None and process.poll() is None:
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass
    if process is None:
        manager.mark_exited(process_id, None)
        _safe_seal(item.log_handle)
    else:
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            refreshed = manager.find_by_id(process_id)
            if refreshed.status == "exited":
                return refreshed
            time.sleep(0.01)
    return manager.find_by_id(process_id)


def reconcile_once() -> int:
    """Mark database rows whose OS process vanished as exited."""
    reconciled = 0
    for item in manager.all_running():
        if not _pid_alive(item.pid):
            manager.mark_exited(item.id, None)
            _safe_seal(item.log_handle)
            reconciled += 1
    return reconciled


def start_runtime() -> None:
    """Reconcile old rows and start the periodic lifecycle monitor."""
    global _reconciler
    reconcile_once()
    with _lock:
        if _reconciler is not None and _reconciler.is_alive():
            return
        _stop_reconciler.clear()
        _reconciler = threading.Thread(
            target=_reconcile_loop, name="lmcp-reconciler", daemon=True
        )
        _reconciler.start()


def shutdown_runtime() -> None:
    """Close Job handles, which kills every owned process tree on Windows."""
    global _reconciler
    _stop_reconciler.set()
    if _reconciler is not None:
        _reconciler.join(timeout=1)
        _reconciler = None
    with _lock:
        jobs = list(_jobs.items())
        processes = list(_processes.items())
        _jobs.clear()
    for _process_id, job in jobs:
        _close_job(job)
    if os.name != "nt":
        for _process_id, process in processes:
            if process.poll() is None:
                process.terminate()


def _monitor_process(
    item: manager.ManagedProcess, process: subprocess.Popen[bytes], preset: Preset,
) -> None:
    assert process.stdout is not None
    port_seen = False
    for raw in iter(process.stdout.readline, b""):
        artifacts.append(item.log_handle, raw)
        if not port_seen:
            line = raw.decode("utf-8", errors="replace")
            match = preset.port_hint_regex.search(line)
            if match is not None:
                port = int(match.group(1))
                if 1 <= port <= 65535:
                    manager.set_port(item.id, port)
                    port_seen = True
    exit_code = process.wait()
    _safe_seal(item.log_handle)
    manager.mark_exited(item.id, exit_code)
    with _lock:
        _processes.pop(item.id, None)
        job = _jobs.pop(item.id, None)
    _close_job(job)


def _reconcile_loop() -> None:
    settings = load_settings().get("process", {})
    interval = max(float(settings.get("reconcile_interval_seconds", 30)), 0.1)
    while not _stop_reconciler.wait(interval):
        reconcile_once()


def _pid_alive(pid: int) -> bool:
    if not psutil.pid_exists(pid):
        return False
    try:
        return bool(psutil.Process(pid).status() != psutil.STATUS_ZOMBIE)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _safe_seal(handle: str) -> None:
    try:
        artifacts.seal(handle)
    except (artifacts.ArtifactNotFound, artifacts.RedactionFailed, OSError):
        pass


def _close_job(job: Any) -> None:
    if os.name == "nt" and job is not None:
        try:
            win32api.CloseHandle(job)
        except Exception:  # noqa: BLE001 - shutdown must be best-effort
            pass


__all__ = ["_attach", "_make_job", "reconcile_once", "shutdown_runtime", "start_dev_server", "start_runtime", "stop"]
