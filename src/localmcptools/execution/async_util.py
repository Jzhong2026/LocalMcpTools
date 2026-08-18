"""Run an async coroutine to completion from synchronous tool code.

The MCP server (mcp == 1.29.0 / FastMCP) may invoke a *synchronous* tool
wrapper either from a worker thread (no running loop) or from inside its own
event loop (a running loop). ``asyncio.run`` fails in the latter case with
"asyncio.run() cannot be called from a running event loop", and calling
``run_until_complete`` on the active loop deadlocks because it waits on the
very stack currently blocking the loop.

The robust fix is to run the coroutine on a dedicated thread with its own
fresh event loop. Subprocess execution (``runner.run``) does not depend on
the server's loop, so this is semantically equivalent and always safe.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, cast


def run_async(coro: Any) -> Any:
    """Execute ``coro`` (a coroutine) and return its result.

    Safe to call from both synchronous tool bodies and from inside an
    already-running event loop: when a loop is active the coroutine is
    submitted to a separate thread where a fresh loop runs it.
    """
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    if in_loop:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return cast(Any, pool.submit(asyncio.run, coro).result())
    return asyncio.run(coro)


__all__ = ["run_async"]
