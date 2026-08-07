"""Bounded async execution with observable queue state."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock
from time import monotonic


class QueueTimeout(TimeoutError):
    """No execution slot became available before the queue deadline."""


@dataclass(frozen=True)
class GateStatus:
    active: int
    queue_depth: int


class ConcurrencyGate:
    def __init__(self, max_concurrent: int = 4, queue_timeout_ms: int = 600_000) -> None:
        if max_concurrent < 1 or queue_timeout_ms < 0:
            raise ValueError("max_concurrent must be positive and queue_timeout_ms non-negative")
        # A thread semaphore intentionally avoids event-loop affinity. Tool
        # wrappers may currently execute in FastMCP worker threads; one shared
        # gate must coordinate those calls as well as future native-async ones.
        self._sem = BoundedSemaphore(max_concurrent)
        self._queue_timeout_ms = queue_timeout_ms
        self._queue_depth = 0
        self._active = 0
        self._lock = Lock()

    @property
    def status(self) -> GateStatus:
        return GateStatus(active=self._active, queue_depth=self._queue_depth)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        deadline = monotonic() + self._queue_timeout_ms / 1000
        with self._lock:
            self._queue_depth += 1
        acquired = False
        try:
            try:
                acquired = await asyncio.to_thread(
                    self._sem.acquire, True, max(0, deadline - monotonic())
                )
                if not acquired:
                    raise TimeoutError
            except TimeoutError as exc:
                raise QueueTimeout("execution queue timeout") from exc
            with self._lock:
                self._queue_depth -= 1
                self._active += 1
            yield
        finally:
            if acquired:
                with self._lock:
                    self._active -= 1
                self._sem.release()
            else:
                with self._lock:
                    self._queue_depth -= 1


__all__ = ["ConcurrencyGate", "GateStatus", "QueueTimeout"]
