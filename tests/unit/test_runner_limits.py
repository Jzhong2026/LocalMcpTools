"""Runner output bounds and timeout-tree cleanup.

The cloud VM is Linux, so the Windows Job Object path is exercised with
mocks. The POSIX grandchild case runs for real.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import types
from pathlib import Path

import psutil
import pytest

from localmcptools.execution import runner


def _kill_pid_tree(pid: int) -> None:
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return
    victims = proc.children(recursive=True) + [proc]
    for victim in victims:
        try:
            victim.kill()
        except psutil.Error:
            pass


@pytest.mark.asyncio
async def test_long_line_without_newline_is_truncated_not_an_error(tmp_path: Path) -> None:
    code = "import sys; sys.stdout.buffer.write(b'A' * 200000); sys.stdout.buffer.flush()"
    result = await runner.run(
        [sys.executable, "-c", code], cwd=str(tmp_path), timeout_ms=10_000
    )
    assert result.exit_code == 0
    assert not result.timed_out
    assert "[output truncated]" in result.output
    assert len(result.output) < 100_000
    assert result.stdout_bytes >= 200_000


@pytest.mark.asyncio
async def test_large_output_is_capped_with_a_truncation_marker(tmp_path: Path) -> None:
    code = "import sys\nfor _ in range(4000):\n    sys.stdout.write('y' * 80 + '\\n')"
    result = await runner.run(
        [sys.executable, "-c", code], cwd=str(tmp_path), timeout_ms=10_000
    )
    assert result.exit_code == 0
    assert "[output truncated]" in result.output
    assert len(result.output) < 100_000
    assert result.stdout_bytes >= 300_000


@pytest.mark.asyncio
async def test_timeout_returns_when_grandchild_keeps_pipes_open(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX process-group kill is verified here; Windows uses a Job Object")
    pidfile = tmp_path / "pid"
    script = (
        "import os, time\n"
        "from pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "if os.fork() == 0:\n"
        "    time.sleep(120)\n"
        "    raise SystemExit(0)\n"
        "time.sleep(120)\n"
    )
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            runner.run(
                [sys.executable, "-c", script], cwd=str(tmp_path), timeout_ms=300
            ),
            timeout=8,
        )
    finally:
        if pidfile.is_file():
            _kill_pid_tree(int(pidfile.read_text().strip() or "0"))
    assert result.timed_out
    assert time.monotonic() - started < 8


def test_windows_job_object_enables_kill_on_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shell runner must use the same Job Object flag as managed processes."""
    monkeypatch.setattr(runner, "_is_windows", lambda: True)
    recorded: dict[str, object] = {}

    fake_job = types.ModuleType("win32job")
    fake_job.JobObjectExtendedLimitInformation = 9
    fake_job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    def create_job(_security, _name):  # type: ignore[no-untyped-def]
        return "job-handle"

    def query(_handle, _cls):  # type: ignore[no-untyped-def]
        return {"BasicLimitInformation": {"LimitFlags": 0}}

    def set_info(_handle, _cls, info):  # type: ignore[no-untyped-def]
        recorded["info"] = info

    fake_job.CreateJobObject = create_job  # type: ignore[attr-defined]
    fake_job.QueryInformationJobObject = query  # type: ignore[attr-defined]
    fake_job.SetInformationJobObject = set_info  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "win32job", fake_job)

    handle = runner._make_job()
    info = recorded["info"]
    assert handle == "job-handle"
    assert isinstance(info, dict)
    flags = info["BasicLimitInformation"]["LimitFlags"]
    assert flags & fake_job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE


@pytest.mark.asyncio
async def test_timeout_closes_the_job_handle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[object] = []
    monkeypatch.setattr(runner, "_make_job", lambda: "job-sentinel")
    monkeypatch.setattr(runner, "_attach_job", lambda pid, job: None)
    monkeypatch.setattr(runner, "_close_job", lambda job: closed.append(job))
    result = await runner.run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path),
        timeout_ms=200,
    )
    assert result.timed_out
    assert closed == ["job-sentinel"]
