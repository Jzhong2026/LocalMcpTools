"""Async subprocess runner shared by semantic presets and controlled shell."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .concurrency import ConcurrencyGate

OUTPUT_CAPTURE_LIMIT_BYTES = 64 * 1024
_READ_CHUNK = 8 * 1024
_DRAIN_TIMEOUT_S = 2.0
_TRUNCATION_MARKER = "\n[output truncated]\n"
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


@dataclass(frozen=True)
class RunResult:
    argv: tuple[str, ...]
    exit_code: int | None
    output: str
    stdout_bytes: int
    stderr_bytes: int
    timed_out: bool


class UseStartDevServer(ValueError):
    """A caller attempted long-running work through the bounded runner."""


class _Capture:
    """Bounded interleaved stdout/stderr text plus full byte counts."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.captured = 0
        self.truncated = False
        self.sizes = {"stdout": 0, "stderr": 0}

    def add(self, label: str, raw: bytes) -> None:
        self.sizes[label] += len(raw)
        if self.truncated or not raw:
            return
        piece = f"{label}: {raw.decode('utf-8', errors='replace')}"
        room = OUTPUT_CAPTURE_LIMIT_BYTES - self.captured
        if len(piece) > room:
            if room > 0:
                self.parts.append(piece[:room])
            self.parts.append(_TRUNCATION_MARKER)
            self.truncated = True
            self.captured = OUTPUT_CAPTURE_LIMIT_BYTES
            return
        self.parts.append(piece)
        self.captured += len(piece)

    def text(self) -> str:
        return "".join(self.parts)


class _Job:
    """One kill-on-close Job Object, closed at most once."""

    def __init__(self) -> None:
        self.handle: object | None = _make_job()

    def close(self) -> None:
        handle = self.handle
        if handle is None:
            return
        self.handle = None
        _close_job(handle)


def _is_windows() -> bool:
    return os.name == "nt"


def _make_job() -> object | None:
    """Create a kill-on-close Job Object, or None off Windows."""
    if not _is_windows():
        return None
    import win32job

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


def _attach_job(pid: int, job: object | None) -> None:
    """Assign ``pid`` to ``job``. No-op off Windows or when there is no job."""
    if not _is_windows() or job is None:
        return
    import win32api
    import win32con
    import win32job

    access = win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE
    process_handle = win32api.OpenProcess(access, False, pid)
    try:
        win32job.AssignProcessToJobObject(job, process_handle)
    finally:
        win32api.CloseHandle(process_handle)


def _close_job(job: object | None) -> None:
    """Close a Job Object handle. On Windows that kills the owned tree."""
    if not _is_windows() or job is None:
        return
    import win32api

    try:
        win32api.CloseHandle(job)
    except OSError:
        return


async def run(
    argv: Sequence[str], *, cwd: str, env: Mapping[str, str] | None = None,
    timeout_ms: int = 120_000, gate: ConcurrencyGate | None = None,
    reject_long_running: bool = False,
) -> RunResult:
    """Run an argv vector, interleave output, and return timeout as data."""
    if not argv:
        raise ValueError("argv must not be empty")
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")
    if reject_long_running and timeout_ms >= 60_000:
        raise UseStartDevServer(
            "timeout_ms >= 60000 must use process.start_dev_server"
        )
    active_gate = gate or ConcurrencyGate()
    job = _Job()
    async with active_gate.slot():
        try:
            spawn_kwargs: dict[str, object] = {}
            if _is_windows():
                spawn_kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP
            else:
                # Own process group so a timeout can signal the whole tree
                # without touching the server's group.
                spawn_kwargs["start_new_session"] = True
            process = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=dict(env) if env is not None else None,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                **spawn_kwargs,
            )
            assert process.stdout is not None and process.stderr is not None
            try:
                _attach_job(process.pid, job.handle)
            except OSError:
                job.close()
                process.kill()
                raise
            capture = _Capture()
            pumps = [
                asyncio.create_task(_pump(process.stdout, "stdout", capture)),
                asyncio.create_task(_pump(process.stderr, "stderr", capture)),
            ]
            timed_out = False
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout_ms / 1000)
            except TimeoutError:
                timed_out = True
                await _terminate_tree(process, job)
            await _bounded_drain(pumps, process, job)
            return RunResult(
                argv=tuple(argv), exit_code=None if timed_out else process.returncode,
                output=capture.text(), stdout_bytes=capture.sizes["stdout"],
                stderr_bytes=capture.sizes["stderr"], timed_out=timed_out,
            )
        finally:
            job.close()


async def _pump(stream: asyncio.StreamReader, label: str, capture: _Capture) -> None:
    """Read fixed chunks so a line longer than the stream limit cannot raise."""
    pending = bytearray()
    while True:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            break
        pending.extend(chunk)
        while True:
            newline = pending.find(b"\n")
            if newline < 0:
                break
            line = bytes(pending[: newline + 1])
            del pending[: newline + 1]
            capture.add(label, line)
        if len(pending) >= _READ_CHUNK:
            capture.add(label, bytes(pending))
            pending.clear()
    if pending:
        capture.add(label, bytes(pending))


async def _bounded_drain(
    pumps: list[asyncio.Task[None]],
    process: asyncio.subprocess.Process,
    job: _Job,
) -> None:
    """Finish readers, or cancel them so a held pipe cannot hang the tool."""
    pending = set(pumps)
    done, pending = await asyncio.wait(pending, timeout=_DRAIN_TIMEOUT_S)
    _consume_task_results(done)
    if not pending:
        return
    await _terminate_tree(process, job)
    done, pending = await asyncio.wait(pending, timeout=_DRAIN_TIMEOUT_S)
    _consume_task_results(done)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _consume_task_results(tasks: set[asyncio.Task[None]]) -> None:
    for task in tasks:
        if task.cancelled():
            continue
        task.exception()


async def _terminate_tree(process: asyncio.subprocess.Process, job: _Job) -> None:
    """Kill the process and every child that inherited its pipes."""
    job.close()
    if process.returncode is not None and process.pid is None:
        return
    if _is_windows():
        await _taskkill_tree(process)
    elif process.pid:
        _kill_posix_group(process.pid)
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            return


async def _taskkill_tree(process: asyncio.subprocess.Process) -> None:
    if process.pid is None:
        return
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        process.kill()
        return
    try:
        await asyncio.wait_for(killer.wait(), timeout=5)
    except TimeoutError:
        killer.kill()
        try:
            process.kill()
        except ProcessLookupError:
            return


def _kill_posix_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return


__all__ = ["OUTPUT_CAPTURE_LIMIT_BYTES", "RunResult", "UseStartDevServer", "run"]
