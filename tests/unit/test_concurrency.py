"""Concurrency gate and execution runner tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from localmcptools.execution.concurrency import ConcurrencyGate, QueueTimeout
from localmcptools.execution.powershell import build_powershell_args
from localmcptools.execution.runner import UseStartDevServer, run


@pytest.mark.asyncio
async def test_queue_timeout_when_all_slots_are_busy() -> None:
    gate = ConcurrencyGate(max_concurrent=1, queue_timeout_ms=10)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with gate.slot():
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    with pytest.raises(QueueTimeout):
        async with gate.slot():
            pass
    release.set()
    await task
    assert gate.status.active == 0
    assert gate.status.queue_depth == 0


@pytest.mark.asyncio
async def test_runner_interleaves_output_and_reports_exit_code(tmp_path: Path) -> None:
    result = await run(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        cwd=str(tmp_path),
    )
    assert result.exit_code == 0
    assert not result.timed_out
    assert "stdout: out" in result.output
    assert "stderr: err" in result.output


@pytest.mark.asyncio
async def test_runner_returns_timeout_as_data(tmp_path: Path) -> None:
    result = await run([sys.executable, "-c", "import time; time.sleep(2)"], cwd=str(tmp_path), timeout_ms=10)
    assert result.timed_out
    assert result.exit_code is None


@pytest.mark.asyncio
async def test_runner_rejects_long_running_shell_mode(tmp_path: Path) -> None:
    with pytest.raises(UseStartDevServer):
        await run(
            [sys.executable, "-c", "print('never')"], cwd=str(tmp_path),
            timeout_ms=60_000, reject_long_running=True,
        )


def test_powershell_builder_forces_noninteractive_utf8() -> None:
    args = build_powershell_args("Write-Host hi")
    assert args[:4] == ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    assert "OutputEncoding" in args[-1]
