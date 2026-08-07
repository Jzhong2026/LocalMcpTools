"""Async subprocess runner shared by semantic presets and controlled shell."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .concurrency import ConcurrencyGate


@dataclass(frozen=True)
class RunResult:
    argv: tuple[str, ...]
    exit_code: int | None
    output: str
    stdout_bytes: int
    stderr_bytes: int
    timed_out: bool


async def run(
    argv: Sequence[str], *, cwd: str, env: Mapping[str, str] | None = None,
    timeout_ms: int = 120_000, gate: ConcurrencyGate | None = None,
) -> RunResult:
    """Run an argv vector, interleave output, and return timeout as data."""
    if not argv:
        raise ValueError("argv must not be empty")
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")
    active_gate = gate or ConcurrencyGate()
    async with active_gate.slot():
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=dict(env) if env is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        output: list[str] = []
        sizes = {"stdout": 0, "stderr": 0}

        async def pump(stream: asyncio.StreamReader, label: str) -> None:
            while chunk := await stream.readline():
                sizes[label] += len(chunk)
                output.append(f"{label}: {chunk.decode('utf-8', errors='replace')}")

        pumps = [asyncio.create_task(pump(process.stdout, "stdout")), asyncio.create_task(pump(process.stderr, "stderr"))]
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_ms / 1000)
        except TimeoutError:
            timed_out = True
            await _terminate_tree(process)
        finally:
            await asyncio.gather(*pumps)
        return RunResult(
            argv=tuple(argv), exit_code=None if timed_out else process.returncode,
            output="".join(output), stdout_bytes=sizes["stdout"], stderr_bytes=sizes["stderr"], timed_out=timed_out,
        )


async def _terminate_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F")
        await killer.wait()
    else:
        process.send_signal(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


__all__ = ["RunResult", "run"]
